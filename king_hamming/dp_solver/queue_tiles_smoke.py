#!/usr/bin/env python3
"""Test queued distributed DP and ordinary agent loss on isolated household deployments."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import argparse
from array import array
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.error import HTTPError

from blob_store import fetch_blob
from deployment import bundle, collect_and_remove, deploy, request, start_worker, stop_worker, wait
from dp_solver.distributed import artifact_record
from dp_solver.distributed_solver import unpack
from dp_solver.tiles import tile

ROOT=Path(__file__).resolve().parent.parent/"cluster"


# Preserve a complete reference comparison using bounded experiment-only dense arrays.
def verify(database: Path, evidence: Path, temporary: Path, run_id: str, result: dict) -> dict:
    """Compare every distributed tile cell and reconstructed split against raw C; return hashes."""

    p,r,side=13,5,512
    width=2198
    values=array("Q",[0])*width**2
    choices=array("I",[0])*width**2
    cache=temporary/"verification-cache"
    cache.mkdir()
    with sqlite3.connect(database) as connection:
        connection.row_factory=sqlite3.Row
        rows=connection.execute("SELECT t.row,t.column,r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id WHERE t.parent_run_id=? ORDER BY t.row,t.column",(run_id,)).fetchall()
        descriptors=[(row["row"],row["column"],artifact_record(connection,row,time.time())) for row in rows]
    for row,column,record in descriptors:
        rectangle=tile(p,r,side,row,column)
        packet=fetch_blob(cache/"blobs",record["sha256"],record["size"],record["locations"])
        directory=cache/f"tile-{row}-{column}"
        unpack(packet,directory,rectangle,p,r)
        tile_width=rectangle.last_v-rectangle.first_v+1
        for name,target,code in (("values",values,"Q"),("choices",choices,"I")):
            data=array(code)
            data.frombytes((directory/(name+".bin")).read_bytes())
            for offset,u in enumerate(range(rectangle.first_u,rectangle.last_u+1)):
                target[u*width+rectangle.first_v:u*width+rectangle.last_v+1]=data[offset*tile_width:(offset+1)*tile_width]
    reference=temporary/"reference"
    reference_artifact=temporary/"reference.json"
    subprocess.run([str(ROOT.parent/"dp_solver"/"kh_dp_local"),str(p),str(r),"--threads","2","--raw-transitions","--max-visits","30000000000","--work-dir",str(reference),"-o",str(reference_artifact)],check=True,capture_output=True,timeout=120)
    if values.tobytes()!=(reference/"values.bin").read_bytes() or choices.tobytes()!=(reference/"choices.bin").read_bytes():
        raise ValueError("queued distributed DP tables differ from raw reference")
    from urllib.request import urlopen
    with urlopen(result["artifact_location"],timeout=5) as response:
        content=response.read()
    if json.loads(content)!=json.loads(reference_artifact.read_text()):
        raise ValueError("queued distributed split differs from raw reference")
    (evidence/"result.json").write_bytes(content)
    return {"values_hash":hashlib.sha256(values.tobytes()).hexdigest(),"choices_hash":hashlib.sha256(choices.tobytes()).hexdigest(),"theta":json.loads(content)["theta"],"tiles":len(rows)}


# Require an explicit run request before touching any household machine.
def main() -> int:
    """Run one private queued failover experiment and retain evidence after exact verification."""

    parser=argparse.ArgumentParser(description="Verify queued distributed tiles and ordinary agent loss on household workers.",
        epilog="Example: ./queue_tiles_smoke.py --run")
    parser.add_argument("--run",action="store_true")
    arguments=parser.parse_args()
    if not arguments.run:
        parser.print_help()
        return 0
    hosts=["192.168.4.101","192.168.4.102","192.168.4.103"]
    identifier="kh-recovery-queued-"+uuid.uuid4().hex
    directory="/tmp/"+identifier
    evidence=ROOT/"experiments"/identifier
    evidence.mkdir()
    temporary=tempfile.TemporaryDirectory(prefix="kh-queued-tiles-")
    local=Path(temporary.name)
    database=local/"leader.sqlite"
    workers={}
    deployed=[]
    report={"experiment":identifier,"cleanup":{}}
    leader=None
    started=time.monotonic()
    try:
        with socket.socket() as listener:
            listener.bind(("0.0.0.0",0))
            port=listener.getsockname()[1]
        base=f"http://192.168.4.151:{port}"
        with (evidence/"leader.log").open("wb") as log:
            leader=subprocess.Popen([sys.executable,str(ROOT/"leader.py"),"serve","--database",str(database),"--listen",f"0.0.0.0:{port}","--lease-seconds","8","--pin-leader-core"],stdout=log,stderr=log)

        def ready():
            """Wait until the private leader is ready without confusing startup connection refusal."""

            try:
                return request(base,"/v1/health")["ok"]
            except OSError:
                return False

        wait(ready,"private queued leader startup",10)
        archive=bundle()
        for host in hosts:
            deployed.append(host)
            deploy(host,directory,archive)
            workers[host]=start_worker(host,directory,base,"queued-"+host.rsplit(".",1)[1],False)
        wait(lambda:len(request(base,"/v1/status")["nodes"])==3,"three ordinary remote agents",30)
        specification={"program":"dp_distributed","arguments":{"p":13,"r":5,"tile_side":512,"threads":2,"max_visits":30_000_000_000}}
        queued=request(base,"/v1/enqueue",{"specification":specification})

        def parent():
            """Read this root calculation while keeping a failed parent visible."""

            row=next(row for row in request(base,"/v1/status")["runs"] if row["run_id"]==queued["run_id"])
            if row["state"]=="failed":
                raise RuntimeError(row["error"])
            return row

        def active_with_frontier():
            """Kill a genuine active child only after some independently replicated progress exists."""

            row=parent()
            if not 0<row["progress_done"]<row["progress_total"]:
                return None
            with sqlite3.connect(database) as connection:
                connection.row_factory=sqlite3.Row
                child=connection.execute("SELECT r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id WHERE t.parent_run_id=? AND r.state='running' AND r.node_name='queued-101'",(queued["run_id"],)).fetchone()
            return (row,dict(child)) if child else None

        before,child=wait(active_with_frontier,"replicated frontier and active origin child",180)
        report["before_loss"]=before
        report["lost_child"]=child
        stop_worker(hosts[0],directory,workers.pop(hosts[0]))
        print(f"killed only the experiment's .101 agent after {before['progress_done']} replicated cells",flush=True)
        finished=wait(lambda:row if (row:=parent())["state"]=="complete" else None,"queued distributed completion after node loss",300)
        report["after_recovery"]=finished
        try:
            request(base,"/v1/complete",{"run_id":child["run_id"],"lease_token":child["lease_token"],"artifact_hash":"0"*64,"artifact_location":"http://stale.invalid"})
            raise RuntimeError("stale child lease accepted a completion")
        except HTTPError as error:
            if error.code!=409:
                raise
        report.update(verify(database,evidence,local,queued["run_id"],finished))
        with sqlite3.connect(database) as connection:
            connection.row_factory=sqlite3.Row
            report["child_attempts"]=[dict(row) for row in connection.execute("SELECT * FROM lease_history WHERE run_id=? ORDER BY attempt",(child["run_id"],))]
        report["verified"]=True
        report["status"]=request(base,"/v1/status")
        print("queued distributed tables and split match raw C after ordinary agent loss",flush=True)
    except Exception as error:
        report["error"]=str(error)
        print(f"queue_tiles_smoke.py: {error}",file=sys.stderr)
    finally:
        blocked=set()
        for host,record in workers.items():
            try:
                stop_worker(host,directory,record)
            except Exception as error:
                blocked.add(host)
                report["cleanup"][host]=str(error)
        for host in deployed:
            if host in blocked:
                continue
            try:
                collected=collect_and_remove(host,directory)
                (evidence/(host+".log")).write_text(collected["log"])
                report["cleanup"][host]="owned processes stopped and private directory removed"
            except Exception as error:
                report["cleanup"][host]=str(error)
        if leader is not None:
            leader.terminate()
            try:
                leader.wait(timeout=5)
            except subprocess.TimeoutExpired:
                leader.kill()
                leader.wait(timeout=5)
        temporary.cleanup()
        report["elapsed_seconds"]=time.monotonic()-started
        (evidence/"report.json").write_text(json.dumps(report,indent=2)+"\n")
        print(f"evidence: {evidence}",flush=True)
    return 0 if report.get("verified") and not report.get("error") and all(value=="owned processes stopped and private directory removed" for value in report["cleanup"].values()) else 1


if __name__=="__main__":
    raise SystemExit(main())
