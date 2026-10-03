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

from agent import request_json
from blob_store import fetch_blob, file_digest, storage_transaction, store_blob
from dependency_cache import checkout
import gpus
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


# Open and verify only the three expected bounded regular files from a hashed tile packet.
def unpack(packet: Path, directory: Path, rectangle, p: int, r: int) -> None:
    """Extract packet into private directory, validating sizes, identity and native layout."""

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
        response=request_json(arguments.leader,"/v1/tile-input",{"run_id":arguments.run_id,"lease_token":arguments.lease_token,"offset":offset,**extra})
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
    if REPORTER is not None:
        REPORTER.total+=record["size"]

    def check() -> None:
        """Check cancellation and periodically fence long transfers against lease loss."""

        nonlocal last_check
        if REPORTER is not None:
            download_root = (arguments.shared_cache_root if arguments.shared_cache_root
                             else cache/"blobs")
            partial=download_root/".downloads"/(record["sha256"]+".part")
            if partial.exists():
                REPORTER.done=min(REPORTER.total,REPORTER.base+partial.stat().st_size)
        if STOP:
            raise InterruptedError("tile transfer stopped")
        if time.monotonic()-last_check>=2:
            response=request_json(arguments.leader,"/v1/run-control",{"run_id":arguments.run_id,"lease_token":arguments.lease_token})
            last_check=time.monotonic()
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
def publish_bands(arguments, p: int, r: int, side: int, rectangle, values: Path, packet: Path) -> int:
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
                write_band(values,p,r,rectangle,kind,blob,stopped)
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


# Compute an immutable tile from peer artifacts, leaving whole-calculation state on no worker.
def compute(arguments, specification: dict) -> None:
    """Assemble one admitted halo, invoke the exact pinned C kernel and pack its complete output."""

    options=specification["arguments"]
    p,r,side=options["p"],options["r"],options["tile_side"]
    rectangle=tile(p,r,side,options["row"],options["column"])
    cache=arguments.output.parent/"tile-inputs"
    cache.mkdir()
    sources={}
    fetched={"band":0,"packet":0}
    counts={"band":0,"packet":0}
    for record in descriptions(arguments):
        source,mode,size=acquire_input(record,arguments,p,r,side,rectangle,cache)
        sources[(record["row"],record["column"])]=source
        fetched[mode]+=size
        counts[mode]+=1
    halo=arguments.output.parent/"halo.bin"
    build_halo(p,r,side,rectangle,sources,halo,int(options.get("max_tile_bytes",2*1024**3)))
    shutil.rmtree(cache)
    if REPORTER is not None:
        REPORTER.close()
    output=arguments.output.parent/"tile-output"
    cpus=sorted(os.sched_getaffinity(0))
    threads=min(int(options.get("threads",1)),len(cpus))
    tail=[str(p),str(r),str(rectangle.first_u),str(rectangle.last_u),str(rectangle.first_v),str(rectangle.last_v),
          str(halo),str(output),str(threads),str(options.get("max_tile_bytes",2*1024**3))]

    def kernel(binary):
        """Run one tile binary under the lease's CPU affinity; return its exit status."""
        command=[sys.executable,str(ROOT.parent/"cluster"/"affinity_exec.py"),"--parent-pid",str(os.getpid()),
                 "--cpus",",".join(map(str,cpus)),"--",str(binary),*tail]
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
    device=int(os.environ.get("KH_GPU_DEVICE","0"))
    if (GPU_TILE.exists() and os.environ.get("KH_DISABLE_GPU_DP")!="1" and
            not gpus.recently_unavailable(device)):
        lock=gpus.DeviceLock(device)
        if lock.acquire(float(os.environ.get("KH_GPU_DP_WAIT_SECONDS","120")),lambda: STOP,skip_long=True):
            try:
                code=kernel(GPU_TILE)
            finally:
                lock.release()
            computed=code==0
            if code==3:
                gpus.mark_unavailable(device)
            if not computed and output.exists():
                shutil.rmtree(output)
        if STOP:
            raise InterruptedError("tile stopped")
    if not computed:
        code=kernel(ROOT.parent/"dp_solver"/"kh_dp_tile")
        if code!=0:
            raise RuntimeError(f"C tile kernel exited {code}")
    with arguments.output.open("xb") as stream:
        with tarfile.open(fileobj=stream,mode="w|gz",format=tarfile.USTAR_FORMAT,compresslevel=1) as archive:
            for name in ("values.bin","choices.bin","tile.json"):
                archive.add(output/name,arcname=name,recursive=False)
        stream.flush()
        os.fsync(stream.fileno())
    published=publish_bands(arguments,p,r,side,rectangle,output/"values.bin",arguments.output)
    cells=rectangle.value_bytes//8
    mode="none" if not counts["band"]+counts["packet"] else "bands" if not counts["packet"] else "packets" if not counts["band"] else "mixed"
    print(json.dumps({"done":cells,"total":cells,"checkpoint_done":cells,"units":"cells","phase":"complete","heartbeat":True,
                      "engine":"gpu" if computed else "cpu","input_mode":mode,
                      "input_bytes":fetched["band"]+fetched["packet"],"input_band_bytes":fetched["band"],
                      "bands_published":published}),flush=True)


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
