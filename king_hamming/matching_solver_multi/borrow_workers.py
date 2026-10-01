#!/usr/bin/env python3
"""Temporarily retain campaign workers as storage-only peers for isolated tests."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dp_solver.launch_dp import request, save, stop_owned_worker
from cluster.deployment import remote

START = '''import json,pathlib,shlex,subprocess,sys
record=json.loads(sys.argv[1]); storage=sys.argv[2]=='1'
root=pathlib.Path(record['root']); command=shlex.split(record['command'])
if '--storage-only' in command: command.remove('--storage-only')
if storage: command.append('--storage-only')
with (root/'agent.log').open('ab') as log:
    child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
record.update(pid=child.pid,start=pathlib.Path(f'/proc/{child.pid}/stat').read_text().split()[21],command=shlex.join(command))
(root/'process.json').write_text(json.dumps(record,indent=2)+'\\n')
print(json.dumps(record))
'''


def replace_record(path, replacement):
    manifest = json.loads(path.read_text())
    manifest["workers"] = [replacement if row["host"] == replacement["host"] else row
                           for row in manifest["workers"]]
    save(path, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("borrow", "restore"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--hosts", default="192.168.4.107,192.168.4.108")
    args = parser.parse_args()
    manifest_path = args.state.resolve() / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    base = manifest["leader"]
    if args.action == "borrow":
        if args.record.exists():
            raise FileExistsError(args.record)
        selected = set(args.hosts.split(","))
        originals = [row for row in manifest["workers"] if row["host"] in selected]
        if len(originals) != len(selected):
            raise ValueError("requested host missing from campaign")
        status = request(base, "/v1/status")
        audit = dict(originals=originals, replacements=[], prior_state=status["campaign_state"], started=time.time())
        save(args.record, audit)
        request(base, "/v1/control", {"state": "stopped"})
        try:
            deadline = time.monotonic() + 90
            while any(row["state"] in {"running", "stopping"} for row in request(base, "/v1/status")["runs"]):
                if time.monotonic() > deadline:
                    raise TimeoutError("campaign did not quiesce")
                time.sleep(1)
            for record in originals:
                stop_owned_worker(record["host"], record)
                replacement = remote(record["host"], START, [json.dumps(record), "1"])
                audit["replacements"].append(replacement)
                save(args.record, audit)
                replace_record(manifest_path, replacement)
            deadline = time.monotonic() + 30
            expected = {"dp-" + host.rsplit(".", 1)[1] for host in selected}
            while True:
                nodes = request(base, "/v1/status")["nodes"]
                ready = {n["node_name"] for n in nodes if n["state"] == "healthy" and not n["compute_enabled"]}
                if expected <= ready:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("storage peers did not register")
                time.sleep(1)
        finally:
            request(base, "/v1/control", {"state": audit["prior_state"]})
        print(json.dumps({"borrowed": sorted(selected), "campaign": audit["prior_state"]}))
    else:
        audit = json.loads(args.record.read_text())
        restored = set(audit.get("restored", []))
        for original in audit["originals"]:
            if original["host"] in restored:
                continue
            current = next(row for row in json.loads(manifest_path.read_text())["workers"] if row["host"] == original["host"])
            stop_owned_worker(current["host"], current)
            replacement = remote(original["host"], START, [json.dumps(original), "0"])
            replace_record(manifest_path, replacement)
            restored.add(original["host"])
            audit["restored"] = sorted(restored)
            save(args.record, audit)
        print(json.dumps({"restored": sorted(restored)}))


if __name__ == "__main__":
    main()
