#!/usr/bin/env python3
"""Launch and operate a retained household DP campaign without installing services."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import argparse
import concurrent.futures
import ipaddress
import hashlib
import os
import json
from pathlib import Path
import shlex
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from deployment import ROOT, bundle, deploy, remote, wait
from dp_solver import scheduling


# Allow busy operator transactions more time without changing worker lease polling.
def request(base: str, route: str, value: dict | None = None) -> dict:
    """GET route or POST value with a bounded 45-second operator timeout; return JSON."""

    body = None if value is None else json.dumps(value).encode()
    query = Request(base+route, data=body, method='GET' if value is None else 'POST')
    if body is not None:
        query.add_header('Content-Type', 'application/json')
    with urlopen(query, timeout=45) as response:
        return json.load(response)


# Persist process identities and commands after each successful deployment step.
def save(path: Path, manifest: dict) -> None:
    """Write manifest atomically to path; return no value."""

    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(path)


# Launch a detached agent with persistent storage and automatically selected CPU affinity.
def build_runtime() -> None:
    """Build every bundled native solver; GPU binaries need only cc (images are committed)."""

    for directory, target in (('dp_solver', 'all'), ('matching_solver', 'all'),
                              ('matching_solver_multi', 'all'), ('cuda', 'all'),
                              ('gpu_match_solver', 'kh_gpu_match_kernel'),
                              ('gpu_block_match_solver', 'kh_gpu_block_kernel'), ('gpu_dp_solver', 'all')):
        subprocess.run(['make', '-C', str(ROOT.parent/directory), target], check=True)


def launch_worker(host: str, directory: str, leader: str, leader_node: bool = False,
                  runtime_version: str = "", private_network: dict | None = None) -> dict:
    """Start one agent on host; return its exact command and process identity."""

    code = '''import json,pathlib,shlex,socket,subprocess,sys
root=pathlib.Path(sys.argv[1])
with socket.socket() as listener:
    listener.bind(('0.0.0.0',0))
    port=listener.getsockname()[1]
arguments=[sys.executable,str(root/'cluster'/'agent.py'),'run','--leader',sys.argv[2],
    '--name','dp-'+sys.argv[3].rsplit('.',1)[1],'--cpus','auto','--slots','auto',
    '--work-root',str(root/'work'),'--storage-root',str(root/'blobs'),
    '--storage-listen',f'0.0.0.0:{port}','--storage-url',f'http://{sys.argv[3]}:{port}',
    '--runtime-version',sys.argv[5]]
if sys.argv[6]:
    arguments.extend(['--storage-private-url',f'http://{sys.argv[6]}:{port}',
                      '--storage-private-group',sys.argv[7]])
if sys.argv[4]=='1':
    arguments.append('--leader-node')
with (root/'agent.log').open('ab') as log:
    process=subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
start=pathlib.Path(f'/proc/{process.pid}/stat').read_text().split()[21]
record={'host':sys.argv[3],'private_network':{'address':sys.argv[6],'group':sys.argv[7]} if sys.argv[6] else None,
        'root':str(root),'pid':process.pid,'start':start,'port':port,'command':shlex.join(arguments)}
(root/'process.json').write_text(json.dumps(record,indent=2)+'\\n')
print(json.dumps(record))
'''
    return remote(host, code, [directory, leader, host,
                               '1' if leader_node else '0', runtime_version,
                               (private_network or {}).get('address', ''),
                               (private_network or {}).get('group', '')])


def private_network(manifest: dict, host: str) -> dict | None:
    """Return an optional same-LAN data endpoint without replacing the fallback URL."""

    return manifest.get('private_networks', {}).get(host)


def set_storage_addresses(path: Path, group: str, assignments: list[str]) -> None:
    """Record same-LAN data IPs without changing Wi-Fi SSH, leader, or fallback URLs."""

    manifest = json.loads(path.read_text())
    if not group or len(group) > 64 or not all(c.isalnum() or c in '-_' for c in group):
        raise ValueError('network group must be a short name containing letters, digits, - or _')
    known = {worker['host'] for worker in manifest['workers']}
    mapping = dict(manifest.get('private_networks', {}))
    for assignment in assignments:
        host, separator, address = assignment.partition('=')
        if not separator or host not in known:
            raise ValueError(f'expected a retained worker HOST=IP: {assignment}')
        ipaddress.ip_address(address)
        mapping[host] = {'address': address, 'group': group}
    if len({item['address'] for item in mapping.values()}) != len(mapping):
        raise ValueError('private data addresses must be unique')
    stop_dispatch_if_idle(path.parent / 'leader.sqlite')
    manifest['private_networks'] = mapping
    save(path, manifest)
    print(json.dumps({'private_networks': mapping, 'campaign_state': 'stopped'}, indent=2))


# Check all target machines before creating a campaign or starting any computation.
def preflight(host: str) -> dict:
    """Return host identity, home and free disk bytes; raise if storage is insufficient."""

    code = '''import json,pathlib,shutil,socket
home=pathlib.Path.home()
free=shutil.disk_usage(home).free
if free<20*1024**3:
    raise RuntimeError('campaign requires at least 20 GiB free')
print(json.dumps({'hostname':socket.gethostname(),'home':str(home),'free_bytes':free}))
'''
    return remote(host, code, [])


# Start the retained control plane, five detached workers and a bounded table frontier.
def start(arguments: argparse.Namespace, path: Path) -> None:
    """Create this campaign and enqueue its DP entries; retain all launch state on failure."""

    if path.parent.exists():
        raise ValueError(f'{path.parent} already exists; use status, stop or resume')
    if len(set(arguments.hosts)) != len(arguments.hosts):
        raise ValueError('worker addresses must be distinct')
    for host in [arguments.address, *arguments.hosts]:
        ipaddress.ip_address(host)
    with socket.socket() as listener:
        listener.bind(('0.0.0.0', arguments.port))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(arguments.hosts)) as pool:
        facts = dict(zip(arguments.hosts, pool.map(preflight, arguments.hosts)))
    build_runtime()
    entries = list(scheduling.campaign(16*1024**3, arguments.max_visits, 4, 512))[:arguments.limit]
    if not entries:
        raise ValueError('no calculations fit the requested frontier')
    path.parent.mkdir(parents=True)
    identifier = 'dp-' + uuid.uuid4().hex
    base = f'http://{arguments.address}:{arguments.port}'
    manifest = {'id': identifier, 'leader': base, 'workers': [], 'preflight': facts,
                'max_visits': arguments.max_visits, 'entries': [], 'state': 'starting'}
    save(path, manifest)
    command = [sys.executable, str(ROOT/'leader.py'), 'serve', '--database',
               str(path.parent/'leader.sqlite'), '--listen', f'0.0.0.0:{arguments.port}',
               '--checkpoint-seconds', '1800', '--pin-leader-core']
    with (path.parent/'leader.log').open('ab') as log:
        leader = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=log, start_new_session=True)
    manifest['leader_process'] = {'pid': leader.pid,
        'start': Path(f'/proc/{leader.pid}/stat').read_text().split()[21],
        'command': shlex.join(command)}
    save(path, manifest)

    def ready() -> bool:
        """Return whether the new leader responds, or raise if its process exited."""

        if leader.poll() is not None:
            raise RuntimeError('leader exited; inspect leader.log')
        try:
            return request(base, '/v1/health')['ok']
        except OSError:
            return False

    wait(ready, 'leader startup', 15)
    archive = bundle()
    runtime_version = hashlib.sha256(archive).hexdigest()
    for host in arguments.hosts:
        directory = str(Path(facts[host]['home'])/'.local/share/king_hamming'/identifier)
        remote(host, "import json,pathlib,sys; pathlib.Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True); print(json.dumps({'ok':True}))", [directory])
        deploy(host, directory, archive)
        record = launch_worker(host, directory, base, host == arguments.address,
                               runtime_version, private_network(manifest, host))
        manifest['workers'].append(record)
        save(path, manifest)
    expected = {'dp-'+host.rsplit('.',1)[1] for host in arguments.hosts}
    wait(lambda: expected <= {node['node_name'] for node in request(base, '/v1/status')['nodes']},
         'worker registration', 30)
    for specification in entries:
        specification['program'] = 'dp_distributed'
        specification['arguments']['max_tile_bytes'] = 2*1024**3
        specification['arguments']['artifact_format'] = 'KHD1'
        result = request(base, '/v1/enqueue', {'specification': specification})
        manifest['entries'].append({'specification': specification, **result})
        save(path, manifest)
    manifest['state'] = 'running'
    save(path, manifest)
    print(json.dumps({'manifest': str(path), 'leader': base, 'workers': len(expected),
                      'queued_calculations': len(entries)}, indent=2))


# Attach new compute/storage nodes without restarting the leader or existing agents.
def add_workers(arguments: argparse.Namespace, path: Path) -> None:
    """Deploy requested new hosts into this retained campaign; save each successful launch."""

    manifest = json.loads(path.read_text())
    hosts = list(dict.fromkeys(arguments.hosts))
    for host in hosts:
        ipaddress.ip_address(host)
    existing = {worker['host'] for worker in manifest['workers']}
    hosts = [host for host in hosts if host not in existing]
    if not hosts:
        print(json.dumps({'added_workers': 0, 'total_workers': len(existing)}))
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        facts = dict(zip(hosts, pool.map(preflight, hosts)))
    build_runtime()
    archive = bundle()
    runtime_version = hashlib.sha256(archive).hexdigest()
    for host in hosts:
        directory = str(Path(facts[host]['home'])/'.local/share/king_hamming'/manifest['id'])
        manifest.setdefault('preflight', {})[host] = facts[host]
        save(path, manifest)
        remote(host, "import json,pathlib,sys; pathlib.Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True); print(json.dumps({'ok':True}))", [directory])
        deploy(host, directory, archive)
        leader_host = urlparse(manifest['leader']).hostname
        manifest['workers'].append(launch_worker(
            host, directory, manifest['leader'], host == leader_host,
            runtime_version, private_network(manifest, host)))
        save(path, manifest)
    print(json.dumps({'added_workers': len(hosts), 'total_workers': len(manifest['workers']),
                      'manifest': str(path)}, indent=2))


def replace_runtime(host: str, directory: str, archive: bytes) -> None:
    """Atomically replace bundled runtime files inside one retained worker root."""

    code = '''import json,os,pathlib,shutil,sys,tarfile,uuid
root=pathlib.Path(sys.argv[1])
if not root.is_dir() or root.name.startswith('.') or root.parent.name!='king_hamming':
    raise RuntimeError('refusing to update an unexpected worker root')
stage=root.parent/(root.name+'.stage-'+uuid.uuid4().hex)
stage.mkdir(mode=0o700)
try:
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
        archive.extractall(stage,filter='data')
    files=0
    for source in sorted(stage.rglob('*')):
        if not source.is_file():
            continue
        relative=source.relative_to(stage)
        target=root/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        os.replace(source,target)
        files+=1
finally:
    shutil.rmtree(stage,ignore_errors=True)
print(json.dumps({'root':str(root),'files':files}))
'''
    remote(host, code, [directory], archive)


def validate_owned_leader(manifest: dict, database: Path) -> list[str]:
    """Return the recorded leader argv only when PID/start/command still match."""
    record = manifest.get("leader_process") or {}
    pid = int(record.get("pid", 0))
    process = Path(f"/proc/{pid}")
    if pid < 2 or not process.exists():
        raise RuntimeError("recorded leader process is not alive")
    actual_start = (process / "stat").read_text().split()[21]
    actual_command = (process / "cmdline").read_bytes().replace(b"\0", b" ")
    expected_program = str(ROOT / "leader.py")
    if (actual_start != str(record.get("start")) or
            expected_program.encode() not in actual_command or
            str(database).encode() not in actual_command):
        raise RuntimeError("refusing to restart a process outside this deployment")
    command = shlex.split(str(record.get("command", "")))
    if expected_program not in command or str(database) not in command:
        raise RuntimeError("recorded leader command does not match this deployment")
    return command


def restart_owned_leader(manifest: dict, manifest_path: Path) -> None:
    """Restart the exact stopped-campaign leader so schema and adapter code upgrade too."""
    database = manifest_path.parent / "leader.sqlite"
    command = validate_owned_leader(manifest, database)
    old_pid = int(manifest["leader_process"]["pid"])
    os.killpg(old_pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while Path(f"/proc/{old_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if Path(f"/proc/{old_pid}").exists():
        raise RuntimeError("owned leader did not stop within 15 seconds")
    with (manifest_path.parent / "leader.log").open("ab") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    manifest["leader_process"] = {
        "pid": process.pid,
        "start": Path(f"/proc/{process.pid}/stat").read_text().split()[21],
        "command": shlex.join(command),
    }
    save(manifest_path, manifest)

    def healthy() -> bool:
        if process.poll() is not None:
            raise RuntimeError("upgraded leader exited during startup")
        try:
            return bool(request(manifest["leader"], "/v1/health").get("ok"))
        except OSError:
            return False

    wait(healthy, "upgraded leader startup", 30)


def start_absent_leader(manifest: dict, manifest_path: Path) -> None:
    """Recover a dead retained leader without ever creating a duplicate instance."""

    database = manifest_path.parent / 'leader.sqlite'
    record = manifest.get('leader_process') or {}
    command = shlex.split(str(record.get('command', '')))
    program = str(ROOT / 'leader.py')
    if (program not in command or str(database) not in command or
            not database.is_file() or Path(f"/proc/{int(record.get('pid', 0))}").exists()):
        raise RuntimeError('refusing to recover an unverified or still-present leader')
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            actual = (process / 'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if program.encode() in actual and str(database).encode() in actual:
            raise RuntimeError(f'an unrecorded leader is already running as PID {process.name}')
    with (manifest_path.parent / 'leader.log').open('ab') as log:
        replacement = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
    manifest['leader_process'] = {
        'pid': replacement.pid,
        'start': Path(f'/proc/{replacement.pid}/stat').read_text().split()[21],
        'command': shlex.join(command),
    }
    save(manifest_path, manifest)

    def healthy() -> bool:
        if replacement.poll() is not None:
            raise RuntimeError('recovered leader exited during startup')
        try:
            return bool(request(manifest['leader'], '/v1/health').get('ok'))
        except OSError:
            return False

    wait(healthy, 'recovered leader startup', 30)


def validate_owned_feeder(state: Path) -> tuple[dict, list[str]] | None:
    """Return the retained feeder identity and argv, or None when none is configured."""

    identity_path = state / "feeder_process.json"
    if not identity_path.exists():
        return None
    record = json.loads(identity_path.read_text())
    pid = int(record.get("pid", 0))
    process = Path(f"/proc/{pid}")
    command_value = record.get("command", [])
    command = ([str(value) for value in command_value]
               if isinstance(command_value, list)
               else shlex.split(str(command_value)))
    expected_program = str(ROOT / "continuous_campaign.py")
    if pid < 2 or not process.exists():
        raise RuntimeError("recorded continuous feeder process is not alive")
    actual_start = (process / "stat").read_text().split()[21]
    actual_command = (process / "cmdline").read_bytes().split(b"\0")
    if (actual_start != str(record.get("start")) or
            expected_program.encode() not in actual_command or
            expected_program not in command or
            "--state" not in command or str(state) not in command):
        raise RuntimeError("refusing to restart a feeder outside this deployment")
    return record, command


def restart_owned_feeder(state: Path) -> bool:
    """Restart the exact retained feeder so campaign policy code upgrades too."""

    validated = validate_owned_feeder(state)
    if validated is None:
        return False
    record, command = validated
    old_pid = int(record["pid"])
    os.killpg(old_pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while Path(f"/proc/{old_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if Path(f"/proc/{old_pid}").exists():
        raise RuntimeError("owned continuous feeder did not stop within 15 seconds")
    with (state / "feeder.log").open("ab") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    replacement = {
        "pid": process.pid,
        "start": Path(f"/proc/{process.pid}/stat").read_text().split()[21],
        "command": command,
    }
    save(state / "feeder_process.json", replacement)
    return True


def ensure_feeder(state: Path) -> bool:
    """Start the exact retained feeder command when its recorded process is gone."""

    identity_path = state / "feeder_process.json"
    record = json.loads(identity_path.read_text())
    command_value = record.get("command", [])
    command = ([str(value) for value in command_value]
               if isinstance(command_value, list) else shlex.split(str(command_value)))
    expected_program = str(ROOT / "continuous_campaign.py")
    if (expected_program not in command or "--state" not in command or
            str(state) not in command or "run" not in command):
        raise RuntimeError("retained feeder command is outside this deployment")
    pid = int(record.get("pid", 0))
    process_path = Path(f"/proc/{pid}")
    if process_path.exists():
        actual_start = (process_path / "stat").read_text().split()[21]
        actual_command = (process_path / "cmdline").read_bytes().split(b"\0")
        if (actual_start != str(record.get("start")) or
                expected_program.encode() not in actual_command):
            raise RuntimeError("recorded feeder PID was reused; refusing replacement")
        return False
    with (state / "feeder.log").open("ab") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    save(identity_path, {"pid": process.pid,
                         "start": Path(f"/proc/{process.pid}/stat").read_text().split()[21],
                         "command": command})
    return True


def stop_owned_worker(host: str, record: dict) -> None:
    """Stop exactly one manifest-owned agent after checking PID identity."""

    code = '''import json,os,pathlib,signal,sys,time
root,pid,start=pathlib.Path(sys.argv[1]),int(sys.argv[2]),sys.argv[3]
proc=pathlib.Path(f'/proc/{pid}')
if proc.exists():
    actual=(proc/'stat').read_text().split()[21]
    command=(proc/'cmdline').read_bytes()
    if actual!=start or str(root/'cluster'/'agent.py').encode() not in command:
        raise RuntimeError('refusing to signal a process outside this campaign')
    os.killpg(pid,signal.SIGTERM)
    deadline=time.monotonic()+15
    while proc.exists() and time.monotonic()<deadline:
        time.sleep(.1)
    if proc.exists():
        os.killpg(pid,signal.SIGKILL)
        time.sleep(.1)
print(json.dumps({'stopped':pid}))
'''
    remote(host, code, [record['root'], str(record['pid']), str(record['start'])])


def resume_workers(path: Path) -> None:
    """Deploy current code and start absent retained agents without replacing blobs."""

    stop_dispatch_if_idle(path.parent / 'leader.sqlite')
    manifest = json.loads(path.read_text())
    leader_absent = not Path(f"/proc/{int(manifest['leader_process']['pid'])}").exists()
    if not leader_absent:
        validate_owned_leader(manifest, path.parent / 'leader.sqlite')
    old_workers = list(manifest['workers'])
    code = '''import json,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]); expected=sys.argv[2]
if not root.is_dir() or not (root/'blobs').is_dir():
    raise RuntimeError('retained worker root or blob storage is missing')
addresses={entry['local'] for interface in json.loads(subprocess.check_output(['ip','-j','address']))
           for entry in interface.get('addr_info',[]) if entry.get('family')=='inet'}
if expected not in addresses:
    raise RuntimeError('configured data address is not assigned on this host: '+expected)
program=str(root/'cluster'/'agent.py').encode()
running=[]
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdecimal():continue
    try:
        command=(proc/'cmdline').read_bytes().split(b'\\0')
        if program in command:
            running.append({'pid':int(proc.name),'start':(proc/'stat').read_text().split()[21],
                            'command':b' '.join(command).decode(errors='replace')})
    except (OSError,IndexError):pass
print(json.dumps({'running':running}))
'''
    checks = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(old_workers)) as pool:
        futures = {pool.submit(remote, worker['host'], code,
                               [worker['root'], private_network(manifest, worker['host'])['address']
                                if private_network(manifest, worker['host']) else worker['host']]): worker
                   for worker in old_workers}
        for future in concurrent.futures.as_completed(futures):
            worker = futures[future]
            checks[worker['host']] = future.result()
    for worker in old_workers:
        running = checks[worker['host']]['running']
        if running and (len(running) != 1 or running[0]['pid'] != worker['pid'] or
                        running[0]['start'] != str(worker['start']) or
                        worker.get('private_network') != private_network(manifest, worker['host'])):
            raise RuntimeError(f"unrecorded or differently configured agent on {worker['host']}")
    build_runtime()
    archive = bundle()
    runtime_version = hashlib.sha256(archive).hexdigest()
    if any(checks[worker['host']]['running'] and
           worker.get('command', '').split('--runtime-version ', 1)[-1].split()[0] != runtime_version
           for worker in old_workers):
        raise RuntimeError('a running retained agent has a different runtime version')
    if leader_absent:
        start_absent_leader(manifest, path)
    else:
        restart_owned_leader(manifest, path)
    leader_host = urlparse(manifest['leader']).hostname
    resumed = []
    for index, worker in enumerate(old_workers):
        running = checks[worker['host']]['running']
        if running:
            continue
        replace_runtime(worker['host'], worker['root'], archive)
        replacement = launch_worker(worker['host'], worker['root'], manifest['leader'],
                                    worker['host'] == leader_host, runtime_version,
                                    private_network(manifest, worker['host']))
        manifest['workers'][index] = replacement
        save(path, manifest)
        resumed.append(worker['host'])
    expected = {'dp-' + worker['host'].rsplit('.', 1)[1] for worker in old_workers}
    def registered():
        status = request(manifest['leader'], '/v1/status')
        healthy = {node['node_name'] for node in status['nodes']
                   if node['state'] == 'healthy' and node['runtime_version'] == runtime_version}
        return status if expected <= healthy else None
    status = wait(registered, 'retained worker registration', 60)
    manifest['runtime_version'] = runtime_version
    manifest['state'] = 'stopped-after-worker-resume'
    save(path, manifest)
    print(json.dumps({'resumed_hosts': resumed, 'campaign_state': status['campaign_state'],
                      'workers': len(expected)}, indent=2))


def stop_dispatch_if_idle(database_path: Path) -> None:
    """Atomically latch dispatch stopped only when no solver or tile is active."""

    with sqlite3.connect(database_path, timeout=45) as database:
        database.execute('BEGIN IMMEDIATE')
        active = [row[0] for row in database.execute(
            "SELECT run_id FROM runs WHERE state IN ('running','stopping')")]
        if active:
            database.rollback()
            raise ValueError(f'refusing worker upgrade with active runs: {active}')
        database.execute(
            "UPDATE settings SET value='stopped' WHERE key='campaign_state'")
        database.commit()


def drain_dispatch(database_path: Path) -> int:
    """Stop granting new leases without asking any running solver to stop.

    The transaction serializes with the leader's lease transactions. Existing
    tiles finish and publish normally; upgrade-workers still separately refuses
    to proceed until every running/stopping row has gone idle.
    """

    with sqlite3.connect(database_path, timeout=45) as database:
        database.execute('BEGIN IMMEDIATE')
        current = database.execute(
            "SELECT value FROM settings WHERE key='campaign_state'").fetchone()
        if current is None or current[0] not in {'running', 'stopped'}:
            raise ValueError('unknown campaign state')
        database.execute(
            "UPDATE settings SET value='stopped' WHERE key='campaign_state'")
        active = database.execute(
            "SELECT COUNT(*) FROM runs WHERE state IN ('running','stopping')").fetchone()[0]
        database.commit()
    return active


def upgrade_workers(path: Path) -> None:
    """Replace idle agent runtimes while preserving all retained campaign data."""

    manifest = json.loads(path.read_text())
    report = {"started": time.time(), "leader": manifest["leader"],
              "manifest": str(path), "stage": "guarding", "workers": [],
              "recovery": "leave dispatch stopped; rerun upgrade-workers after correcting the reported stage"}
    report_path = path.parent / "last_rollout.json"
    save(report_path, report)
    try:
        stop_dispatch_if_idle(path.parent / 'leader.sqlite')
        report["stage"] = "building"
        save(report_path, report)
        build_runtime()
        archive = bundle()
        runtime_version = hashlib.sha256(archive).hexdigest()
        report["runtime_version"] = runtime_version
        validate_owned_leader(manifest, path.parent / 'leader.sqlite')
        validate_owned_feeder(path.parent)
        old_workers = list(manifest['workers'])
        report["previous_workers"] = old_workers
        for worker in old_workers:
            stop_owned_worker(worker['host'], worker)
        report["stage"] = "leader-restart"
        save(report_path, report)
        restart_owned_leader(manifest, path)
        replacements = []
        leader_host = urlparse(manifest['leader']).hostname
        for worker in old_workers:
            report["stage"] = f"worker:{worker['host']}"
            save(report_path, report)
            replace_runtime(worker['host'], worker['root'], archive)
            replacement = launch_worker(
                worker['host'], worker['root'], manifest['leader'],
                worker['host'] == leader_host, runtime_version,
                private_network(manifest, worker['host']))
            replacements.append(replacement)
            report["workers"] = replacements
            manifest['workers'] = [*replacements, *old_workers[len(replacements):]]
            save(path, manifest)
            save(report_path, report)
        expected = {'dp-'+worker['host'].rsplit('.',1)[1] for worker in replacements}
        def upgraded_status():
            snapshot = request(manifest['leader'], '/v1/status')
            healthy = {node['node_name'] for node in snapshot['nodes']
                       if node.get('state') == 'healthy' and
                       node.get('runtime_version') == runtime_version}
            return snapshot if expected <= healthy else None
        status = wait(upgraded_status,
            'upgraded worker version registration', 60)
        manifest['runtime_version'] = runtime_version
        manifest['state'] = 'stopped-after-upgrade'
        save(path, manifest)
        feeder_restarted = restart_owned_feeder(path.parent)
        report.update(stage="complete", finished=time.time(),
                      feeder_restarted=feeder_restarted,
                      schema_version=status.get("schema_version"),
                      campaign_state=status.get("campaign_state"),
                      inventory={node["node_name"]: node.get("idle_reason")
                                 for node in status["nodes"]})
        save(report_path, report)
        print(json.dumps({'upgraded_workers': len(replacements),
                          'feeder_restarted': feeder_restarted,
                          'campaign_state': 'stopped', 'manifest': str(path),
                          'report': str(report_path)}, indent=2))
    except Exception as error:
        report.update(stage="failed:" + report["stage"], finished=time.time(), error=str(error))
        save(report_path, report)
        raise


def upgrade_leader(path: Path) -> None:
    """Guard and restart only the owned idle leader, preserving agent sessions."""

    manifest = json.loads(path.read_text())
    stop_dispatch_if_idle(path.parent / "leader.sqlite")
    validate_owned_leader(manifest, path.parent / "leader.sqlite")
    restart_owned_leader(manifest, path)
    status = request(manifest["leader"], "/v1/status")
    print(json.dumps({"leader_restarted": True,
                      "campaign_state": status["campaign_state"],
                      "schema_version": status.get("schema_version"),
                      "workers_preserved": len(manifest.get("workers", []))}, indent=2))


def upgrade_leader_live(path: Path) -> None:
    """Restart only the owned leader while healthy active leases keep running.

    Never use the stopped campaign state here: run-control treats that as a
    request to stop every active solver. The short leader outage is tolerated
    by agent renewal retries, provided every lease has sufficient slack.
    """

    manifest = json.loads(path.read_text())
    database_path = path.parent / "leader.sqlite"
    validate_owned_leader(manifest, database_path)
    expected_nodes = {'dp-' + worker['host'].rsplit('.', 1)[1]
                      for worker in manifest.get('workers', [])}
    with sqlite3.connect(database_path, timeout=45) as database:
        database.execute('BEGIN IMMEDIATE')
        now = time.time()
        settings = dict(database.execute(
            "SELECT key,value FROM settings WHERE key IN ('campaign_state','lease_seconds')"))
        lease_seconds = float(settings['lease_seconds'])
        if settings['campaign_state'] != 'running' or lease_seconds < 45:
            raise ValueError('live leader upgrade requires a running campaign with leases >=45 seconds')
        active = database.execute(
            "SELECT run_id,lease_expires FROM runs WHERE state IN ('running','stopping')").fetchall()
        if any(expires is None or expires - now < 30 for _, expires in active):
            raise ValueError('an active lease has less than 30 seconds remaining; retry shortly')
        nodes = {name: heartbeat for name, heartbeat in database.execute(
            "SELECT node_name,last_heartbeat FROM nodes")}
        if not expected_nodes or any(now - nodes.get(name, 0) > lease_seconds / 2
                                     for name in expected_nodes):
            raise ValueError('a retained worker heartbeat is stale; refusing live leader upgrade')
        database.commit()
    started = time.monotonic()
    restart_owned_leader(manifest, path)
    outage = time.monotonic() - started
    if outage >= 30:
        raise RuntimeError(f'leader restart took {outage:.1f}s; inspect active lease health')
    status = request(manifest['leader'], '/v1/status')
    healthy = {node['node_name'] for node in status['nodes'] if node.get('state') == 'healthy'}
    if status['campaign_state'] != 'running' or not expected_nodes <= healthy:
        raise RuntimeError('leader restarted, but campaign or retained worker health is not restored')
    print(json.dumps({'leader_restarted': True, 'campaign_state': 'running',
                      'workers_healthy': len(expected_nodes), 'active_leases_before': len(active),
                      'restart_seconds': round(outage, 2)}, indent=2))


def upgrade_worker_rolling(path: Path, host: str, wait_seconds: float) -> None:
    """Drain one host's new leases, then replace only its idle owned agent."""

    manifest = json.loads(path.read_text())
    worker = next((item for item in manifest['workers'] if item['host'] == host), None)
    if worker is None:
        raise ValueError(f'{host} is not a retained campaign worker')
    validate_owned_leader(manifest, path.parent / 'leader.sqlite')
    node_name = 'dp-' + host.rsplit('.', 1)[1]
    build_runtime()
    archive = bundle()
    version = hashlib.sha256(archive).hexdigest()
    request(manifest['leader'], '/v1/node-control', {'node_name': node_name, 'action': 'drain'})

    def idle() -> bool:
        with sqlite3.connect(f"file:{path.parent / 'leader.sqlite'}?mode=ro", uri=True) as database:
            active = database.execute(
                "SELECT COUNT(*) FROM runs WHERE node_name=? AND state IN ('running','stopping')",
                (node_name,)).fetchone()[0]
            partners = database.execute(
                "SELECT COUNT(*) FROM node_reservations WHERE node_name=?", (node_name,)).fetchone()[0]
        return active == 0 and partners == 0

    try:
        wait(idle, f'{node_name} to finish its existing leases', wait_seconds)
    except Exception:
        request(manifest['leader'], '/v1/node-control', {'node_name': node_name, 'action': 'resume'})
        raise

    # Leave this node drained on any replacement failure: a future retry can
    # recover it, but the leader must never lease against a half-updated agent.
    stop_owned_worker(host, worker)
    replace_runtime(host, worker['root'], archive)
    replacement = launch_worker(host, worker['root'], manifest['leader'],
                                host == urlparse(manifest['leader']).hostname,
                                version, private_network(manifest, host))
    replacement['runtime_version'] = version
    manifest['workers'][manifest['workers'].index(worker)] = replacement
    save(path, manifest)

    def healthy() -> bool:
        status = request(manifest['leader'], '/v1/status')
        return any(node['node_name'] == node_name and node.get('state') == 'healthy' and
                   node.get('runtime_version') == version for node in status['nodes'])

    wait(healthy, f'{node_name} upgraded registration', 60)
    request(manifest['leader'], '/v1/node-control', {'node_name': node_name, 'action': 'resume'})
    if all(item.get('runtime_version') == version or
           item.get('command', '').split('--runtime-version ', 1)[-1].split()[0] == version
           for item in manifest['workers']):
        manifest['runtime_version'] = version
        save(path, manifest)
    print(json.dumps({'upgraded_host': host, 'runtime_version': version,
                      'campaign_state': 'running', 'other_workers_preserved': len(manifest['workers']) - 1},
                     indent=2))


# Expand the table frontier while preserving previously submitted mathematical entries.
def extend(arguments: argparse.Namespace, path: Path) -> None:
    """Submit up to limit new prime powers under max_visits, retaining old runs and settings."""

    if arguments.limit <= 0 or arguments.max_visits <= 0:
        raise ValueError('campaign limits must be positive')
    manifest = json.loads(path.read_text())
    known = {(entry['specification']['arguments']['p'], entry['specification']['arguments']['r'])
             for entry in manifest['entries']}
    # Include manually submitted DP roots when avoiding repeat mathematical work.
    status = request(manifest['leader'], '/v1/status')
    for run in status['runs']:
        specification = json.loads(run['specification'])
        if specification.get('program') in {'dp', 'dp_distributed'}:
            known.add((specification['arguments']['p'], specification['arguments']['r']))
    submitted = []
    for specification in scheduling.campaign(16*1024**3, arguments.max_visits, 4, 512):
        parameters = specification['arguments']
        field = (parameters['p'], parameters['r'])
        if field in known:
            continue
        specification['program'] = 'dp_distributed'
        parameters.update(max_tile_bytes=2*1024**3, artifact_format='KHD1')
        result = request(manifest['leader'], '/v1/enqueue', {'specification': specification})
        entry = {'specification': specification, **result}
        manifest['entries'].append(entry)
        submitted.append({'p': field[0], 'r': field[1], 'run_id': result['run_id']})
        known.add(field)
        save(path, manifest)
        if len(submitted) >= arguments.limit:
            break
    manifest['max_visits'] = max(manifest.get('max_visits', 0), arguments.max_visits)
    save(path, manifest)
    print(json.dumps({'added_calculations': len(submitted), 'new_fields': submitted,
                      'total_calculations': len(manifest['entries'])}, indent=2))


# Copy completed compact artifacts to the leader for convenient mathematical review.
def collect(path: Path, manifest: dict) -> dict:
    """Collect this campaign's completed results, validating hashes and KHD1; return counts."""

    sys.path.insert(0, str(ROOT.parent/'dp_solver'))
    from artifacts import decode_dp
    status = request(manifest['leader'], '/v1/status')
    owned = {entry['run_id'] for entry in manifest['entries']}
    directory = path.parent/'results'
    directory.mkdir(exist_ok=True)
    index = {}
    index_path = directory/'index.json'
    if index_path.exists():
        index = json.loads(index_path.read_text())
    for run in status['runs']:
        if run['run_id'] not in owned or run['state'] != 'complete':
            continue
        specification = json.loads(run['specification'])
        p, r = (specification['arguments'][key] for key in ('p', 'r'))
        output = directory/f"{p}_{r}_{run['run_id']}.khdp"
        digest = run['artifact_hash']
        if output.exists():
            raw = output.read_bytes()
        else:
            locations = [run['artifact_location']]
            locations.extend(node['storage_url'].rstrip('/')+'/blobs/'+digest
                             for node in status['nodes'] if node.get('storage_url'))
            raw = None
            errors = []
            for location in dict.fromkeys(locations):
                try:
                    with urlopen(location, timeout=15) as response:
                        candidate = response.read(16*1024**2+1)
                    if len(candidate)>16*1024**2 or hashlib.sha256(candidate).hexdigest()!=digest:
                        raise ValueError('artifact size or hash mismatch')
                    raw = candidate
                    break
                except Exception as error:
                    errors.append(str(error))
            if raw is None:
                raise RuntimeError(f"cannot collect {run['run_id']}: {errors}")
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError(f'{output}: existing result hash mismatch')
        document = decode_dp(raw)
        if (document['p'], document['r'])!=(p, r):
            raise ValueError('result dimensions differ from queue specification')
        if not output.exists():
            temporary = output.with_name(output.name+'.tmp-'+uuid.uuid4().hex)
            try:
                with temporary.open('xb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
        index[run['run_id']] = {'p': p, 'r': r, 'theta': document['theta'],
            'sha256': digest, 'bytes': len(raw), 'file': output.name,
            'optimality_checked': False}
    save(index_path, index)
    descriptor = os.open(directory, os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {'collected_results': len(index), 'index': str(index_path)}


# Expose an explicit launch action and harmless help when invoked with no arguments.
def main() -> int:
    """Dispatch deployment, expansion, control and collection actions; return zero on success or one on failure."""

    parser = argparse.ArgumentParser(description=__doc__,
        epilog='Example: ./launch_dp.py start; ./launch_dp.py status; ./launch_dp.py stop')
    parser.add_argument('--state', type=Path, default=ROOT/'deployments'/'dp-campaign',
                        help='retained local leader database, log and manifest directory')
    commands = parser.add_subparsers(dest='action')
    launch = commands.add_parser('start', help='deploy and start a new retained campaign')
    launch.add_argument('--hosts', nargs='+', default=[
        *[f'192.168.4.{n}' for n in range(101, 109)], '192.168.4.151'])
    launch.add_argument('--address', default='192.168.4.151')
    launch.add_argument('--port', type=int, default=8041)
    launch.add_argument('--limit', type=int, default=100)
    launch.add_argument('--max-visits', type=int, default=3_000_000_000_000)
    attach = commands.add_parser('add-workers', help='attach workers to the existing campaign')
    attach.add_argument('--hosts', nargs='+', required=True)
    commands.add_parser('upgrade-workers',
                        help='replace idle worker runtimes and leave dispatch stopped')
    commands.add_parser('resume-workers',
                        help='start absent retained agents after a quiesce, preserving their storage')
    network = commands.add_parser('set-storage-addresses',
                                  help='record optional same-LAN blob addresses for retained agents')
    network.add_argument('--group', required=True)
    network.add_argument('assignments', nargs='+', metavar='HOST=IP')
    leader_upgrade = commands.add_parser('upgrade-leader',
                        help='restart the owned leader (idle by default, or guarded live restart)')
    leader_upgrade.add_argument('--live', action='store_true',
                                help='preserve active leases and running dispatch; refuse stale workers/leases')
    rolling = commands.add_parser('upgrade-worker-rolling',
                                  help='drain one node without stopping its active work, then upgrade it')
    rolling.add_argument('--host', required=True)
    rolling.add_argument('--wait-seconds', type=float, default=180)
    expansion = commands.add_parser('extend', help='add new prime powers without repeating existing entries')
    expansion.add_argument('--max-visits', type=int, default=30_000_000_000_000)
    expansion.add_argument('--limit', type=int, default=100)
    for name in ('status', 'stop', 'resume', 'collect', 'ensure-feeder', 'drain'):
        commands.add_parser(name)
    arguments = parser.parse_args()
    if arguments.action is None:
        parser.print_help()
        return 0
    path = arguments.state.resolve()/'manifest.json'
    try:
        if arguments.action == 'start':
            if arguments.limit <= 0 or arguments.max_visits <= 0:
                raise ValueError('campaign limits must be positive')
            start(arguments, path)
        elif arguments.action == 'add-workers':
            add_workers(arguments, path)
        elif arguments.action == 'upgrade-workers':
            upgrade_workers(path)
        elif arguments.action == 'resume-workers':
            resume_workers(path)
        elif arguments.action == 'set-storage-addresses':
            set_storage_addresses(path, arguments.group, arguments.assignments)
        elif arguments.action == 'upgrade-leader':
            if arguments.live:
                upgrade_leader_live(path)
            else:
                upgrade_leader(path)
        elif arguments.action == 'upgrade-worker-rolling':
            if not 0 < arguments.wait_seconds <= 3600:
                raise ValueError('wait-seconds must be from 1 to 3600')
            upgrade_worker_rolling(path, arguments.host, arguments.wait_seconds)
        elif arguments.action == 'extend':
            extend(arguments, path)
        elif arguments.action == 'ensure-feeder':
            print(json.dumps({"feeder_started": ensure_feeder(path.parent)}, indent=2))
        elif arguments.action == 'drain':
            print(json.dumps({"campaign_state": "stopped", "active_finishing":
                              drain_dispatch(path.parent / 'leader.sqlite')}, indent=2))
        else:
            manifest = json.loads(path.read_text())
            if arguments.action == 'collect':
                print(json.dumps(collect(path, manifest), indent=2))
            elif arguments.action == 'status':
                from kh import print_status
                print_status(request(manifest['leader'], '/v1/status'))
            else:
                state = 'stopped' if arguments.action == 'stop' else 'running'
                print(json.dumps(request(manifest['leader'], '/v1/control', {'state': state}), indent=2))
        return 0
    except Exception as error:
        print(f'launch_dp.py: {error}; retained launch state: {path}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
