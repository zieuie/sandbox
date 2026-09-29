#!/usr/bin/env python3
"""Verify algorithm-neutral lifecycle hooks and a self-contained deployed runtime."""

from __future__ import annotations

import inspect
import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import adapters
import checkpoints
import leader
from adapters import SolverAdapter
from common import calculation_id
from deployment import bundle
from test_integration import free_port, request_json, wait_until, find_run


# Exercise a different algorithm without borrowing any DP layout or command assumptions.
class ToyAdapter(SolverAdapter):
    """A tiny independent counter solver and single-file restart layout."""

    programs = ('toy_test',)

    def estimate(self, specification, rate):
        """Return the requested counter value as a deterministic runtime estimate."""
        return specification['arguments']['value']

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        """Return fixed argv that writes the requested value to output."""
        code = "import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps({'value':int(sys.argv[2])}))"
        return [sys.executable, '-c', code, str(output), str(specification['arguments']['value'])]

    def validate_result(self, specification, output):
        """Reject output whose counter differs from the requested value."""
        if json.loads(output.read_text()) != {'value': specification['arguments']['value']}:
            raise ValueError('counter result differs from request')

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        """Fill coverage and return the counter's single checkpoint file."""
        manifest.update(done=cursor, total=specification['arguments']['value'])
        return {'counter.txt': directory/'counter.txt'}

    def checkpoint_description(self, manifest, specification, require_native=False):
        """Return bounded portable counter checkpoint members and coverage."""
        return {'counter.txt': 1}, manifest['cursor'], specification['arguments']['value']

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        """Check the saved counter against the manifest cursor."""
        if int(paths['counter.txt'].read_text()) != manifest['cursor']:
            raise ValueError('counter checkpoint cursor mismatch')

    def checkpoint_destination(self, directory):
        """Return the single counter restore destination and member."""
        return directory/'counter.txt', 'counter.txt'


# Check actual transactions and checkpoint transport through a newly registered adapter.
class AdapterTests(unittest.TestCase):
    """Generic queue and storage work with an unrelated algorithm and packaged DP."""

    def test_new_algorithm_queue_and_checkpoint_roundtrip(self):
        """Use a new program, filename and cursor without changing generic lifecycle code."""
        adapters.load()
        with patch.dict(adapters._REGISTRY), tempfile.TemporaryDirectory() as temporary:
            adapters.register(ToyAdapter())
            root = Path(temporary)
            database = root/'leader.sqlite'
            leader.initialize(database,1800)
            handler = object.__new__(leader.make_handler(database))
            specification = {'program':'toy_test','arguments':{'value':4}}
            queued = handler.dispatch_post('/v1/enqueue',{'specification':specification})
            handler.dispatch_post('/v1/register',{'node_name':'toy-worker'})
            job = handler.dispatch_post('/v1/lease',{'node_name':'toy-worker'})['job']
            self.assertEqual(job['run_id'],queued['run_id'])
            with leader.connect(database) as connection:
                self.assertEqual(connection.execute('SELECT estimated_seconds FROM runs WHERE run_id=?',(job['run_id'],)).fetchone()[0],4)
            source = root/'source'
            source.mkdir()
            (source/'counter.txt').write_text('2')
            manifest = checkpoints.capture_checkpoint(specification,job['run_id'],source,root/'blobs',2)
            task = {'manifest':manifest,'manifest_hash':calculation_id(manifest),'sources':[]}
            target = root/'target'
            checkpoints.restore_checkpoint(task,specification,job['run_id'],target,root/'blobs')
            self.assertEqual((target/'counter.txt').read_text(),'2')
            self.assertFalse((target/'dp-state').exists())
            with self.assertRaises(ValueError):
                adapters.get({'program':'unregistered'})

    def test_campaign_extension_deduplicates_mathematical_entries(self):
        """Retain old settings, skip manual queued fields, and make a repeated extension idempotent."""
        from dp_solver import launch_dp, scheduling
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            database=root/'leader.sqlite'
            leader.initialize(database,1800)
            handler=object.__new__(leader.make_handler(database))
            candidates=list(scheduling.campaign(max_visits=30_000_000_000_000,threads=4,tile_side=512))[:3]
            original=copy.deepcopy(candidates[0])
            original['program']='dp_distributed'
            original['arguments'].update(artifact_format='KHD1',max_visits=3_000_000_000_000)
            queued=handler.dispatch_post('/v1/enqueue',{'specification':original})
            old_entry={'specification':original,**queued}
            manual=copy.deepcopy(candidates[2])
            manual['program']='dp_distributed'
            handler.dispatch_post('/v1/enqueue',{'specification':manual})
            path=root/'manifest.json'
            launch_dp.save(path,{'leader':'http://private.test','entries':[old_entry],'max_visits':3_000_000_000_000})

            def request(base,route,value=None):
                """Use actual enqueue transactions and a private queue snapshot without network access."""
                if route=='/v1/status':
                    with leader.connect(database) as connection:
                        return {'runs':[dict(row) for row in connection.execute('SELECT specification FROM runs WHERE parent_run_id IS NULL')]}
                return handler.dispatch_post(route,value)

            arguments=argparse.Namespace(limit=100,max_visits=30_000_000_000_000)
            with patch.object(launch_dp,'request',side_effect=request), patch.object(launch_dp.scheduling,'campaign',side_effect=lambda *args:copy.deepcopy(candidates)), contextlib.redirect_stdout(io.StringIO()):
                launch_dp.extend(arguments,path)
                launch_dp.extend(arguments,path)
            manifest=json.loads(path.read_text())
            self.assertEqual(len(manifest['entries']),2)
            self.assertEqual(manifest['entries'][0],old_entry)
            self.assertEqual(manifest['entries'][1]['specification']['arguments']['artifact_format'],'KHD1')
            with leader.connect(database) as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM runs WHERE parent_run_id IS NULL').fetchone()[0],3)

    def test_bundled_runtime_accepts_another_solver_and_distributed_dp(self):
        """Run copied generic services with a new configured adapter and two DP workers."""
        with tempfile.TemporaryDirectory(prefix='kh-adapter-bundle-') as temporary:
            root = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(bundle())) as archive:
                archive.extractall(root,filter='data')
            (root/'toy_adapter.py').write_text('import json\nimport sys\nfrom adapters import SolverAdapter\n\n'+inspect.getsource(ToyAdapter))
            config = root/'adapter_config.py'
            config.write_text(config.read_text()+"\n    from toy_adapter import ToyAdapter\n    register(ToyAdapter())\n")
            base = f'http://127.0.0.1:{free_port()}'
            processes = []
            logs = []
            try:
                def start(name, arguments):
                    """Start only a private copied service and retain its diagnostic log."""
                    stream = (root/(name+'.log')).open('wb')
                    logs.append(stream)
                    process = subprocess.Popen([sys.executable,*arguments],stdout=stream,stderr=stream)
                    processes.append(process)
                    return process

                start('leader',[str(root/'cluster/leader.py'),'serve','--database',str(root/'leader.sqlite'),
                    '--listen',base.removeprefix('http://'),'--lease-seconds','10'])

                def ready():
                    """Return true when the private copied leader accepts requests."""
                    try:
                        return request_json(base,'GET','/v1/health')['ok']
                    except OSError:
                        return False

                wait_until(ready,'bundled leader startup')
                cpu = str(sorted(os.sched_getaffinity(0))[-1])
                for number in range(2):
                    port = free_port()
                    start(f'worker-{number}',[str(root/'cluster/agent.py'),'run','--leader',base,
                        '--name',f'bundle-{number}','--cpus',cpu,'--work-root',str(root/f'work-{number}'),
                        '--storage-root',str(root/f'blobs-{number}'),'--storage-listen',f'127.0.0.1:{port}',
                        '--storage-url',f'http://127.0.0.1:{port}','--poll-seconds','0.1','--control-seconds','0.1'])
                wait_until(lambda:len(request_json(base,'GET','/v1/status')['nodes'])==2,'bundled agents')
                toy = {'program':'toy_test','arguments':{'value':42}}
                dp = {'program':'dp_distributed','arguments':{'p':3,'r':3,'tile_side':4,'threads':1,'artifact_format':'KHD1'}}
                for specification in (toy,dp):
                    queued = request_json(base,'POST','/v1/enqueue',{'specification':specification})
                    def complete():
                        """Return this job's completed row, raising immediately on algorithm failure."""
                        row=find_run(base,queued['run_id'])
                        if row['state']=='failed':
                            raise AssertionError(row['error'])
                        return row if row['state']=='complete' else None
                    row=wait_until(complete,'bundled solver completion',timeout=45)
                    output=root/(specification['program']+'.bin')
                    with urlopen(row['artifact_location'],timeout=10) as response:
                        output.write_bytes(response.read())
                    spec=root/(specification['program']+'.json')
                    spec.write_text(json.dumps(specification))
                    result=subprocess.run([sys.executable,str(root/'cluster/verify_artifact.py'),str(spec),str(output)],capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
            finally:
                for process in reversed(processes):
                    process.terminate()
                for process in reversed(processes):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                for stream in logs:
                    stream.close()


if __name__=='__main__':
    if '--run' not in sys.argv:
        print('Test adapter isolation and deployment.\nExample: python3 tests/test_adapters.py --run')
    else:
        sys.argv.remove('--run')
        unittest.main()
