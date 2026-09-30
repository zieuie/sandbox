"""Shared SSH deployment, runtime bundling, and owned process controls."""

from __future__ import annotations

import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import time
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]

# Quote remote Python arguments as shell words, never as interpolated shell code.
def remote(host: str, code: str, arguments: list[str], data: bytes = b"") -> dict[str, Any]:
    """Run code on host with arguments/data; return its sole JSON result or raise."""

    command = [*SSH, host, shlex.join(["python3", "-c", code, *arguments])]
    result = subprocess.run(command, input=data, capture_output=True, timeout=30)

    if result.returncode:
        raise RuntimeError(f"SSH {host}: {result.stderr.decode(errors='replace')[-2000:]}")

    return json.loads(result.stdout)


# Exchange small control messages without transferring solver state through the leader.
def request(base: str, route: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET route or POST value to base; return the decoded JSON object."""

    body = None if value is None else json.dumps(value).encode()
    query = Request(base + route, data=body, method="GET" if value is None else "POST")

    if body is not None:
        query.add_header("Content-Type", "application/json")

    with urlopen(query, timeout=5) as response:
        return json.load(response)


# Poll a concrete condition while preserving a useful experiment timeout.
def wait(predicate: Any, message: str, seconds: float = 180) -> Any:
    """Return the first truthy predicate result, or raise after seconds."""

    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        value = predicate()

        if value:
            return value

        time.sleep(0.1)

    raise RuntimeError(f"timed out: {message}")


# Package the current trusted Python modules and centrally compiled portable binaries.
def bundle() -> bytes:
    """Return a small tar archive containing only runtime code and solver executables."""

    output = io.BytesIO()

    with tarfile.open(fileobj=output, mode="w") as archive:
        for source in sorted(ROOT.glob("*.py")):
            if source.name not in {"cluster_smoke.py", "queue_tiles_smoke.py"}:
                archive.add(source, arcname=f"cluster/{source.name}")

        archive.add(ROOT.parent / "adapter_config.py", arcname="adapter_config.py")
        for source in sorted((ROOT.parent / "campaigns").glob("*.py")):
            archive.add(source, arcname=f"campaigns/{source.name}")
        import adapters
        for source, name in adapters.runtime_files():
            archive.add(source, arcname=name)

    return output.getvalue()


# Deploy solely into the unique temporary root owned by this experiment.
def deploy(host: str, directory: str, archive: bytes) -> dict[str, Any]:
    """Create directory on host and extract runtime archive without installing services."""

    code = """import json, pathlib, sys, tarfile
root = pathlib.Path(sys.argv[1])
root.mkdir(mode=0o700)
with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
    archive.extractall(root, filter='data')
print(json.dumps({'root': str(root)}))
"""
    return remote(host, code, [directory], archive)


# Launch one independently addressable worker whose PID identity is recorded locally.
def start_worker(
    host: str, directory: str, leader: str, name: str, storage_only: bool,
    phase_delay: float = 0,
) -> dict[str, Any]:
    """Start a private agent on host; return PID/start identity and peer storage URL."""

    code = """import json, os, pathlib, socket, subprocess, sys
root = pathlib.Path(sys.argv[1])
with socket.socket() as listener:
    listener.bind(('0.0.0.0',0))
    port = listener.getsockname()[1]
arguments = [sys.executable, str(root/'cluster'/'agent.py'), 'run', '--leader', sys.argv[2],
             '--name',sys.argv[3], '--cpus','0' if sys.argv[4]=='1' else '0,1',
             '--work-root',str(root/'work'), '--storage-root',str(root/'blobs'),
             '--storage-listen',f'0.0.0.0:{port}', '--storage-url',f'http://{sys.argv[5]}:{port}',
             '--poll-seconds','0.1', '--control-seconds','0.2']
if sys.argv[4]=='1':
    arguments.append('--storage-only')
environment=os.environ.copy()
if float(sys.argv[6])>0:
    environment['KH_MATCH_TEST_PHASE_DELAY']=sys.argv[6]
with (root/'agent.log').open('ab') as log:
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                               start_new_session=True, env=environment)
start = pathlib.Path(f'/proc/{process.pid}/stat').read_text().split()[21]
print(json.dumps({'pid':process.pid,'start':start,'port':port,'name':sys.argv[3]}))
"""
    return remote(host, code, [directory, leader, name, "1" if storage_only else "0",
                               host, str(phase_delay)])


# Signal only a process group whose start identity and command match this private deployment.
def stop_worker(host: str, directory: str, record: dict[str, Any]) -> None:
    """Kill the exact owned test agent; PID reuse or a different command is rejected."""

    code = """import json, os, pathlib, signal, sys, time
pid = int(sys.argv[2])
path = pathlib.Path(f'/proc/{pid}')
if path.exists():
    actual = (path/'stat').read_text().split()[21]
    command = (path/'cmdline').read_bytes()
    if actual != sys.argv[3] or (sys.argv[1]+'/cluster/agent.py').encode() not in command:
        raise RuntimeError('refusing to signal a process outside this experiment')
    os.killpg(pid,signal.SIGKILL)
    time.sleep(0.1)
print(json.dumps({'stopped':pid}))
"""
    remote(host, code, [directory, str(record["pid"]), str(record["start"])])


# Collect a bounded log, then delete only the private temporary deployment tree.
def collect_and_remove(host: str, directory: str) -> dict[str, Any]:
    """Return the deployment's final log and remove its owned temporary directory."""

    code = """import json,pathlib,shutil,sys
root=pathlib.Path(sys.argv[1])
if root.parent != pathlib.Path('/tmp') or not root.name.startswith('kh-recovery-'):
    raise RuntimeError('invalid experiment cleanup root')
log=(root/'agent.log').read_text()[-64000:] if (root/'agent.log').exists() else ''
shutil.rmtree(root)
print(json.dumps({'log':log,'removed':str(root)}))
"""
    return remote(host, code, [directory])

