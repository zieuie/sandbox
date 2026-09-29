#!/usr/bin/env python3
"""Read, independently verify, and hydrate the small king_hamming v1 artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


class Reader:
    def __init__(self, raw):
        require(36 <= len(raw) <= 16 * 1024 * 1024, 'invalid artifact size')
        require(hashlib.sha256(raw[:-32]).digest() == raw[-32:], 'checksum mismatch')
        self.data, self.pos = raw[:-32], 0

    def take(self, n):
        require(n <= len(self.data) - self.pos, 'truncated artifact')
        result = self.data[self.pos:self.pos+n]
        self.pos += n
        return result

    def uint(self):
        result = 0
        for shift in range(0, 64, 7):
            x = self.take(1)[0]
            require(shift != 63 or x <= 1, 'varint overflow')
            result |= (x & 127) << shift
            if x < 128:
                require(not shift or x != 0, 'noncanonical varint')
                return result
        raise Invalid('varint overflow')

    def packed(self, count, bits):
        raw = self.take((count * bits + 7) // 8)
        if count * bits % 8:
            require(raw[-1] >> (count * bits % 8) == 0, 'nonzero padding bits')
        return [sum(((raw[(i*bits+k)//8] >> ((i*bits+k)%8)) & 1) << k
                    for k in range(bits)) for i in range(count)]

    def end(self):
        require(self.pos == len(self.data), 'unexpected trailing data')


def parameters(p, r):
    require(2 <= p <= 31 and all(p % d for d in range(2, p)), 'invalid prime')
    require(3 <= r <= 15 and r % 2, 'invalid odd degree')
    q, f = p**r, p**(r//2)
    require(q <= 65536 and p*f <= 2048, 'instance exceeds proof-of-concept limits')
    return q, f, p*f


def omega(p, a, b, t):
    return len({(j*t-i) % p for i in range(a) for j in range(b)})


def read_dp(raw):
    rd = Reader(raw)
    require(rd.take(4) == b'KHD1', 'unsupported DP format')
    p, r, theta, runs = (rd.uint() for _ in range(4))
    q, f, budget = parameters(p, r)
    require(0 < runs <= budget, 'invalid run count')
    split = []
    for _ in range(runs):
        a, b, t, count = (rd.uint() for _ in range(4))
        require(all(1 <= x <= p for x in (a, b, t)), 'invalid triple')
        require(0 < count <= budget-len(split), 'invalid run length')
        split.extend([[a, b, t] for _ in range(count)])
    rd.end()
    used_p = sum(a*t for a, b, t in split)
    used_q = sum(b*t for a, b, t in split)
    require(used_p <= budget and used_q <= budget, 'split exceeds budgets')
    require(sum(t*omega(p, a, b, t) for a, b, t in split) == theta, 'wrong split value')
    require(sum(t for a, b, t in split)+1 <= q-1, 'not enough distinct cosets')
    return dict(format='KHD1', p=p, r=r, q=q, suffix_count=f, budget=budget,
                theta=theta, predicted_rows=theta*f*f+q, split=split,
                used_stripes=used_p, used_special_sets=used_q,
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                optimality_checked=False, construction_verified=False)


def verify_dp(dp):
    """Independent recurrence evaluation, including the paper's first-tie rule."""
    p, budget = dp['p'], dp['budget']
    moves = [(a, b, t, t*omega(p, a, b, t))
             for a in range(1, p+1) for b in range(1, p+1) for t in range(1, p+1)
             if a*t <= budget and b*t <= budget]
    require((budget+1)**2 * len(moves) <= 200_000_000, 'verification work limit exceeded')
    values = [[0]*(budget+1) for _ in range(budget+1)]
    choice = {}
    for u in range(1, budget+1):
        for v in range(1, budget+1):
            best, step = 0, None
            for a, b, t, gain in moves:
                if a*t <= u and b*t <= v:
                    candidate = gain + values[u-a*t][v-b*t]
                    if candidate > best:
                        best, step = candidate, [a, b, t]
            values[u][v], choice[u, v] = best, step
    expected, u, v = [], budget, budget
    while u and v:
        step = choice[u, v]
        if step is None:
            break
        expected.append(step)
        a, b, t = step
        u, v = u-a*t, v-b*t
    require(dp['theta'] == values[budget][budget], 'claimed DP optimum is wrong')
    require(dp['split'] == expected, 'split violates the specified DP tie rule')
    dp['optimality_checked'] = True


class Field:
    """Independent polynomial arithmetic; no calls to the C field implementation."""
    def __init__(self, p, r, poly):
        self.p, self.r, self.poly = p, r, poly
        self.q, self.f, _ = parameters(p, r)
        require(len(poly) == r+1 and poly[-1] == 1 and poly[0] != 0
                and all(0 <= c < p for c in poly), 'invalid monic polynomial')
        self.coefficients = [(0,)*r]
        seen = set()
        current = (1,)+(0,)*(r-1)
        x = (0, 1)+(0,)*(r-2)
        for _ in range(self.q-1):
            require(any(current) and current not in seen, 'polynomial is not primitive with generator X')
            seen.add(current)
            self.coefficients.append(current)
            current = self.multiply(current, x)
        require(current == self.coefficients[1], 'invalid X cycle')
        self.labels = {c: i for i, c in enumerate(self.coefficients)}
        self.cells = {(g, h): [] for g in range(p) for h in range(self.f)}
        for label, c in enumerate(self.coefficients):
            g = sum(c[r//2:]) % p
            h = sum(c[j]*p**j for j in range(r//2))
            self.cells[g, h].append(label)
        require(all(len(v) == self.f for v in self.cells.values()), 'invalid special set sizes')

    def multiply(self, a, b):
        c = [0]*(2*self.r-1)
        for i, x in enumerate(a):
            for j, y in enumerate(b):
                c[i+j] = (c[i+j]+x*y) % self.p
        for k in range(len(c)-1, self.r-1, -1):
            lead = c[k]
            for j, coefficient in enumerate(self.poly):
                c[k-self.r+j] = (c[k-self.r+j]-lead*coefficient) % self.p
        return tuple(c[:self.r])

    def mul_label(self, a, b):
        return self.labels[self.multiply(self.coefficients[a], self.coefficients[b])]

    def add_label(self, a, b):
        return self.labels[tuple((x+y) % self.p for x, y in zip(self.coefficients[a], self.coefficients[b]))]


def left_nodes(dp):
    nodes, coset = [], 0
    for a, b, t in dp['split']:
        for _ in range(t):
            nodes.extend((coset, g, h) for g in range(a) for h in range(dp['suffix_count']))
            coset += 1
    return nodes


def symbol_sets(dp, field):
    sets, cursor = [], 0
    for a, b, t in dp['split']:
        for ell in range(t):
            group = []
            for j in range(b):
                special = cursor+ell+j*t
                group.extend(field.cells[special % dp['p'], special // dp['p']])
            sets.append(sorted(group))
        cursor += b*t
    return sets


def verify_hall(neighbors, witness, matched):
    neighborhood = {v for u in witness for v in neighbors[u]}
    require(len(witness) > len(neighborhood), 'invalid Hall witness')
    require(len(witness)-len(neighborhood) == len(neighbors)-matched,
            'Hall witness does not certify claimed maximum size')
    return neighborhood


def read_matching(raw, dp, verify=False, hydrate=False):
    rd = Reader(raw)
    require(rd.take(4) == b'KHM1', 'unsupported matching format')
    require(rd.take(32).hex() == dp['sha256'], 'wrong DP dependency')
    p, r = rd.uint(), rd.uint()
    require((p, r) == (dp['p'], dp['r']), 'field disagrees with DP')
    poly = [rd.uint() for _ in range(r+1)]
    status, count, matched = rd.uint(), rd.uint(), rd.uint()
    require(status in (0, 1), 'unknown matching status')
    nodes = left_nodes(dp)
    require(count == len(nodes) and matched <= count, 'invalid matching counts')
    require((status == 0 and matched == count) or (status == 1 and matched < count), 'inconsistent matching status')
    f = dp['suffix_count']
    choices = rd.packed(count, (f+status-1).bit_length())
    require(all(k < f+status for k in choices), 'neighbor index out of range')
    hall_bits = rd.packed(count, 1) if status else []
    rd.end()
    result = dict(format='KHM1', bytes=len(raw), dp_sha256=dp['sha256'], p=p, r=r,
                  polynomial_low_first=poly, generator='X', status='obstruction' if status else 'full_matching',
                  required=count, matched=matched, verified=False,
                  claimed_rows=dp['predicted_rows'] if not status else None)
    if not (verify or hydrate):
        return result
    field = Field(p, r, poly)
    # Build neighbors by multiplying each special-set element by the inverse
    # slope using polynomial arithmetic, independently of C's label subtraction.
    inverse = {i: 1 if i == 0 else field.q-i for i, g, h in nodes}
    neighbors = [[field.mul_label(z, inverse[i]) for z in field.cells[g, h]] for i, g, h in nodes]
    assignment = [None if status and k == 0 else neighbors[u][k-status] for u, k in enumerate(choices)]
    occupied = [v for v in assignment if v is not None]
    require(len(occupied) == matched and len(set(occupied)) == matched, 'invalid matching: duplicate or missing right vertices')
    for (i, g, h), v in zip(nodes, assignment):
        if v is None:
            continue
        z = field.mul_label(i+1, v)
        require(z in field.cells[g, h], 'illegal matching edge')
    if status:
        witness = [u for u, bit in enumerate(hall_bits) if bit]
        neighborhood = verify_hall(neighbors, witness, matched)
        result['hall'] = dict(left_count=len(witness), neighbor_count=len(neighborhood),
                              deficiency=len(witness)-len(neighborhood))
        if hydrate:
            result['hall']['left_indices'] = witness
            result['hall']['neighbors'] = sorted(neighborhood)
    else:
        qsets = symbol_sets(dp, field)
        require(len(set().union(*(set(s) for s in qsets))) == sum(map(len, qsets)), 'overlapping symbol sets')
        result['construction_verified'] = True  # structural conditions + paper's coverage lemma
        if hydrate:
            psets = [[] for _ in qsets]
            for (i, g, h), v in zip(nodes, assignment):
                psets[i].append(v)
            result['position_sets'] = [sorted(s) for s in psets]
            result['symbol_sets'] = qsets
            result['freebie_coset'] = len(qsets)
    if hydrate:
        result['matching'] = [dict(left=list(node), right=v) for node, v in zip(nodes, assignment)]
    result['verified'] = True
    return result


def expand_rows(dp, result):
    """Slow, direct P&E hydration for small examples and independent tests."""
    require(result['status'] == 'full_matching' and result.get('position_sets') is not None,
            'expansion requires a hydrated successful matching')
    field = Field(dp['p'], dp['r'], result['polynomial_low_first'])
    q = field.q
    require(q <= 343, 'full row expansion is limited to q <= 343')
    rows, counts = [], []
    for i, (positions, symbols) in enumerate(zip(result['position_sets'], result['symbol_sets'])):
        symbols = set(symbols)
        products = [field.mul_label(i+1, x) for x in range(q)]
        count = 0
        for intercept in range(q):
            row = [field.add_label(z, intercept) for z in products]
            position = next((x for x in positions if row[x] in symbols), None)
            if position is not None:
                displaced = row[position]
                row[position] = q
                rows.append(row+[displaced])
                count += 1
        counts.append(count)
    freebie = result['freebie_coset']
    products = [field.mul_label(freebie+1, x) for x in range(q)]
    rows.extend([field.add_label(z, b) for z in products]+[q] for b in range(q))
    require(len(rows) == dp['predicted_rows'], 'direct coverage disagrees with DP')
    require(all(sorted(row) == list(range(q+1)) for row in rows), 'invalid expanded permutation')
    return rows, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Examples (from king_hamming):\n'
        '  python3 inspect_artifact.py examples/5_3.khdp --verify\n'
        '  python3 inspect_artifact.py examples/5_3.khmatch --dp examples/5_3.khdp --verify --hydrate\n'
        '  python3 inspect_artifact.py examples/3_3.khmatch --dp examples/3_3.khdp --pa /tmp/rows.txt\n'
        'No arguments prints this help. JSON goes to stdout; use shell redirection to save it.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--dp', type=Path, help='DP dependency for a matching artifact')
    parser.add_argument('--verify', action='store_true', help='independently check DP optimality and the matching/Hall certificate')
    parser.add_argument('--hydrate', action='store_true', help='include full decoded matching, P/Q sets, or Hall witness in JSON')
    parser.add_argument('--pa', type=Path, help='expand successful construction to permutation rows (q <= 343; no overwrite)')
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args()
    try:
        raw = args.artifact.read_bytes()
        if raw[:4] == b'KHD1':
            require(args.dp is None and args.pa is None, '--dp and --pa are only for matching artifacts')
            result = read_dp(raw)
            if args.verify:
                verify_dp(result)
        else:
            require(args.dp is not None, 'a matching artifact requires --dp FILE')
            dp = read_dp(args.dp.read_bytes())
            if args.verify or args.pa:
                verify_dp(dp)
            result = read_matching(raw, dp, args.verify or bool(args.pa), args.hydrate or bool(args.pa))
            result['dp_optimality_checked'] = dp['optimality_checked']
            if args.pa:
                rows, counts = expand_rows(dp, result)
                with args.pa.open('x') as out:
                    for row in rows:
                        out.write(' '.join(map(str, row))+'\n')
                result['expanded_rows'] = len(rows)
                result['direct_coset_coverage'] = counts
        print(json.dumps(result, indent=2))
        return 0
    except (Invalid, OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
