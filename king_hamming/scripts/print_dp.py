#!/usr/bin/env python3
"""Print a saved binary DP split as a readable table."""

import argparse
from pathlib import Path
import sys

from inspect_artifact import Invalid, omega, read_dp, verify_dp


def print_split(dp):
    """Print the parameters, ordered transitions, and predicted coverage.

    Parameters:
        dp: Decoded DP dictionary from read_dp; not modified.
    Returns:
        None; writes a human-readable report to stdout.
    """

    # Label the DP value as a prediction until a construction is available.
    print(f"DP split: GF({dp['p']}^{dp['r']}) = GF({dp['q']})")
    print(f"Format: {dp['format']}    File size: {dp['bytes']} bytes")
    print(f"Budget on each side: {dp['budget']}    Special-set size: {dp['suffix_count']}")
    print(f"Theta: {dp['theta']}    Predicted rows including freebie: {dp['predicted_rows']}")
    print("Integrity and split feasibility: checked")
    status = 'checked' if dp['optimality_checked'] else 'not checked (use --verify)'
    print(f"DP optimality and tie rule: {status}")
    print("Matching: not contained in this file")
    print(f"SHA-256: {dp['sha256']}")
    print()
    print(f"{'Block':>5}  {'Cosets':<13} {'a':>3} {'b':>3} {'t':>3} "
          f"{'Stripes':>8} {'Sets':>6} {'Omega':>6} {'Rows':>10}")
    coset = 0

    # Expand saved runs into the exact order used by graph construction.
    for index, (a, b, t) in enumerate(dp['split'], start=1):
        overlap = omega(dp['p'], a, b, t)
        rows = t * overlap * dp['suffix_count']**2
        label = f'C{coset}' if t == 1 else f'C{coset}-C{coset+t-1}'
        print(f"{index:5}  {label:<13} {a:3} {b:3} {t:3} "
              f"{a*t:8} {b*t:6} {overlap:6} {rows:10}")
        coset += t

    # Report unused resources separately from the freebie contribution.
    print(f"\nUsed stripes: {dp['used_stripes']}/{dp['budget']}")
    print(f"Used special sets: {dp['used_special_sets']}/{dp['budget']}")
    print(f"Freebie: C{coset}, adding {dp['q']} rows if the matching succeeds")


def main():
    """Parse the command line, decode a DP artifact, and print its contents.

    Parameters: none; reads sys.argv.
    Returns:
        0 for help or a printed report; 1 for invalid input or file errors.
        argparse exits with status 2 for invalid command-line syntax.
    """

    # Keep no-argument invocation useful without requiring an example file.
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Example: python3 print_dp.py examples/5_3.khdp --verify\n'
               'Save text: python3 print_dp.py examples/5_3.khdp > split.txt',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('artifact', type=Path, help='binary .khdp file')
    parser.add_argument('--verify', action='store_true', help='also recompute the DP optimum and tie rule')

    # An empty invocation is a successful help request.
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    # Validate the artifact before printing any result as meaningful data.
    try:
        dp = read_dp(args.artifact.read_bytes())

        # Optimality verification is optional because it reruns the DP.
        if args.verify:
            verify_dp(dp)

        print_split(dp)
        return 0
    except (Invalid, OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


# Execute only when invoked as a program, leaving the printer reusable by imports.
if __name__ == '__main__':
    sys.exit(main())
