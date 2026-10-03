"""DP lifecycle implementation behind the generic cluster adapter interface."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys

from adapters import SolverAdapter
from . import checkpoints, distributed, scheduling
from .tiles import dependencies, memory_bytes, tile

ROOT = Path(__file__).resolve().parent.parent


class DPAdapter(SolverAdapter):
    """Own DP commands, checkpoints, work estimates, tile workflows and scratch layout."""

    programs = ('dp', 'dp_tile', 'dp_distributed')
    input_routes = ('/v1/tile-input', '/v1/adapter-input')

    def validate(self, specification, internal=False):
        """Validate DP dimensions; reject externally submitted internal tile jobs."""
        super().validate(specification, internal)
        scheduling.dp_estimate(specification)
        cap = specification.get('arguments', {}).get('max_cpus', 1024)
        if type(cap) is not int or not 1 <= cap <= 1024:
            raise ValueError('max_cpus must be an integer from 1 to 1024')
        if specification['program']=='dp_tile' and not internal:
            raise ValueError('dp_tile is internal; enqueue dp_distributed instead')

    def describe(self, specification):
        """Return a concise prime-power label for a DP job."""
        arguments = specification["arguments"]
        return f"{arguments['p']}^{arguments['r']} {specification['program']}"

    def status_details(self, specification, run, status):
        """Describe DP state size and durable tile frontier without coupling the CLI to DP."""
        estimate = scheduling.dp_estimate(specification)
        details = [f"permutation=rows-pending-DP x {estimate['q'] + 1:,}; "
                   f"DP-state={_byte_size(estimate['state_bytes'])}"]
        if specification["program"] == "dp_distributed":
            counts = run.get("tile_counts")
            if counts:
                details.append(
                    f"tiles={counts['durable']} durable, {counts['running']} running, "
                    f"{counts['ready']} ready, {counts['blocked']} blocked, "
                    f"{counts['failed']} failed; boundary={counts['boundary']}")
            else:
                match = re.search(r"(\d+)/(\d+) replicated tiles",
                                  run.get("progress_message") or "")
                if match:
                    complete, total = map(int, match.groups())
                    active = sum(child.get("parent_run_id") == run["run_id"] and
                                 child.get("state") == "running"
                                 for child in status.get("runs", []))
                    details.append(
                        f"tiles={complete} complete, {total - complete} pending, {active} active")
        return details

    def augment_status(self, connection, runs, now):
        """Attach durable/ready/blocked tile counts and the first dependency boundary."""
        lease_seconds = float(connection.execute(
            "SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
        for run in runs:
            try:
                specification = json.loads(run["specification"])
            except (TypeError, ValueError):
                continue
            if specification.get("program") != "dp_distributed":
                continue
            arguments = specification["arguments"]
            p, r = int(arguments["p"]), int(arguments["r"])
            side = int(arguments.get("tile_side", 4096))
            rows = connection.execute(
                "SELECT t.row,t.column,t.child_run_id,child.state,child.artifact_hash "
                "FROM distributed_tiles t LEFT JOIN runs child ON child.run_id=t.child_run_id "
                "WHERE t.parent_run_id=? ORDER BY t.row,t.column", (run["run_id"],),
            ).fetchall()
            if not rows:
                continue
            durable = set()
            for item in rows:
                if item["state"] != "complete" or not item["artifact_hash"]:
                    continue
                copies = connection.execute(
                    "SELECT COUNT(*) FROM replicas replica JOIN nodes USING(node_name) "
                    "WHERE replica.artifact_hash=? AND nodes.last_heartbeat>?",
                    (item["artifact_hash"], now - lease_seconds),
                ).fetchone()[0]
                if copies >= 2:
                    durable.add((item["row"], item["column"]))
            counts = {"durable": len(durable), "running": 0, "ready": 0,
                      "blocked": 0, "failed": 0, "boundary": "none"}
            boundary = None
            for item in rows:
                key = (item["row"], item["column"])
                if key in durable:
                    continue
                if item["state"] == "running":
                    counts["running"] += 1
                elif item["state"] == "failed":
                    counts["failed"] += 1
                elif item["child_run_id"] is not None:
                    counts["ready"] += 1
                else:
                    target = tile(p, r, side, item["row"], item["column"])
                    missing = [(dep.row, dep.column) for dep in dependencies(p, r, side, target)
                               if (dep.row, dep.column) not in durable]
                    if missing:
                        counts["blocked"] += 1
                        if boundary is None:
                            boundary = (f"{item['row']},{item['column']} waits "
                                        f"{missing[0][0]},{missing[0][1]}")
                    else:
                        counts["ready"] += 1
            counts["boundary"] = boundary or "clear"
            run["tile_counts"] = counts

    def estimate(self, specification, rate):
        """Return conservative raw transition visits divided by configured rate."""
        return scheduling.dp_estimate(specification)['raw_visits']/rate

    def resource_requirements(self, specification):
        """Reserve the configured upper bound for an immutable tile or reconstruction."""
        arguments = specification.get("arguments", {})
        maximum = (int(arguments.get("max_tile_bytes", 2 * 1024**3))
                   if specification["program"] in {"dp_tile", "dp_distributed"}
                   else int(arguments.get("max_state_bytes", 16 * 1024**3)))
        return {"coordinator_memory_bytes": maximum,
                "worker_memory_bytes": maximum, "min_cpu_count": 1}

    def allows_host_sharing(self, specification):
        """Allow immutable tile kernels, but not roots/reconstruction, on disjoint slots."""
        return specification["program"] == "dp_tile"

    def cpu_width(self, specification, available):
        """Use spare cores, bounded by the halo, native stacks and an optional cap.

        Queue-time threads estimates admission, not the replacement host's size.
        Each lease's exact CPU team stays fixed until completion or revocation.
        """
        arguments = specification["arguments"]
        target = tile(arguments["p"], arguments["r"], arguments["tile_side"],
                      arguments["row"], arguments["column"])
        maximum = int(arguments.get("max_tile_bytes", 2 * 1024**3))
        fixed = memory_bytes(arguments["p"], target, 1) - 8 * 1024**2
        stacks = max(0, (maximum - fixed) // (8 * 1024**2))
        # Input fetches and durable publication hold the lease but use little CPU.
        # Keep several independent tiles in flight per host by default; callers
        # can still opt into a wider team for a measured compute-bound field.
        return min(available, stacks, int(arguments.get("max_cpus", 2)))

    def initialize(self, connection):
        """Initialize tile state, ownership, and repair legacy queued root transitions."""
        distributed.initialize(connection)
        connection.execute('UPDATE runs SET parent_run_id=(SELECT parent_run_id FROM distributed_tiles WHERE child_run_id=runs.run_id) WHERE parent_run_id IS NULL AND run_id IN (SELECT child_run_id FROM distributed_tiles WHERE child_run_id IS NOT NULL)')

        # One-time compatibility tagging for rows written by pre-outcome agents.
        # All current supervision and repair decisions use the typed column.
        connection.execute(
            "UPDATE runs SET failure_kind='stop_failure' "
            "WHERE failure_kind IS NULL AND state='failed' "
            "AND error LIKE '%solver failed while stopping%'"
        )

        # Older agents could wrap a tile kernel's normal stop-time SIGTERM as
        # exit 1. Preserve every completed tile and release only those
        # explicitly stop-induced failed children back to the DAG scheduler.
        candidates = connection.execute(
            "SELECT DISTINCT parent.run_id,parent.calculation_id,parent.created,"
            "(SELECT COUNT(*) FROM distributed_tiles completed_tile "
            "JOIN runs completed_child ON completed_child.run_id=completed_tile.child_run_id "
            "WHERE completed_tile.parent_run_id=parent.run_id "
            "AND completed_child.state='complete') completed "
            "FROM runs parent JOIN distributed_tiles t "
            "ON t.parent_run_id=parent.run_id JOIN runs failed "
            "ON failed.run_id=t.child_run_id "
            "WHERE parent.state='failed' AND failed.state='failed' AND "
            "failed.failure_kind='stop_failure'").fetchall()
        best_by_calculation = {}
        for row in candidates:
            key = row["calculation_id"]
            score = (int(row["completed"]), float(row["created"]))
            if key not in best_by_calculation or score > best_by_calculation[key][0]:
                best_by_calculation[key] = (score, row["run_id"])
        interrupted_parents = [value[1] for value in best_by_calculation.values()]
        for parent_run_id in interrupted_parents:
            connection.execute(
                "UPDATE distributed_tiles SET child_run_id=NULL "
                "WHERE parent_run_id=? AND child_run_id IN "
                "(SELECT run_id FROM runs WHERE state='failed' "
                "AND failure_kind='stop_failure')", (parent_run_id,))
            remaining_failure = connection.execute(
                "SELECT 1 FROM distributed_tiles t JOIN runs child "
                "ON child.run_id=t.child_run_id WHERE t.parent_run_id=? "
                "AND child.state='failed' LIMIT 1", (parent_run_id,)).fetchone()
            if remaining_failure is None:
                connection.execute(
                    "UPDATE runs SET state='waiting',finished=NULL,error=NULL,"
                    "stop_requested=0,progress_phase='tiles',"
                    "progress_message='recovering interrupted tile frontier' "
                    "WHERE run_id=? AND state='failed'", (parent_run_id,))

        for run in connection.execute(
                "SELECT run_id,specification FROM runs WHERE state='queued' AND parent_run_id IS NULL"
        ).fetchall():
            try:
                specification = json.loads(run["specification"])
            except (TypeError, ValueError):
                continue
            if specification.get("program") == "dp_distributed" and connection.execute(
                    "SELECT 1 FROM distributed_tiles WHERE parent_run_id=? LIMIT 1",
                    (run["run_id"],)).fetchone() is not None:
                connection.execute(
                    "UPDATE runs SET state='waiting',progress_phase='tiles',"
                    "progress_message='reconciling durable tile frontier' WHERE run_id=?",
                    (run["run_id"],),
                )

    def enqueue(self, connection, run_id, specification, now):
        """Create the DP DAG when requested; return the root's initial state."""
        if specification['program']=='dp_distributed':
            distributed.create(connection, run_id, specification)
            distributed.advance(connection, now)
            return 'waiting'
        return 'queued'

    def resume_transition(self, connection, run, specification, now):
        """Return incomplete distributed roots to their dependency scheduler."""
        if specification['program']=='dp_distributed':
            return 'waiting', 'tiles'
        return super().resume_transition(connection, run, specification, now)

    def advance(self, connection, now):
        """Advance DP tile dependencies using the leader transaction."""
        distributed.advance(connection, now)

    def inputs(self, connection, run, request, now):
        """Return only predecessor descriptors authorized for this leased DP task."""
        return distributed.inputs(connection, run, request, now)

    def locality_scores(self, connection, node_name, items, now):
        """Prefer tiles whose left (then upper) predecessor this node produced or already stores."""
        return distributed.locality_scores(connection, node_name, items, now)

    def worker_specification(self, specification, cpus):
        """Cap operational thread count to the replacement worker's assigned CPUs."""
        specification = json.loads(json.dumps(specification))
        arguments = specification.setdefault('arguments', {})
        arguments['threads'] = (len(cpus) if specification['program'] == 'dp_tile'
                                else min(int(arguments.get('threads', 1)), len(cpus)))
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

    def locality_args(self, specification, storage_root, storage_url, work_root):
        """Allow distributed tasks to reuse this worker's verified input packets."""
        if specification['program'] not in {'dp_tile', 'dp_distributed'}:
            return []
        return ['--local-storage-root', str(storage_root),
                '--local-storage-url', storage_url,
                '--shared-cache-root', str(work_root / '.dependency-cache')]

    def checkpoint_handshake(self, specification):
        """Return true; immutable helper jobs accept the flag without emitting snapshots."""
        return True

    def retry_elsewhere(self, specification):
        """Return true for immutable tile/reconstruction retries under fresh leases."""
        return specification['program'] in {'dp_tile','dp_distributed'}

    def cleanup(self, specification, directory):
        """Delete disposable native scratch only after durable completion acknowledgment."""
        if self.retry_elsewhere(specification):
            for name in ('tile-output','tile-inputs','reconstruction','bands'):
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


def _byte_size(value: int) -> str:
    """Format an adapter-owned binary byte count for operator status."""
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f}{unit}" if unit != "B" else f"{value}B"
        amount /= 1024
    raise AssertionError("unreachable")
