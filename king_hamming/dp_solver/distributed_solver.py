#!/usr/bin/env python3
"""Queue worker for bounded C tiles and compact reconstruction from replicated artifacts."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import argparse
from array import array
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from urllib.error import HTTPError

from agent import request_json
from blob_store import fetch_blob, file_digest, storage_transaction, store_blob
from dependency_cache import checkout
import gpus
from dp_solver import tile_codec, tile_scratch
from dp_solver.bands import read_band, write_band
from dp_solver.scheduling import dp_estimate
from dp_solver.tiles import band_kind, band_region, build_halo, needed_bands, tile

ROOT = Path(__file__).resolve().parent
GPU_TILE = ROOT.parent / "gpu_dp_solver" / "kh_gpu_dp_tile"
STOP = False
REPORTER = None


# Keep dependency-transfer liveness and byte progress independent of blocking peer reads.
class transfer_reporter_t:
    """Own one short-lived reporter until native computation starts or reconstruction ends."""

    def __init__(self):
        """Start reporting with unknown-size zero progress before input descriptors arrive."""

        self.done=0
        self.base=0
        self.total=0
        self.stopped=threading.Event()
        self.thread=threading.Thread(target=self.report,daemon=True)
        self.thread.start()

    def report(self):
        """Emit genuine helper liveness and monotone transfer-byte counters once a second."""

        while not self.stopped.wait(1):
            done=min(self.done,self.total)
            print(json.dumps({"done":done,"total":self.total,"checkpoint_done":0,"units":"bytes","phase":"fetching","heartbeat":True}),flush=True)

    def close(self):
        """Stop before another producer reports cell progress through the same stdout stream."""

        self.stopped.set()
        self.thread.join(timeout=2)


# Let the ordinary agent stop an active tile without treating partial output as durable.
def stop(signum, frame) -> None:
    """Latch intentional termination; the running C child is terminated by its polling supervisor."""

    global STOP
    STOP = True


def leader_request(arguments, route: str, body: dict) -> dict:
    """Wait out transient leader congestion while the agent independently renews the lease."""

    delay = 0.5
    while not STOP:
        try:
            return request_json(arguments.leader, route, body)
        except HTTPError as error:
            if error.code == 409:
                raise InterruptedError("lease was reassigned") from error
            if error.code < 500:
                raise
        except OSError:
            pass
        deadline = time.monotonic() + delay
        while not STOP and time.monotonic() < deadline:
            time.sleep(min(0.2, deadline - time.monotonic()))
        delay = min(8.0, delay * 2)
    raise InterruptedError("leader request stopped")


# A format-2 packet names its field, rectangle and native layout in its header.
def packet_identity(p: int, r: int, rectangle) -> dict:
    """Return the header fields a format-2 packet for rectangle must carry."""

    return {"format":tile_codec.PACKET_FORMAT,"p":p,"r":r,"first_u":rectangle.first_u,"last_u":rectangle.last_u,
            "first_v":rectangle.first_v,"last_v":rectangle.last_v,"byteorder":sys.byteorder,"value_bytes":8,"choice_bytes":4}


# Open and verify only the three expected bounded regular files from a hashed tile packet.
def unpack(packet: Path, directory: Path, rectangle, p: int, r: int) -> None:
    """Extract packet into private directory, validating sizes, identity and native layout."""

    if tile_codec.is_xz(packet):
        tile_codec.read_packet(packet,directory,rectangle.last_u-rectangle.first_u+1,rectangle.last_v-rectangle.first_v+1,
                               packet_identity(p,r,rectangle),stopped)
        check_tile_metadata(directory,rectangle,p,r)
        return
    directory.mkdir()
    expected = {"values.bin":rectangle.value_bytes,"choices.bin":rectangle.value_bytes//2,"tile.json":None}
    seen=set()
    with tarfile.open(packet,"r|*") as archive:
        for member in archive:
            if member.name not in expected or member.name in seen or not member.isfile() or (expected[member.name] is not None and member.size!=expected[member.name]) or (member.name=="tile.json" and member.size>4096):
                raise ValueError("invalid tile artifact member size/type")
            seen.add(member.name)
            source=archive.extractfile(member)
            with source, (directory/member.name).open("xb") as output:
                while block:=source.read(1024*1024):
                    if STOP:
                        raise InterruptedError("tile transfer stopped")
                    output.write(block)
    if seen!=set(expected):
        raise ValueError("unexpected tile artifact members")
    check_tile_metadata(directory,rectangle,p,r)


def check_tile_metadata(directory: Path, rectangle, p: int, r: int) -> None:
    """Refuse unpacked kernel metadata naming another field, rectangle or native layout."""

    metadata=json.loads((directory/"tile.json").read_text())
    required={"format":"KH-DP-TILE-1","p":p,"r":r,"first_u":rectangle.first_u,"last_u":rectangle.last_u,
              "first_v":rectangle.first_v,"last_v":rectangle.last_v,"byteorder":sys.byteorder,"value_bytes":8,"choice_bytes":4}
    if any(metadata.get(name)!=value for name,value in required.items()):
        raise ValueError("tile artifact identity or layout mismatch")


# Request only the immutable dependency descriptions belonging to the current lease.
def descriptions(arguments, **extra) -> list[dict]:
    """Return paginated input descriptors while enforcing current ownership at the leader."""

    records=[]
    offset=0
    while True:
        if STOP:
            raise InterruptedError("dependency request stopped")
        response=leader_request(arguments,"/v1/tile-input",{"run_id":arguments.run_id,"lease_token":arguments.lease_token,"offset":offset,**extra})
        records.extend(response["records"])
        if response["next"] is None:
            return records
        offset=response["next"]


# Let long local copies notice an intentional stop between blocks.
def stopped() -> None:
    """Raise InterruptedError once the agent has asked this tile to stop."""

    if STOP:
        raise InterruptedError("tile stopped")


# Bands are an optimization with a rollback switch: KH_DP_BANDS=0 neither publishes nor uses them.
def bands_enabled() -> bool:
    """Return whether this worker publishes and consumes edge bands."""

    return os.environ.get("KH_DP_BANDS", "1") != "0"


# Resolve one hashed blob from this node's disk, the shared cache or live peers.
def fetch(record: dict, arguments, cache: Path) -> Path:
    """Fetch and hash-check the blob a descriptor names, fencing long transfers; return its local path."""

    last_check=0.0
    spacing=2.0
    if REPORTER is not None:
        REPORTER.total+=record["size"]

    def check() -> None:
        """Check cancellation and periodically fence long transfers against lease loss."""

        nonlocal last_check, spacing
        if REPORTER is not None:
            download_root = (arguments.shared_cache_root if arguments.shared_cache_root
                             else cache/"blobs")
            partial=download_root/".downloads"/(record["sha256"]+".part")
            if partial.exists():
                REPORTER.done=min(REPORTER.total,REPORTER.base+partial.stat().st_size)
        if STOP:
            raise InterruptedError("tile transfer stopped")
        if time.monotonic()-last_check>=spacing:
            response=leader_request(arguments,"/v1/run-control",{"run_id":arguments.run_id,"lease_token":arguments.lease_token})
            last_check=time.monotonic()
            # Each check is a leader write that also renews the lease; a sixth of the lease
            # keeps it safe while sparing the leader a request every two seconds per fetch.
            spacing=min(5.0,max(1.0,float(response.get("lease_seconds",12))/6))
            if response["stop_requested"]:
                raise InterruptedError("campaign stopped")

    if arguments.shared_cache_root:
        packet=checkout(arguments.shared_cache_root,cache/"packets",
                        record["sha256"],record["size"],record["locations"],
                        arguments.local_storage_root,arguments.local_storage_url,check)
    else:
        packet=fetch_blob(cache/"blobs",record["sha256"],record["size"],record["locations"],check)
    if REPORTER is not None:
        REPORTER.base+=record["size"]
        REPORTER.done=REPORTER.base
    return packet


# Resolve a complete artifact directly from its live peer HTTP sources.
def acquire(record: dict, arguments, p: int, r: int, side: int, cache: Path) -> Path:
    """Fetch and validate one tile packet in bounded buffers; return its private extracted directory."""

    rectangle=tile(p,r,side,record["row"],record["column"])
    maximum=(rectangle.value_bytes+rectangle.value_bytes//2)*101//100+16384
    if type(record["size"]) is not int or not 0<record["size"]<=maximum:
        raise ValueError("tile packet exceeds expected byte size")
    packet=fetch(record,arguments,cache)
    directory=cache/record["sha256"]
    if not directory.exists():
        unpack(packet,directory,rectangle,p,r)
    return directory


# Fetch just the edge band of a predecessor that the target's halo can reach.
def acquire_band(band: dict, arguments, p: int, r: int, predecessor, kind: str, cache: Path):
    """Fetch, hash-check and identity-check one band; return the piece of values it holds."""

    region=band_region(p,predecessor,kind)
    maximum=region.value_bytes*101//100+16384
    if type(band["size"]) is not int or not 0<band["size"]<=maximum:
        raise ValueError("band exceeds expected byte size")
    blob=fetch(band,arguments,cache)
    return read_band(blob,cache/("band-"+band["sha256"]),p,r,predecessor,kind,stopped)


# Prefer the small band; any problem with it falls back to the whole packet, which is always listed.
def acquire_input(record: dict, arguments, p: int, r: int, side: int, target, cache: Path):
    """Return (values source, "band" or "packet", bytes fetched) for one predecessor of target."""

    band=record.get("band")
    if band is not None and bands_enabled():
        predecessor=tile(p,r,side,record["row"],record["column"])
        kind=band_kind(target,predecessor)
        if band.get("kind")==kind:
            before=(REPORTER.total,REPORTER.base) if REPORTER is not None else None
            try:
                return acquire_band(band,arguments,p,r,predecessor,kind,cache),"band",band["size"]
            except InterruptedError:
                raise
            except (ValueError,OSError,KeyError) as error:
                print(f"{kind} band of tile {record['row']},{record['column']} unusable, fetching the whole packet: {error}",
                      file=sys.stderr,flush=True)
                shutil.rmtree(cache/("band-"+band["sha256"]),ignore_errors=True)
                if REPORTER is not None:
                    REPORTER.total,REPORTER.base=before
    directory=acquire(record,arguments,p,r,side,cache)
    return directory/"values.bin","packet",record["size"]


# Cut the bands from the finished tile and index them, so successors can skip the whole packet.
def publish_bands(arguments, p: int, r: int, side: int, rectangle, values: Path, packet: Path,
                  tile_format: int = 1) -> int:
    """Store this tile's needed bands in local storage and register them; return how many were registered.

    Failures are never fatal: the tile's own packet is complete, and a successor
    without bands just downloads packets as before.
    """

    if not (bands_enabled() and arguments.local_storage_root and arguments.local_storage_url):
        return 0
    kinds=needed_bands(p,r,side,rectangle)
    if not kinds:
        return 0
    work=packet.parent/"bands"
    stored={}
    try:
        work.mkdir()
        entries={}
        with storage_transaction(arguments.local_storage_root,stopped):
            for kind in kinds:
                blob=work/(kind+".khband")
                write_band(values,p,r,rectangle,kind,blob,stopped,tile_format)
                digest,path=store_blob(blob,arguments.local_storage_root,stopped)
                stored[kind]=path
                entries[kind]={"sha256":digest,"size":path.stat().st_size,
                               "location":f"{arguments.local_storage_url.rstrip('/')}/blobs/{digest}"}
        response=request_json(arguments.leader,"/v1/tile-input",
                              {"run_id":arguments.run_id,"lease_token":arguments.lease_token,
                               "publish_bands":{"packet":file_digest(packet),"bands":entries}})
        if response.get("registered")!=len(entries):
            raise ValueError("leader did not register the bands")
        return len(entries)
    except InterruptedError:
        raise
    except Exception as error:
        print(f"tile bands not published: {error}",file=sys.stderr,flush=True)
        return 0
    finally:
        shutil.rmtree(work,ignore_errors=True)


# The GPU kernel exits 3 both when the device is missing and when a tile does not fit in its
# memory, and exit 3 marks this host's GPU unavailable for every tile for ten minutes. So a tile
# too big for the card goes straight to the CPU. The agent exports the cards it detected
# (KH_GPU_DEVICES); without that, assume the fleet's smallest, a P600 (1.7 GiB usable).
def gpu_fits(p: int, rectangle, device: int = 0) -> bool:
    """Return whether rectangle's halo, choices and transitions fit the device's usable memory."""

    limit=1536*1024**2
    try:
        for item in json.loads(os.environ.get("KH_GPU_DEVICES","[]")):
            if item.get("index")==device:
                limit=gpus.usable_bytes(item)
    except (ValueError,TypeError,KeyError,AttributeError):
        pass
    limit=int(os.environ.get("KH_GPU_DP_MAX_BYTES",limit))
    return rectangle.halo_bytes+rectangle.value_bytes//2+p**3*24+32*1024**2<=limit


# CPU seconds per cell, per transition (p^3 of them) and per thread, measured on live tiles:
# 23^7 604 s, 29^7 1378 s, 31^7 1632 s, 7^13 16 s on two threads (5.6e-9 to 6.7e-9).
CPU_SECONDS_PER_VISIT = 6.5e-9
MIN_GPU_WAIT_SECONDS = 1.0
MAX_GPU_WAIT_SECONDS = 900.0


# A fixed 120 s wait suited neither end: a heavy tile that gave up then spent up to 25 minutes on
# the CPU (30x slower than the GPU it had nearly reached), while a light tile idled for two
# minutes for a GPU that its own CPUs would beat. Waiting is worth it only while it costs less
# than the CPU run it avoids, so wait up to half the CPU estimate, within sane bounds.
def gpu_wait_seconds(p: int, rectangle, threads: int) -> float:
    """Return how long this tile should wait for the GPU before computing on its CPUs."""

    configured=os.environ.get("KH_GPU_DP_WAIT_SECONDS")
    if configured:
        return float(configured)
    cpu_seconds=(rectangle.value_bytes//8)*p**3*CPU_SECONDS_PER_VISIT/max(1,threads)
    return min(MAX_GPU_WAIT_SECONDS,max(MIN_GPU_WAIT_SECONDS,cpu_seconds/2))


# While the GPU serves one tile, the host's other CPUs sit idle: each slot's two CPUs belong to a
# tile that is only waiting for the GPU. CPU assist lets one tile per host that finds the GPU busy
# compute on the node's CPUs instead, at idle priority, so GPU and CPUs work on different tiles at
# once (+6-15% on heavy fields, more on light ones). Racing the two on one tile gains nothing: the
# GPU wait is seconds, the CPU run minutes.
ASSIST_MAX_THREADS = 8
ASSIST_MAX_SECONDS = 1200.0
# Light tiles are better served by the ordinary rule (wait briefly for the GPU, else compute on the
# leased CPUs): measured live, an idle-priority eight-thread run of a 16 s tile took 80 s, starved
# by those ordinary CPU runs and slowed by waiting on its own barriers, and held its slot ten times
# longer than the GPU would have. Assist only tiles whose own CPUs would take longer than this.
ASSIST_MIN_SECONDS = 120.0


def assist_plan(p: int, rectangle, lease_cpus: list[int]) -> list[int] | None:
    """Return the CPUs a CPU-assist run would use, or None when assisting is off or not worth it.

    Assist is opt-in (KH_CPU_ASSIST=1): in a live 5-minute window one tile of 353 used it."""

    if os.environ.get("KH_CPU_ASSIST","0")!="1":      # off unless KH_CPU_ASSIST=1: it has not shown a measurable gain
        return None
    try:
        node=sorted({int(item) for item in os.environ.get("KH_NODE_CPUS","").split(",") if item})
    except ValueError:
        return None
    # The lease's own CPUs first, so the run is never narrower than an ordinary CPU fallback.
    wide=(lease_cpus+[cpu for cpu in node if cpu not in lease_cpus])[:ASSIST_MAX_THREADS]
    if len(wide)<=len(lease_cpus):
        return None
    work=(rectangle.value_bytes//8)*p**3*CPU_SECONDS_PER_VISIT
    minimum=float(os.environ.get("KH_CPU_ASSIST_MIN_SECONDS",ASSIST_MIN_SECONDS))
    return wide if work/len(lease_cpus)>minimum and work/len(wide)<=ASSIST_MAX_SECONDS else None


# Compute an immutable tile from peer artifacts, leaving whole-calculation state on no worker.
def compute(arguments, specification: dict) -> None:
    """Compute one tile with its halo and kernel output in RAM scratch when it fits, else on disk."""

    options=specification["arguments"]
    rectangle=tile(options["p"],options["r"],options["tile_side"],options["row"],options["column"])
    # The halo plus the kernel's values and choices; staging and tile.json fit in the slack.
    scratch=tile_scratch.claim(rectangle.halo_bytes+rectangle.value_bytes*3//2+16*1024**2)
    try:
        compute_in(arguments,specification,scratch or arguments.output.parent,"ram" if scratch else "disk")
    finally:
        tile_scratch.release(scratch)


def compute_in(arguments, specification: dict, work: Path, scratch: str) -> None:
    """Assemble one admitted halo in work, invoke the exact pinned C kernel and pack its complete output.

    Inputs and the packet stay beside arguments.output: inputs are small bands except in the
    rare whole-packet fallback, and the agent publishes the packet after this process exits.
    """

    options=specification["arguments"]
    p,r,side=options["p"],options["r"],options["tile_side"]
    rectangle=tile(p,r,side,options["row"],options["column"])
    cache=arguments.output.parent/"tile-inputs"
    cache.mkdir()
    sources={}
    fetched={"band":0,"packet":0}
    counts={"band":0,"packet":0}
    # Seconds per phase, reported with the result so slow tiles can be explained.
    timings={}
    mark=time.monotonic()

    def lap(name):
        nonlocal mark
        now=time.monotonic()
        timings[name+"_seconds"]=round(timings.get(name+"_seconds",0)+now-mark,3)
        mark=now

    for record in descriptions(arguments):
        source,mode,size=acquire_input(record,arguments,p,r,side,rectangle,cache)
        sources[(record["row"],record["column"])]=source
        fetched[mode]+=size
        counts[mode]+=1
    lap("fetch")
    halo=work/"halo.bin"
    build_halo(p,r,side,rectangle,sources,halo,int(options.get("max_tile_bytes",2*1024**3)))
    shutil.rmtree(cache)
    lap("halo")
    if REPORTER is not None:
        REPORTER.close()
    output=work/"tile-output"
    cpus=sorted(os.sched_getaffinity(0))
    threads=min(int(options.get("threads",1)),len(cpus))

    def kernel(binary,on=None,team=None,idle=False):
        """Run one tile binary on the lease's CPUs (or on, with team threads); return its exit status."""
        on=cpus if on is None else on
        tail=[str(p),str(r),str(rectangle.first_u),str(rectangle.last_u),str(rectangle.first_v),str(rectangle.last_v),
              str(halo),str(output),str(threads if team is None else team),str(options.get("max_tile_bytes",2*1024**3))]
        command=[sys.executable,str(ROOT.parent/"cluster"/"affinity_exec.py"),"--parent-pid",str(os.getpid()),
                 "--cpus",",".join(map(str,on)),"--",str(binary),*tail]
        if idle:
            command=["nice","-n","19",*command]
        process=subprocess.Popen(command)
        try:
            while process.poll() is None:
                if STOP:
                    process.terminate()
                    raise InterruptedError("tile stopped")
                time.sleep(0.1)
            return process.returncode
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    # The GPU kernel's output is byte-identical to kh_dp_tile, so it is an opportunistic
    # accelerator: wait briefly for this host's GPU, otherwise compute on the leased CPUs.
    computed=False
    assisted=0
    limit=0.0
    device=int(os.environ.get("KH_GPU_DEVICE","0"))
    if (GPU_TILE.exists() and os.environ.get("KH_DISABLE_GPU_DP")!="1" and gpu_fits(p,rectangle,device) and
            not gpus.recently_unavailable(device)):
        lock=gpus.DeviceLock(device)
        limit=gpu_wait_seconds(p,rectangle,threads)
        acquired=lock.acquire(0.0,lambda: STOP,skip_long=True)
        wide=None if acquired else assist_plan(p,rectangle,cpus)
        if wide is not None:
            slot=gpus.AssistSlot(int(os.environ.get("KH_CPU_ASSIST_SLOTS","1")))
            if slot.acquire():
                assisted=len(wide)
                lap("gpu_wait")
                try:
                    code=kernel(ROOT.parent/"dp_solver"/"kh_dp_tile",on=wide,team=len(wide),idle=True)
                finally:
                    slot.release()
                    lap("kernel")
                if code!=0:
                    raise RuntimeError(f"C tile kernel exited {code}")
                computed=True
        if not computed and not acquired:
            acquired=lock.acquire(limit,lambda: STOP,skip_long=True)
        lap("gpu_wait")
        if acquired:
            try:
                code=kernel(GPU_TILE)
            finally:
                lock.release()
                lap("kernel")
            computed=code==0
            if code==3:
                gpus.mark_unavailable(device)
            if not computed and output.exists():
                shutil.rmtree(output)
        if STOP:
            raise InterruptedError("tile stopped")
    if not computed:
        code=kernel(ROOT.parent/"dp_solver"/"kh_dp_tile")
        lap("kernel")
        if code!=0:
            raise RuntimeError(f"C tile kernel exited {code}")
    tile_format=int(options.get("tile_format",1))
    if tile_format==2:
        tile_codec.write_packet(output,rectangle.last_u-rectangle.first_u+1,rectangle.last_v-rectangle.first_v+1,
                                packet_identity(p,r,rectangle),arguments.output,stopped)
        with arguments.output.open("rb") as stream:
            os.fsync(stream.fileno())
    else:
        with arguments.output.open("xb") as stream:
            with tarfile.open(fileobj=stream,mode="w|gz",format=tarfile.USTAR_FORMAT,compresslevel=1) as archive:
                for name in ("values.bin","choices.bin","tile.json"):
                    archive.add(output/name,arcname=name,recursive=False)
            stream.flush()
            os.fsync(stream.fileno())
    lap("pack")
    published=publish_bands(arguments,p,r,side,rectangle,output/"values.bin",arguments.output,tile_format)
    lap("publish")
    cells=rectangle.value_bytes//8
    mode="none" if not counts["band"]+counts["packet"] else "bands" if not counts["packet"] else "packets" if not counts["band"] else "mixed"
    print(json.dumps({"done":cells,"total":cells,"checkpoint_done":cells,"units":"cells","phase":"complete","heartbeat":True,
                      "engine":"cpu-assist" if assisted else "gpu" if computed else "cpu","assist_threads":assisted,
                      "gpu_wait_limit":round(limit,1),"input_mode":mode,"scratch":scratch,
                      "input_bytes":fetched["band"]+fetched["packet"],"input_band_bytes":fetched["band"],
                      "bands_published":published,**timings}),flush=True)


# Reconstruct through on-demand native choice shards without downloading a whole field table.
def reconstruct(arguments, specification: dict) -> None:
    """Write the exact run-length DP artifact after all tile dependencies are durably indexed."""

    options=specification["arguments"]
    p,r,side=options["p"],options["r"],int(options.get("tile_side",4096))
    estimate=dp_estimate(specification)
    budget=estimate["budget"]
    u,v=budget,budget
    active=None
    directory=None
    cache=arguments.output.parent/"reconstruction"
    cache.mkdir()
    theta=None
    runs=[]
    while u and v:
        if STOP:
            raise InterruptedError("reconstruction stopped")
        row,column=(u-1)//side,(v-1)//side
        rectangle=tile(p,r,side,row,column)
        if active!=(row,column):
            shutil.rmtree(cache)
            cache.mkdir()
            record=descriptions(arguments,row=row,column=column)[0]
            directory=acquire(record,arguments,p,r,side,cache)
            active=(row,column)
        offset=(u-rectangle.first_u)*(rectangle.last_v-rectangle.first_v+1)+v-rectangle.first_v
        if theta is None:
            with (directory/"values.bin").open("rb") as source:
                source.seek(offset*8)
                value=array("Q")
                value.frombytes(source.read(8))
                theta=value[0]
        with (directory/"choices.bin").open("rb") as source:
            source.seek(offset*4)
            value=array("I")
            value.frombytes(source.read(4))
            choice=value[0]
        if choice==0:
            break
        if choice>p**3:
            raise ValueError("invalid reconstruction choice")
        index=choice-1
        t=index%p+1
        index//=p
        b=index%p+1
        a=index//p+1
        if a*t>u or b*t>v:
            raise ValueError("invalid reconstruction cost")
        if runs and (runs[-1]["a"],runs[-1]["b"],runs[-1]["t"])==(a,b,t):
            runs[-1]["repeat"]+=1
        else:
            runs.append({"a":a,"b":b,"t":t,"repeat":1})
        u-=a*t
        v-=b*t
    document={"format":"KHDP2-draft","p":p,"r":r,"q":estimate["q"],"f":p**(r//2),"budget":budget,"theta":theta or 0,"runs":runs}
    if options.get("artifact_format", "JSON") == "KHD1":
        sys.path.insert(0, str(ROOT.parent/"dp_solver"))
        from artifacts import encode_dp
        with arguments.output.open("xb") as output:
            output.write(encode_dp(document))
            output.flush()
            os.fsync(output.fileno())
    else:
        with arguments.output.open("x") as output:
            json.dump(document,output,indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    if REPORTER is not None:
        REPORTER.close()
    print(json.dumps({"done":budget**2,"total":budget**2,"checkpoint_done":budget**2,"units":"cells","phase":"complete","heartbeat":True}),flush=True)


# Internal worker entry remains manually runnable and useful with no arguments.
def main() -> int:
    """Run one leased task from a specification file; intentional stops exit 75."""

    global REPORTER
    parser=argparse.ArgumentParser(description="Execute a leased immutable DP tile or reconstruct its parent split.",
        epilog="Example: ./distributed_solver.py --specification task.json --leader http://127.0.0.1:8765 --run-id RUN --lease-token TOKEN --output result.bin")
    parser.add_argument("--specification",type=Path)
    parser.add_argument("--leader")
    parser.add_argument("--run-id")
    parser.add_argument("--lease-token")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--local-storage-root",type=Path)
    parser.add_argument("--local-storage-url")
    parser.add_argument("--shared-cache-root",type=Path)
    parser.add_argument("--checkpoint-handshake",action="store_true",help="agent protocol compatibility; completed tiles are immutable checkpoints")
    arguments=parser.parse_args()
    if arguments.specification is None:
        parser.print_help()
        return 0
    if not all((arguments.leader,arguments.run_id,arguments.lease_token,arguments.output)):
        parser.error("specification, leader, lease identity and output are required")
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    specification=json.loads(arguments.specification.read_text())
    REPORTER=transfer_reporter_t()
    try:
        if specification["program"]=="dp_tile":
            compute(arguments,specification)
        elif specification["program"]=="dp_distributed":
            reconstruct(arguments,specification)
        else:
            raise ValueError("unsupported distributed solver program")
    except InterruptedError:
        return 75
    finally:
        REPORTER.close()
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"distributed_solver.py: {error}",file=sys.stderr)
        raise SystemExit(1)
