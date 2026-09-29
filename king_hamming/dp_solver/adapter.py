"""DP lifecycle implementation behind the generic cluster adapter interface."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from adapters import SolverAdapter
from . import checkpoints, distributed, scheduling

ROOT = Path(__file__).resolve().parent.parent


class DPAdapter(SolverAdapter):
    """Own DP commands, checkpoints, work estimates, tile workflows and scratch layout."""

    programs = ('dp', 'dp_tile', 'dp_distributed')
    input_routes = ('/v1/tile-input', '/v1/adapter-input')

    def validate(self, specification, internal=False):
        """Validate DP dimensions; reject externally submitted internal tile jobs."""
        super().validate(specification, internal)
        scheduling.dp_estimate(specification)
        if specification['program']=='dp_tile' and not internal:
            raise ValueError('dp_tile is internal; enqueue dp_distributed instead')

    def describe(self, specification):
        """Return a concise prime-power label for a DP job."""
        arguments = specification["arguments"]
        return f"{arguments['p']}^{arguments['r']} {specification['program']}"

    def estimate(self, specification, rate):
        """Return conservative raw transition visits divided by configured rate."""
        return scheduling.dp_estimate(specification)['raw_visits']/rate

    def initialize(self, connection):
        """Initialize the DP tile DAG and backfill generic child ownership."""
        distributed.initialize(connection)
        connection.execute('UPDATE runs SET parent_run_id=(SELECT parent_run_id FROM distributed_tiles WHERE child_run_id=runs.run_id) WHERE parent_run_id IS NULL AND run_id IN (SELECT child_run_id FROM distributed_tiles WHERE child_run_id IS NOT NULL)')

    def enqueue(self, connection, run_id, specification, now):
        """Create the DP DAG when requested; return the root's initial state."""
        if specification['program']=='dp_distributed':
            distributed.create(connection, run_id, specification)
            distributed.advance(connection, now)
            return 'waiting'
        return 'queued'

    def advance(self, connection, now):
        """Advance DP tile dependencies using the leader transaction."""
        distributed.advance(connection, now)

    def inputs(self, connection, run, request, now):
        """Return only predecessor descriptors authorized for this leased DP task."""
        return distributed.inputs(connection, run, request, now)

    def worker_specification(self, specification, cpus):
        """Cap operational thread count to the replacement worker's assigned CPUs."""
        specification = json.loads(json.dumps(specification))
        arguments = specification.setdefault('arguments', {})
        arguments['threads'] = min(int(arguments.get('threads', 1)), len(cpus))
        return specification

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        """Return argv for the local C solver or leased distributed helper."""
        arguments = specification.get('arguments', {})
        if specification['program'] in {'dp_tile','dp_distributed'}:
            return [sys.executable, str(ROOT/'dp_solver/distributed_solver.py'), '--output', str(output)]
        return [str(ROOT/'dp_solver/kh_dp_local'), str(int(arguments['p'])), str(int(arguments['r'])),
            '--work-dir', str(output.parent/'dp-state'), '-o', str(output),
            '--tile-side', str(int(arguments.get('tile_side',4096))),
            '--threads', str(int(arguments.get('threads',1))),
            '--progress-milliseconds', str(int(arguments.get('progress_milliseconds',10000))),
            '--checkpoint-seconds', str(checkpoint_seconds),
            '--max-state-bytes', str(int(arguments.get('max_state_bytes',17179869184))),
            '--max-visits', str(int(arguments.get('max_visits',5000000000)))]

    def prepare(self, specification, directory, job, leader):
        """Write private distributed task input and return its fenced launch arguments."""
        if specification['program'] not in {'dp_tile','dp_distributed'}:
            return []
        task = directory/'task.json'
        task.write_text(json.dumps(specification))
        return ['--specification',str(task),'--leader',leader,'--run-id',job['run_id'],'--lease-token',job['lease_token']]

    def checkpoint_handshake(self, specification):
        """Return true; immutable helper jobs accept the flag without emitting snapshots."""
        return True

    def retry_elsewhere(self, specification):
        """Return true for immutable tile/reconstruction retries under fresh leases."""
        return specification['program'] in {'dp_tile','dp_distributed'}

    def cleanup(self, specification, directory):
        """Delete disposable native scratch only after durable completion acknowledgment."""
        if self.retry_elsewhere(specification):
            for name in ('tile-output','tile-inputs','reconstruction'):
                path = directory/name
                if path.is_dir():
                    shutil.rmtree(path)
            (directory/'halo.bin').unlink(missing_ok=True)

    def checkpoint_description(self, manifest, specification, require_native=False):
        """Validate native DP layout and return expected file sizes and coverage."""
        if specification['program']!='dp':
            raise ValueError('immutable DP jobs have no whole-state checkpoint')
        return checkpoints.describe(manifest, specification, require_native)

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        """Fill native layout/coverage and return the committed DP files."""
        if specification['program']!='dp':
            raise ValueError('immutable DP jobs have no whole-state checkpoint')
        dimensions = checkpoints.dp_dimensions(specification)
        manifest.update(parameters=dimensions, layout=checkpoints.native_layout(),
            done=checkpoints.covered_cells(dimensions,cursor), total=dimensions['budget']**2)
        return {name: directory/'dp-state'/name for name in ('values.bin','choices.bin','checkpoint.bin')}

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        """Check the native DP restart cursor against its portable manifest."""
        checkpoints.validate_metadata(manifest, paths, require_native)

    def checkpoint_destination(self, directory):
        """Return the mutable DP directory destination for generic atomic restoration."""
        return directory/'dp-state', None

    def runtime_files(self):
        """Return DP binaries and portable artifact codec needed on deployed workers."""
        package = [(source, 'dp_solver/'+source.name) for source in sorted((ROOT/'dp_solver').glob('*.py'))]
        return package + [(ROOT/'dp_solver'/name, 'dp_solver/'+name) for name in
                ('kh_dp_local','kh_estimate','kh_dp_tile')]

    def configure_campaign(self, parser):
        """Add DP frontier controls to the compatibility campaign command parser."""
        parser.add_argument('--distributed',action='store_true')
        parser.add_argument('--max-tile-bytes',type=int,default=2*1024**3)
        parser.add_argument('--submit',action='store_true')
        parser.add_argument('--max-state-bytes',type=int,default=scheduling.DEFAULT_STATE_BYTES)
        parser.add_argument('--max-visits',type=int,default=scheduling.DEFAULT_VISITS)
        parser.add_argument('--threads',type=int,default=1)
        parser.add_argument('--tile-side',type=int,default=4096)
        parser.add_argument('--limit',type=int,default=20)

    def campaign_entries(self, arguments):
        """Yield DP specifications and estimates selected by the supplied CLI arguments."""
        if arguments.limit<1:
            raise ValueError('campaign limit must be positive')
        entries=list(scheduling.campaign(arguments.max_state_bytes,arguments.max_visits,arguments.threads,arguments.tile_side))[:arguments.limit]
        for specification in entries:
            if arguments.distributed:
                specification['program']='dp_distributed'
                specification['arguments']['max_tile_bytes']=arguments.max_tile_bytes
            yield {'specification':specification,'estimate':scheduling.dp_estimate(specification)}

    def read_result(self, specification, output):
        """Read and validate compact DP output and its requested dimensions."""
        sys.path.insert(0, str(ROOT/'dp_solver'))
        from artifacts import decode_dp, validate
        if output.stat().st_size>16*1024**2:
            raise ValueError('DP artifact exceeds compact result size limit')
        raw=output.read_bytes()
        document=decode_dp(raw) if raw.startswith(b'KHD1') else json.loads(raw)
        validate(document)
        arguments=specification['arguments']
        if (document['p'],document['r']) != (arguments['p'],arguments['r']):
            raise ValueError('DP artifact dimensions differ from specification')
        return document

    def validate_result(self, specification, output):
        """Validate final split feasibility; immutable packet checks belong to the tile helper."""
        if specification['program']!='dp_tile':
            self.read_result(specification, output)

    def verify_result(self, specification, output, max_visits=200_000_000):
        """Independently recompute final DP optimum under max_visits; reject internal packets."""
        if specification['program']=='dp_tile':
            raise ValueError('verify a reconstructed DP split rather than an internal tile packet')
        document=self.read_result(specification, output)
        from verify_dp import verify
        verify(document, max_visits)
