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
import socket
import subprocess
import sys
import uuid
from urllib.request import Request, urlopen

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
def launch_worker(host: str, directory: str, leader: str) -> dict:
    """Start one agent on host; return its exact command and process identity."""

    code = '''import json,pathlib,shlex,socket,subprocess,sys
root=pathlib.Path(sys.argv[1])
with socket.socket() as listener:
    listener.bind(('0.0.0.0',0))
    port=listener.getsockname()[1]
arguments=[sys.executable,str(root/'cluster'/'agent.py'),'run','--leader',sys.argv[2],
    '--name','dp-'+sys.argv[3].rsplit('.',1)[1],'--cpus','auto',
    '--work-root',str(root/'work'),'--storage-root',str(root/'blobs'),
    '--storage-listen',f'0.0.0.0:{port}','--storage-url',f'http://{sys.argv[3]}:{port}']
with (root/'agent.log').open('ab') as log:
    process=subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
start=pathlib.Path(f'/proc/{process.pid}/stat').read_text().split()[21]
record={'host':sys.argv[3],'root':str(root),'pid':process.pid,'start':start,'port':port,'command':shlex.join(arguments)}
(root/'process.json').write_text(json.dumps(record,indent=2)+'\\n')
print(json.dumps(record))
'''
    return remote(host, code, [directory, leader, host])


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
    subprocess.run(['make', '-C', str(ROOT.parent/'dp_solver'), 'all'], check=True)
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
    for host in arguments.hosts:
        directory = str(Path(facts[host]['home'])/'.local/share/king_hamming'/identifier)
        remote(host, "import json,pathlib,sys; pathlib.Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True); print(json.dumps({'ok':True}))", [directory])
        deploy(host, directory, archive)
        record = launch_worker(host, directory, base)
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
    subprocess.run(['make', '-C', str(ROOT.parent/'dp_solver'), 'all'], check=True)
    archive = bundle()
    for host in hosts:
        directory = str(Path(facts[host]['home'])/'.local/share/king_hamming'/manifest['id'])
        manifest.setdefault('preflight', {})[host] = facts[host]
        save(path, manifest)
        remote(host, "import json,pathlib,sys; pathlib.Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True); print(json.dumps({'ok':True}))", [directory])
        deploy(host, directory, archive)
        manifest['workers'].append(launch_worker(host, directory, manifest['leader']))
        save(path, manifest)
    print(json.dumps({'added_workers': len(hosts), 'total_workers': len(manifest['workers']),
                      'manifest': str(path)}, indent=2))


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
    launch.add_argument('--hosts', nargs='+', default=[f'192.168.4.{n}' for n in range(101,106)])
    launch.add_argument('--address', default='192.168.4.151')
    launch.add_argument('--port', type=int, default=8041)
    launch.add_argument('--limit', type=int, default=100)
    launch.add_argument('--max-visits', type=int, default=3_000_000_000_000)
    attach = commands.add_parser('add-workers', help='attach workers to the existing campaign')
    attach.add_argument('--hosts', nargs='+', required=True)
    expansion = commands.add_parser('extend', help='add new prime powers without repeating existing entries')
    expansion.add_argument('--max-visits', type=int, default=30_000_000_000_000)
    expansion.add_argument('--limit', type=int, default=100)
    for name in ('status', 'stop', 'resume', 'collect'):
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
        elif arguments.action == 'extend':
            extend(arguments, path)
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
