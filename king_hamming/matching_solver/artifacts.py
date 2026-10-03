"""Streaming KHM1 certificates and independent primitive-X matching verification."""

from __future__ import annotations

from array import array
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile

from dp_solver.artifacts import dimensions, varint

NATIVE_VERIFIER = Path(__file__).resolve().parent / "kh_verify_khm1"
NATIVE_MIN_LABELS = 1 << 24  # below this the Python verifier is fast enough


# Derive the request count without expanding compressed DP runs.
def request_count(dp):
    """Return the left count for validated DP document dp."""
    return dp["f"] * sum(run["a"] * run["t"] * run["repeat"] for run in dp["runs"])


# Enumerate the exact request order used by the preserved reference.
def requests(dp):
    """Yield (coset,prefix,suffix) from validated dp without storing all requests."""
    coset = 0
    for run in dp["runs"]:
        for _ in range(run["t"] * run["repeat"]):
            for prefix in range(run["a"]):
                for suffix in range(dp["f"]):
                    yield coset, prefix, suffix
            coset += 1


# Stream checksums so certificate size does not determine Python heap usage.
def file_hash(path):
    """Return the SHA-256 digest of input path; raise on I/O failure."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


# Publish complete artifacts without overwriting previous attempts.
def publish(path, header, payload):
    """Write header plus input payload plus checksum to fresh path, with durable atomic publication."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as output, Path(payload).open("rb") as source:
            output.write(header)
            digest.update(header)
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
            output.write(digest.digest())
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


# Preserve KHM1 byte conventions rather than introduce an unnecessary new format.
def header(dp, dp_digest, metadata):
    """Return a KHM1 header for validated dp, its hash, and kernel metadata; reject inconsistencies."""
    n = request_count(dp)
    status = metadata["status"]
    matched = metadata["matched"]
    polynomial = metadata["polynomial"]
    if (metadata["p"], metadata["r"], metadata["required"]) != (dp["p"], dp["r"], n):
        raise ValueError("kernel dimensions disagree with DP")
    if status not in (0, 1) or not 0 <= matched <= n or (status == 0) != (matched == n):
        raise ValueError("inconsistent matching outcome")
    if len(polynomial) != dp["r"] + 1 or any(type(c) is not int or not 0 <= c < dp["p"] for c in polynomial):
        raise ValueError("invalid polynomial metadata")
    return b"KHM1" + dp_digest + b"".join(varint(value) for value in
        [dp["p"], dp["r"], *polynomial, status, n, matched])


# Keep parsing bounded by the recorded artifact length and canonical varint rules.
class Reader:
    """Read a checksum-validated path through stream; caller owns closing stream."""

    def __init__(self, stream, size):
        """Initialize input stream and total size; checksum validation is the caller's responsibility."""
        self.stream = stream
        self.end = size - 32

    def take(self, count):
        """Read exactly count payload bytes; raise on truncation or crossing the checksum."""
        if count < 0 or self.stream.tell() + count > self.end:
            raise ValueError("truncated matching artifact")
        result = self.stream.read(count)
        if len(result) != count:
            raise ValueError("truncated matching artifact")
        return result

    def uint(self):
        """Return one canonical uint64 varint; reject overflow and redundant encoding."""
        value = 0
        for shift in range(0, 64, 7):
            byte = self.take(1)[0]
            if shift == 63 and byte > 1:
                raise ValueError("varint overflow")
            value |= (byte & 127) << shift
            if byte < 128:
                if shift and byte == 0:
                    raise ValueError("noncanonical varint")
                return value
        raise ValueError("varint overflow")


# Decode low-bit-first choices a chunk at a time, including canonical padding checks.
def packed(stream, count, bits):
    """Yield count integers of bits bits from input stream; reject truncation or nonzero padding."""
    remaining = (count * bits + 7) // 8
    accumulator = available = 0
    block = b""
    cursor = 0
    for _ in range(count):
        while available < bits:
            if cursor == len(block):
                block = stream.read(min(65536, remaining))
                if not block:
                    raise ValueError("truncated packed choices")
                remaining -= len(block)
                cursor = 0
            accumulator |= block[cursor] << available
            cursor += 1
            available += 8
        yield accumulator & ((1 << bits) - 1)
        accumulator >>= bits
        available -= bits
    if accumulator or remaining or cursor != len(block):
        raise ValueError("nonzero padding or incomplete packed choices")


# Independently perform quotient-polynomial multiplication, without calling native code.
def multiply(left, right, p, r, polynomial):
    """Return the product of packed input residues left,right modulo polynomial over F_p."""
    a = []
    b = []
    for _ in range(r):
        a.append(left % p)
        b.append(right % p)
        left //= p
        right //= p
    coefficients = [0] * (2 * r - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            coefficients[i + j] = (coefficients[i + j] + x * y) % p
    for degree in range(2 * r - 2, r - 1, -1):
        lead = coefficients[degree]
        for j in range(r):
            coefficients[degree - r + j] = (coefficients[degree - r + j] - lead * polynomial[j]) % p
    return sum(coefficient * p**j for j, coefficient in enumerate(coefficients[:r]))


# Test generator order independently before reconstructing the SUD partition.
def primitive(p, r, polynomial):
    """Return whether monic input polynomial is primitive with the fixed generator X."""
    q, _, _ = dimensions(p, r)
    if len(polynomial) != r + 1 or polynomial[-1] != 1 or polynomial[0] == 0 or any(not 0 <= c < p for c in polynomial):
        return False

    # Modular exponentiation is bounded by the extension degree and log(q).
    def power(exponent):
        """Return X^exponent in the selected quotient for a nonnegative exponent."""
        value = 1
        base = p
        while exponent:
            if exponent & 1:
                value = multiply(value, base, p, r, polynomial)
            exponent //= 2
            if exponent:
                base = multiply(base, base, p, r, polynomial)
        return value

    if power(q - 1) != 1:
        return False
    remaining = q - 1
    divisor = 2
    while divisor * divisor <= remaining:
        if remaining % divisor == 0:
            if power((q - 1) // divisor) == 1:
                return False
            while remaining % divisor == 0:
                remaining //= divisor
        divisor += 1
    return remaining <= 1 or power((q - 1) // remaining) != 1


# Construct sorted SUD cells with one packed label array and compact write counters.
def field_cells(p, r, polynomial):
    """Return an owned uint32 SUD array for a primitive polynomial; raise on bucket inconsistencies."""
    q, f, budget = dimensions(p, r)
    cells = array("I", [0]) * q
    positions = array("I", [0]) * budget
    current = 1
    half = r // 2
    for label in range(q):
        element = 0 if label == 0 else current
        suffix = element % f
        high = element // f
        prefix = 0
        for _ in range(r - half):
            prefix += high % p
            high //= p
        cell = (prefix % p) * f + suffix
        offset = positions[cell]
        if offset >= f:
            raise ValueError("invalid SUD bucket size")
        cells[cell * f + offset] = label
        positions[cell] += 1
        if label:
            top = current // (q // p)
            rest = (current % (q // p)) * p
            current = 0
            place = 1
            for j in range(r):
                digit = (rest % p - top * polynomial[j]) % p
                current += digit * place
                place *= p
                rest //= p
    if current != 1 or any(count != f for count in positions):
        raise ValueError("invalid primitive cycle or SUD partition")
    return cells


# Large full matchings: the native verifier checks every edge in a streaming pass.
def native_memory(dp, q, f):
    """Bytes the native verifier needs: cell rows of used prefixes, endpoint bitmap, and slack."""
    amax = max(run["a"] for run in dp["runs"])
    return 4 * f * amax * f + (q + 7) // 8 + 4 * dimensions(dp["p"], dp["r"])[2] + 64 * 1024 * 1024


def native_assigned(path, dp, polynomial, payload_start):
    """Run kh_verify_khm1 over path's packed choices; return the number of distinct endpoints it checked."""
    with tempfile.TemporaryDirectory(prefix=".kh-verify-") as directory:
        blocks = Path(directory) / "blocks.txt"
        blocks.write_text(f"{len(dp['runs'])}\n" + "".join(
            f"{run['a']} {run['t'] * run['repeat']}\n" for run in dp["runs"]))
        done = subprocess.run(
            [str(NATIVE_VERIFIER), str(dp["p"]), str(dp["r"]), ",".join(map(str, polynomial)),
             str(blocks), str(path), str(payload_start)], capture_output=True, text=True)
    if done.returncode:
        raise ValueError(done.stderr.strip().removeprefix("kh_verify_khm1: ") or "native verification failed")
    return int(json.loads(done.stdout)["assigned"])


# Check an entire certificate without searching for a matching or storing all endpoints.
def verify(path, dp, dp_digest, max_bytes=2**31, hydrate=None, native=None):
    """Verify input path against dp/hash under max_bytes; optionally write TSV rows to hydrate stream; return summary.

    Full matchings with q >= NATIVE_MIN_LABELS use kh_verify_khm1 when it is built (native=None);
    native=True requires it and native=False forces this Python path. Hydration is Python-only.
    """
    path = Path(path)
    size = path.stat().st_size
    if size < 36:
        raise ValueError("invalid matching artifact size")
    with path.open("rb") as source:
        checksum = hashlib.sha256()
        remaining = size - 32
        while remaining:
            chunk = source.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("truncated matching artifact")
            checksum.update(chunk)
            remaining -= len(chunk)
        if checksum.digest() != source.read(32):
            raise ValueError("matching checksum mismatch")
        source.seek(0)
        reader = Reader(source, size)
        if reader.take(4) != b"KHM1" or reader.take(32) != dp_digest:
            raise ValueError("unsupported matching format or wrong DP dependency")
        p, r = reader.uint(), reader.uint()
        if (p, r) != (dp["p"], dp["r"]):
            raise ValueError("matching dimensions disagree with DP")
        polynomial = [reader.uint() for _ in range(r + 1)]
        status, n, matched = reader.uint(), reader.uint(), reader.uint()
        if status not in (0, 1) or n != request_count(dp) or not 0 <= matched <= n or (status == 0) != (matched == n):
            raise ValueError("invalid matching outcome or counts")
        q, f, budget = dimensions(p, r)
        bits = (f + status - 1).bit_length()
        payload_start = source.tell()
        choices_size = (n * bits + 7) // 8
        hall_size = (n + 7) // 8 if status else 0
        if payload_start + choices_size + hall_size != size - 32:
            raise ValueError("invalid matching payload length")
        use_native = (status == 0 and hydrate is None and native is not False and
                      (native is True or (q >= NATIVE_MIN_LABELS and NATIVE_VERIFIER.exists())))
        if use_native and not NATIVE_VERIFIER.exists():
            raise ValueError("native verifier is not built")
        memory = native_memory(dp, q, f) if use_native else 4 * q + 4 * budget + 2 * ((q + 7) // 8) + 64 * 1024 * 1024
        if memory > max_bytes:
            raise ValueError(f"verification requires at least {memory} bytes; limit={max_bytes}")
        if not primitive(p, r, polynomial):
            raise ValueError("polynomial is not primitive with generator X")
        assigned = hall_left = hall_right = 0
        if use_native:
            assigned = native_assigned(path, dp, polynomial, payload_start)
        else:
            cells = field_cells(p, r, polynomial)
            occupied = bytearray((q + 7) // 8)
            neighborhood = bytearray((q + 7) // 8) if status else None
            if hydrate is not None:
                hydrate.write("left\tcoset\tprefix\tsuffix\tright\n")
            with path.open("rb") as hall_stream:
                hall_stream.seek(payload_start + choices_size)
                hall_iterator = packed(hall_stream, n, 1) if status else iter(())
                choice_iterator = packed(source, n, bits)
                for u, ((coset, prefix, suffix), choice) in enumerate(zip(requests(dp), choice_iterator)):
                    if choice >= f + status:
                        raise ValueError("neighbor index out of range")
                    cell_start = (prefix * f + suffix) * f
                    right = None
                    if not status or choice:
                        label = cells[cell_start + choice - status]
                        right = 0 if label == 0 else 1 + (label - 1 - coset) % (q - 1)
                        mask = 1 << (right % 8)
                        if occupied[right // 8] & mask:
                            raise ValueError("matching repeats a right endpoint")
                        occupied[right // 8] |= mask
                        assigned += 1
                    if hydrate is not None:
                        hydrate.write(f"{u}\t{coset}\t{prefix}\t{suffix}\t{right if right is not None else '-'}\n")
                    if status and next(hall_iterator):
                        hall_left += 1
                        for k in range(f):
                            label = cells[cell_start + k]
                            neighbor = 0 if label == 0 else 1 + (label - 1 - coset) % (q - 1)
                            mask = 1 << (neighbor % 8)
                            if not neighborhood[neighbor // 8] & mask:
                                neighborhood[neighbor // 8] |= mask
                                hall_right += 1

                # Exhaustion executes each decoder's final padding check after its last yielded value.
                if next(choice_iterator, None) is not None or (status and next(hall_iterator, None) is not None):
                    raise ValueError("unexpected packed values")
        if assigned != matched:
            raise ValueError("matching cardinality disagrees with certificate")
        if status and (hall_left <= hall_right or hall_left - hall_right != n - matched):
            raise ValueError("Hall witness does not prove claimed maximum cardinality")
    summary = dict(format="KHM1", p=p, r=r, polynomial=polynomial, generator="X",
                   status="obstruction" if status else "full_matching", required=n, matched=matched,
                   bytes=size, verified=True, dp_optimality_checked=False)
    if status:
        summary["hall"] = dict(left_count=hall_left, right_count=hall_right, deficiency=hall_left - hall_right)
    return summary


# Keep DP input size bounded before allocating or decoding it.
def load_dp(path):
    """Return validated KHD1 document and whole-file hash from input path; reject oversized inputs."""
    from dp_solver.artifacts import decode_dp
    with Path(path).open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("DP artifact exceeds 16 MiB")
    return decode_dp(raw), hashlib.sha256(raw).digest()


# Atomically retain an already verified temporary artifact on the same filesystem.
def retain(source, destination):
    """Link input source to fresh destination and fsync its parent; raise rather than overwrite."""
    destination = Path(destination)
    os.link(source, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
