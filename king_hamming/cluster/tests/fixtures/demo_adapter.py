"""Deterministic test solver adapter for exercising the generic cluster lifecycle."""

import json
from pathlib import Path
import sys

from adapters import SolverAdapter


class DemoAdapter(SolverAdapter):
    """Own the demonstration command and its tiny portable checkpoint."""

    programs = ('demo',)

    def estimate(self, specification, rate):
        """Return steps times delay for specification; rate is unused."""
        arguments = specification.get('arguments', {})
        return int(arguments.get('steps', 10)) * float(arguments.get('delay', 0.1))

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        """Return demo argv using output/checkpoint paths; interval is unused."""
        arguments = specification.get('arguments', {})
        command = [sys.executable, str(Path(__file__).with_name('demo_solver.py')),
            '--steps', str(int(arguments.get('steps', 10))), '--delay', str(float(arguments.get('delay', 0.1))),
            '--checkpoint', str(checkpoint), '--output', str(output)]
        if 'fail_once_at' in arguments:
            command.extend(['--fail-once-at', str(int(arguments['fail_once_at']))])
        return command

    def checkpoint_handshake(self, specification):
        """Return true because the demo acknowledges immutable capture over stdin."""
        return True

    def checkpoint_description(self, manifest, specification, require_native=False):
        """Validate demo cursor and member bound; return sizes and coverage."""
        total = int(specification.get('arguments', {}).get('steps', 10))
        cursor = manifest['cursor']
        if not 0 <= cursor <= total:
            raise ValueError('invalid demo checkpoint cursor')
        if any(record['size'] > 1024 for record in manifest.get('files', [])):
            raise ValueError('demo checkpoint too large')
        return {'solver.checkpoint.json': None}, cursor, total

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        """Fill demo coverage and return its quiescent checkpoint path."""
        manifest.update(done=cursor, total=int(specification.get('arguments', {}).get('steps', 10)))
        return {'solver.checkpoint.json': directory/'solver.checkpoint.json'}

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        """Check the demo next-step record against the committed cursor."""
        if json.loads(paths['solver.checkpoint.json'].read_bytes()) != {'next_step': manifest['cursor']+1}:
            raise ValueError('demo checkpoint metadata disagrees with manifest')

    def checkpoint_destination(self, directory):
        """Return the single-file demo restore destination and its member name."""
        return directory/'solver.checkpoint.json', 'solver.checkpoint.json'

    def validate_result(self, specification, output):
        """Check the completed demo steps against the requested specification."""
        result = json.loads(output.read_text())
        expected = int(specification.get('arguments', {}).get('steps', 10))
        if not isinstance(result, dict) or set(result) != {'steps'} or int(result['steps']) != expected:
            raise ValueError('demo artifact step count or fields are incorrect')
