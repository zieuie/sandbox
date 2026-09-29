#!/usr/bin/env python3
"""Print a binary field/matching artifact, resolving its saved DP dependency."""

import argparse
from pathlib import Path
import sys

from inspect_artifact import Invalid, read_dp, read_matching, verify_dp


def polynomial_text(coefficients):
    """Format coefficients as a polynomial in descending degree order.

    Parameters:
        coefficients: Input sequence of nonnegative coefficients, low degree first.
    Returns:
        A readable polynomial string; zero is represented as '0'.
    """
    terms = []

    # Omit zero terms and suppress coefficient one on nonconstant terms.
    for degree in range(len(coefficients) - 1, -1, -1):
        coefficient = coefficients[degree]

        # Zero coefficients contribute no term.
        if coefficient == 0:
            continue

        # Constants need no variable; other terms identify their degree.
        if degree == 0:
            terms.append(str(coefficient))
        else:
            power = 'X' if degree == 1 else f'X^{degree}'
            terms.append(power if coefficient == 1 else f'{coefficient}*{power}')

    return ' + '.join(terms) or '0'


def print_matching(result, dp, summary=False, sets=False):
    """Print field data, decoded assignments, and optional P/Q sets.

    Parameters:
        result: Hydrated, verified dictionary from read_matching; not modified.
        dp: Decoded dependency, used to report DP verification status.
        summary: If true, omit individual assignments and full Hall sets.
        sets: If true, also print P/Q sets for a successful construction.
    Returns:
        None; writes the report to stdout.
    """

    # Separate certificate verification from optional DP optimality verification.
    print(f"Matching: GF({result['p']}^{result['r']}) = GF({dp['q']})")
    print(f"Format: {result['format']}    File size: {result['bytes']} bytes")
    print(f"Primitive polynomial: {polynomial_text(result['polynomial_low_first'])}")
    print('Polynomial coefficients (low degree first): ' +
          ', '.join(map(str, result['polynomial_low_first'])))
    print('Generator: X (field label 2)')
    print(f"Status: {result['status']}    Matched: {result['matched']}/{result['required']}")
    print('Integrity, DP reference, field, and matching/Hall certificate: checked')
    status = 'checked' if dp['optimality_checked'] else 'not checked (use --verify)'
    print(f'DP optimality and tie rule: {status}')
    print(f"DP SHA-256: {result['dp_sha256']}")

    # Obstructions have a deficiency proof rather than a constructed row count.
    if result['status'] == 'full_matching':
        print(f"Construction rows including freebie: {result['claimed_rows']}")
        print(f"Freebie coset: C{result['freebie_coset']}")
    else:
        hall = result['hall']
        print(f"Hall witness: |S|={hall['left_count']}, |N(S)|={hall['neighbor_count']}, "
              f"deficiency={hall['deficiency']}")

    # The suffix column uses the paper's base-p integer suffix index.
    if not summary:
        print('\nLeft indices, coset indices, and suffix indices are zero-based.')
        print('Positions are field labels; a dash means the request is unmatched.')
        print(f"{'Left':>6} {'Coset':>7} {'Prefix':>7} {'Suffix':>7} {'Position':>10}")

        # Preserve the artifact's canonical left-vertex ordering.
        for index, entry in enumerate(result['matching']):
            coset, prefix, suffix = entry['left']
            position = '-' if entry['right'] is None else str(entry['right'])
            print(f'{index:6} {coset:7} {prefix:7} {suffix:7} {position:>10}')

        # Show the actual deficient set and its complete neighborhood.
        if result['status'] == 'obstruction':
            print('\nS (left indices): ' + ', '.join(map(str, result['hall']['left_indices'])))
            print('N(S) (field labels): ' + ', '.join(map(str, result['hall']['neighbors'])))

    # Expanded position and symbol sets are derived from the same certificate.
    if sets and result['status'] == 'full_matching':
        print('\nPosition and symbol sets (field labels):')

        # Group both sides by their common coset index for hand inspection.
        for coset, (positions, symbols) in enumerate(zip(result['position_sets'], result['symbol_sets'])):
            print(f"P{coset} = {{{', '.join(map(str, positions))}}}")
            print(f"Q{coset} = {{{', '.join(map(str, symbols))}}}")


def main():
    """Decode and validate the selected files, then print the matching report.

    Parameters: none; reads sys.argv.
    Returns:
        0 for help or a valid printed artifact, including a certified obstruction;
        1 for invalid input or file errors. argparse exits with status 2 on bad syntax.
    """

    # Require an explicit DP path rather than guessing from filenames.
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Example: python3 print_matching.py examples/5_3.khmatch --dp examples/5_3.khdp\n'
               'With sets: python3 print_matching.py examples/3_3.khmatch --dp examples/3_3.khdp --sets\n'
               'Redirect stdout to save a text report. No arguments prints this help.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('artifact', type=Path, help='binary .khmatch file')
    parser.add_argument('--dp', type=Path, required=True, help='referenced binary .khdp file')
    parser.add_argument('--verify', action='store_true', help='also recompute the DP optimum and tie rule')
    parser.add_argument('--summary', action='store_true', help='omit individual assignments and full Hall sets')
    parser.add_argument('--sets', action='store_true', help='also print the derived P/Q sets on success')

    # Print help successfully before enforcing required command-line arguments.
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    # Decode with the existing checked reader; do not duplicate the binary codec.
    try:
        dp = read_dp(args.dp.read_bytes())

        # Rerun the optimizer only when requested.
        if args.verify:
            verify_dp(dp)

        result = read_matching(args.artifact.read_bytes(), dp, verify=True, hydrate=True)
        print_matching(result, dp, args.summary, args.sets)
        return 0
    except (Invalid, OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


# Keep formatting helpers importable without running the command-line interface.
if __name__ == '__main__':
    sys.exit(main())
