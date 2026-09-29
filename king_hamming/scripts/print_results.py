#!/usr/bin/env python3
"""Print collected DP results as a compact Markdown table."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dp_solver.artifacts import decode_dp


# Read mathematical results rather than trusting a potentially stale summary index.
def load_results(source: Path) -> list[tuple[Path, dict]]:
    """Load source directory or index.json; return validated artifact paths and decoded DP results."""

    source = source.resolve()
    index_path = source/'index.json' if source.is_dir() else source
    if index_path.name == 'index.json' and index_path.is_file():
        index = json.loads(index_path.read_text())
        if not isinstance(index, dict):
            raise ValueError('result index must be an object')
        entries = [(index_path.parent/record['file'], record) for record in index.values()]
    elif source.is_dir():
        entries = [(path, None) for path in sorted(source.glob('*.khdp'))]
    else:
        raise ValueError('provide a results directory or its index.json')
    results = []
    for path, record in entries:
        if path.stat().st_size > 16*1024**2:
            raise ValueError(f'{path}: compact artifact exceeds size limit')
        raw = path.read_bytes()
        document = decode_dp(raw)
        if record is not None:
            if hashlib.sha256(raw).hexdigest() != record['sha256']:
                raise ValueError(f'{path}: hash differs from index')
            if (document['p'], document['r'], document['theta']) != (record['p'], record['r'], record['theta']):
                raise ValueError(f'{path}: result differs from index')
        results.append((path, document))
    return sorted(results, key=lambda result: (result[1]['q'], result[1]['p'], result[1]['r'], result[0].name))


# Keep links usable when redirecting output to a Markdown file in the current directory.
def markdown(results: list[tuple[Path, dict]], link_base: Path) -> str:
    """Return a Markdown table for results with artifact links relative to link_base."""

    lines = ['# DP results', '', f'{len(results)} retained results, ordered by field size.', '',
             '| p | r | q | θ | Predicted rows | Artifact |',
             '| ---: | ---: | ---: | ---: | ---: | --- |']
    for path, document in results:
        p, r, q, theta = (document[key] for key in ('p', 'r', 'q', 'theta'))
        predicted = q + theta * document['f']**2
        target = quote(os.path.relpath(path, link_base), safe='/.-_')
        lines.append(f'| {p} | {r} | {q} | {theta} | {predicted} | [DP]({target}) |')
    lines.extend(['', 'Predicted rows include the freebie: `q + θ F²`, where `F = p^floor(r/2)`.',
                  'Checksums and split feasibility are checked; DP optimality and bipartite matching are not independently verified by this utility.', ''])
    return '\n'.join(lines)


# Make no-argument use helpful, and keep normal execution read-only.
def main() -> int:
    """Parse command-line input and print the table; return zero on success or help."""

    parser = argparse.ArgumentParser(description=__doc__,
        epilog='Example: python3 king_hamming/scripts/print_results.py king_hamming/cluster/deployments/dp-campaign/results > results.md')
    parser.add_argument('results', type=Path, nargs='?', help='results directory or index.json')
    parser.add_argument('--link-base', type=Path, default=Path.cwd(),
                        help='directory where the Markdown report will live; defaults to the current directory')
    arguments = parser.parse_args()
    if arguments.results is None:
        parser.print_help()
        return 0
    print(markdown(load_results(arguments.results), arguments.link_base.resolve()), end='')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'print_results.py: {error}', file=sys.stderr)
        raise SystemExit(1)
