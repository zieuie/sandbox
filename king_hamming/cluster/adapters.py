"""Algorithm-neutral solver lifecycle and trusted adapter registry."""

from __future__ import annotations

import importlib
import math
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Supply no-op orchestration hooks while requiring algorithm-specific execution contracts.
class SolverAdapter:
    """Base contract for trusted solver implementations; parameters and return values are documented per hook."""

    programs = ()
    input_routes = ()

    def validate(self, specification, internal=False):
        """Validate specification and its external/internal admission; raise on unsupported input."""
        if not isinstance(specification.get('arguments', {}), dict):
            raise ValueError('solver arguments must be an object')

    def describe(self, specification):
        """Return a compact human-readable calculation label for operator status."""
        return specification.get("program", "unknown")

    def status_details(self, specification, run, status):
        """Return compact solver-owned status detail strings for one run."""
        return []

    def augment_status(self, connection, runs, now):
        """Attach solver-owned, read-only status fields to selected run rows."""

    def estimate(self, specification, rate):
        """Return estimated seconds for specification at configured rate; override in an adapter."""
        return 1e100

    def required_nodes(self, specification):
        """Return simultaneous compute-node slots, including the coordinator lease owner."""
        return 1

    def resource_requirements(self, specification):
        """Return coordinator/worker host memory and minimum logical CPUs."""
        return {"coordinator_memory_bytes": 0, "worker_memory_bytes": 0,
                "min_cpu_count": 1}

    def allows_host_sharing(self, specification):
        """Return whether disjoint CPU slots may run beside this single-node job."""
        return False

    def cpu_width(self, specification, available):
        """Choose a lease width from free host CPUs; sharing adapters default to one."""
        return min(1, available)

    def initialize(self, connection):
        """Initialize adapter tables using connection; return no value."""

    def enqueue(self, connection, run_id, specification, now):
        """Prepare run_id at enqueue time; return its initial queue state."""
        return 'queued'

    def resume_transition(self, connection, run, specification, now):
        """Return the durable state and progress phase for a paused run being resumed."""
        return 'queued', 'queued'

    def advance(self, connection, now):
        """Advance adapter workflows inside the leader transaction; return no value."""

    def inputs(self, connection, run, request, now):
        """Return authorized input descriptors for run, or reject unsupported requests."""
        raise ValueError('solver does not expose leased input descriptors')

    def locality_scores(self, connection, node_name, items, now):
        """Return {run_id: score} for queued runs whose inputs node_name already holds; higher is better.

        items is a list of (run_id, specification, created). The scores only break
        ties between runs the scheduler would otherwise treat alike, and an adapter
        should give a long-waiting run the top score so no run waits for locality
        indefinitely. Runs it has no opinion about are simply omitted.
        """
        return {}

    def worker_specification(self, specification, cpus):
        """Return worker-specific operational settings without changing mathematical identity."""
        return specification

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        """Return solver argv for requested files and checkpoint interval."""
        raise NotImplementedError('adapter must provide a solver command')

    def prepare(self, specification, directory, job, leader):
        """Prepare private inputs; return additional solver arguments."""
        return []

    def locality_args(self, specification, storage_root, storage_url, work_root):
        """Return optional trusted worker-local input paths for this solver."""
        return []

    def peer_command(self, specification, cpus, worker_index=1, worker_count=2):
        """Return a trusted peer-worker argv, or reject peer tunnels for this program."""
        raise ValueError("solver does not expose a peer worker")

    def checkpoint_handshake(self, specification):
        """Return whether solver uses the paused checkpoint acknowledgment protocol."""
        return False

    def retry_elsewhere(self, specification):
        """Return whether an engine retry should use a new lease instead of a local restart."""
        return False

    def cleanup(self, specification, directory):
        """Remove disposable scratch after acknowledged completion; return no value."""

    def validate_result(self, specification, output):
        """Validate completed output before publication; return no value."""

    def verify_result(self, specification, output, max_visits=200_000_000):
        """Verify output for manual review, subject to adapter work limits."""
        self.validate_result(specification, output)

    def checkpoint_description(self, manifest, specification, require_native=False):
        """Return expected file sizes, committed units and total units; validate solver layout."""
        raise ValueError('solver does not support checkpoint manifests')

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        """Fill manifest layout/coverage and return quiescent checkpoint file paths."""
        raise ValueError('solver does not support checkpoint capture')

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        """Validate restart metadata at paths against manifest; return no value."""
        raise ValueError('solver does not support checkpoint metadata')

    def checkpoint_destination(self, directory):
        """Return restore destination and optional single member; generic transport installs it."""
        raise ValueError('solver does not support checkpoint restoration')

    def runtime_files(self):
        """Return iterable of trusted (source_path, archive_name) runtime dependencies."""
        return []


_REGISTRY = {}
_LOADED = False
_LOAD_LOCK = threading.RLock()


# Register trusted adapters independently of queue, network and process code.
def register(adapter):
    """Register adapter for its program names; reject conflicting ownership."""

    with _LOAD_LOCK:
        for program in adapter.programs:
            if program in _REGISTRY and _REGISTRY[program] is not adapter:
                raise ValueError(f'duplicate solver adapter: {program}')
            _REGISTRY[program] = adapter



# Read the project composition outside the generic cluster package.
def load():
    """Load configured adapters once; return no value."""

    global _LOADED
    with _LOAD_LOCK:
        if not _LOADED:
            importlib.import_module('adapter_config').configure(register)
            if os.environ.get('KH_ENABLE_TEST_FIXTURES') == '1':
                fixture_root = Path(__file__).resolve().parent / 'tests' / 'fixtures'
                sys.path.insert(0, str(fixture_root))
                from demo_adapter import DemoAdapter
                register(DemoAdapter())
            _LOADED = True



# Resolve program ownership without embedding algorithm names in lifecycle code.
def get(specification):
    """Return the adapter for specification; reject unregistered program names."""

    load()
    program = specification.get('program')
    if not isinstance(program, str) or program not in _REGISTRY:
        raise ValueError(f'unsupported solver program: {program}')
    return _REGISTRY[program]


def all_adapters():
    """Return each configured adapter once, in registration order."""

    load()
    return list(dict.fromkeys(_REGISTRY.values()))


def estimate_seconds(specification, rate):
    """Return a finite nonnegative adapter estimate for specification and positive rate."""

    if not math.isfinite(rate) or rate <= 0:
        raise ValueError('visit rate must be finite and positive')
    adapter = get(specification)
    adapter.validate(specification, internal=True)
    result = adapter.estimate(specification, rate)
    if not math.isfinite(result) or result < 0:
        raise ValueError('invalid workload estimate')
    return result


def initialize(connection):
    """Initialize all adapters with connection; return no value."""

    for adapter in all_adapters():
        adapter.initialize(connection)


def advance(connection, now):
    """Advance all registered workflows within the caller's transaction; return no value."""

    for adapter in all_adapters():
        adapter.advance(connection, now)


def augment_status(connection, runs, now):
    """Let adapters add bounded metadata without coupling the leader to algorithms."""

    for adapter in all_adapters():
        adapter.augment_status(connection, runs, now)


def input_route(route):
    """Return whether route belongs to any configured leased input endpoint."""

    return any(route in adapter.input_routes for adapter in all_adapters())


def runtime_files():
    """Return all adapter runtime dependencies for deployment bundling."""

    return [item for adapter in all_adapters() for item in adapter.runtime_files()]


def default_campaign():
    """Return the configured campaign adapter for compatibility operator commands."""

    config = importlib.import_module('adapter_config')
    return get({'program': config.DEFAULT_CAMPAIGN_PROGRAM})
