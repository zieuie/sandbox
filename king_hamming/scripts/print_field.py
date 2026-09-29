#!/usr/bin/env python3
"""Print field elements, prefix/suffix values, and the Sudborough-set partition."""

import argparse
from pathlib import Path
import sys

from inspect_artifact import Field, Invalid, read_dp, read_matching, require
from print_matching import polynomial_text


def print_table(headers, rows, markdown=False):
    """Render string-valued rows as aligned text or a Markdown table.

    Parameters:
        headers: Sequence of column headings.
        rows: List of rows, each containing one string per heading.
        markdown: Whether to emit Markdown instead of aligned text.
    Returns:
        None; writes the complete table to stdout.
    """

    # Markdown tables retain the same values and ordering as plain text.
    if markdown:
        print('| ' + ' | '.join(headers) + ' |')
        print('| ' + ' | '.join(['---'] * len(headers)) + ' |')

        # Emit one row per field element or special set.
        for row in rows:
            print('| ' + ' | '.join(row) + ' |')

        return

    # Measure all cells to prevent long polynomial or set values from misaligning columns.
    widths = [max(len(row[column]) for row in [headers, *rows])
              for column in range(len(headers))]
    print('  '.join(value.ljust(width) for value, width in zip(headers, widths)))
    print('  '.join('-' * width for width in widths))

    # Keep each logical record on one line for straightforward hand inspection.
    for row in rows:
        print('  '.join(value.ljust(width) for value, width in zip(row, widths)))


def print_field(field, markdown=False):
    """Print all field elements and their partition into Sudborough sets.

    Parameters:
        field: Validated primitive-X Field instance; not modified.
        markdown: Whether to emit Markdown tables instead of aligned plain text.
    Returns:
        None; writes conventions, q element rows, and p*F special-set rows to stdout.
    """
    title = f'GF({field.p}^{field.r}), primitive polynomial {polynomial_text(field.poly)}'
    print(title)
    print('Generator t = X. Label 0 denotes zero; label i+1 denotes t^i.')
    print(f'Coefficients are highest degree first: X^{field.r-1} through X^0.')
    m = field.r // 2
    print(f'Prefix = sum of coefficients of X^{m} through X^{field.r-1}, modulo {field.p}.')
    print(f'Suffix = base-{field.p} index of the lowest {m} coefficients (0..{field.f-1}).')
    print()
    headers = ('Index', 'Power of t', 'Remainder', 'Remainder coefficients', 'Prefix', 'Suffix')
    rows = []

    # Polynomial storage is low degree first; the paper displays tuples in reverse order.
    for label, coefficients in enumerate(field.coefficients):
        power = '0' if label == 0 else f't^{label-1}'
        remainder = polynomial_text(coefficients)
        displayed = '(' + ', '.join(map(str, reversed(coefficients))) + ')'
        prefix = sum(coefficients[m:]) % field.p
        suffix = sum(coefficients[j] * field.p**j for j in range(m))
        rows.append((str(label), power, remainder, displayed, str(prefix), str(suffix)))

    print_table(headers, rows, markdown)
    print()
    print('## Sudborough sets' if markdown else 'Sudborough sets')
    print()
    print('S(prefix, suffix) contains the field labels with that prefix residue and suffix.')
    print(f'There are {field.p * field.f} disjoint sets, each containing {field.f} elements.')
    print()
    sets = []

    # Reuse the exact special-set index used by the field and certificate decoder.
    for prefix in range(field.p):

        # List suffixes in ascending order within each prefix-residue group.
        for suffix in range(field.f):
            elements = field.cells[prefix, suffix]
            labels = '{' + ', '.join(map(str, elements)) + '}'
            sets.append((str(prefix), str(suffix), str(len(elements)), labels))

    print_table(('Prefix', 'Suffix', 'Size', 'Elements (field labels)'), sets, markdown)


def main():
    """Load an explicit field or saved field specification and print its elements.

    Parameters: none; reads sys.argv.
    Returns:
        0 for help or successful output; 1 for invalid field/artifact or I/O errors.
        argparse exits with status 2 for invalid command-line syntax.
    """

    # Offer both manual field inspection and reuse of a saved construction's polynomial.
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Examples (from king_hamming):\n'
               '  python3 print_field.py 5 3 --poly 2,3,0,1\n'
               '  python3 print_field.py --matching examples/5_3.khmatch --dp examples/5_3.khdp\n'
               '  python3 print_field.py 3 3 --poly 1,0,2,1 --markdown > field.md\n'
               'Polynomial input is LOW degree first; printed tuples are HIGH degree first.\n'
               'Uses the current prototype limits (odd degrees >= 3).\n'
               'No arguments prints this help.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('prime', type=int, nargs='?', help='prime characteristic, e.g. 5')
    parser.add_argument('degree', type=int, nargs='?', help='odd extension degree, e.g. 3')
    parser.add_argument('--poly', help='primitive polynomial coefficients, constant term first')
    parser.add_argument('--matching', type=Path, help='read field specification from this .khmatch artifact')
    parser.add_argument('--dp', type=Path, help='referenced .khdp artifact, required with --matching')
    parser.add_argument('--markdown', action='store_true', help='print both tables in Markdown')

    # No input is a request for help rather than a missing-argument error.
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    # Reject ambiguous combinations before reading files or constructing the field.
    if args.matching is not None:

        # Artifact mode obtains all field parameters from the checked dependency pair.
        if args.dp is None or any(value is not None for value in (args.prime, args.degree, args.poly)):
            parser.error('--matching requires --dp and cannot be combined with prime, degree, or --poly')
    elif args.dp is not None or any(value is None for value in (args.prime, args.degree, args.poly)):
        parser.error('supply PRIME DEGREE --poly COEFFICIENTS, or --matching FILE --dp FILE')

    # Validate the primitive-X field completely before printing its table.
    try:

        # Check artifact integrity and reference without rerunning matching or DP.
        if args.matching is not None:
            dp = read_dp(args.dp.read_bytes())
            metadata = read_matching(args.matching.read_bytes(), dp)
            field = Field(metadata['p'], metadata['r'], metadata['polynomial_low_first'])
        else:
            parts = args.poly.split(',')
            require(all(part.isascii() and part.isdigit() for part in parts),
                    'polynomial coefficients must be comma-separated nonnegative decimal integers')
            field = Field(args.prime, args.degree, list(map(int, parts)))

        print_field(field, args.markdown)
        return 0
    except (Invalid, OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


# Allow the rendering function to be imported independently of the CLI.
if __name__ == '__main__':
    sys.exit(main())
