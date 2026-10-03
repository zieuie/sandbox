"""Compact exact encoding for DP tile values and choices (tile format 2).

DP values never decrease as either budget grows, and neighbouring cells differ by
small steps; a tile also uses few distinct choices. So each row of values is
stored as its difference from the previous row (taken as one wide integer modulo
2^(64*width), which is exact whatever the data), rows are grouped in blocks and
split into byte planes, choices follow unchanged, and the whole stream is
xz-compressed. Measured on live 13^9, 23^7, 37^5 and 5^13 tiles this stores
0.0024-0.021 bytes per cell, against 0.8-1.8 for gzip of the raw arrays.
Standard library only: workers have no numpy.
"""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

from contextlib import contextmanager
import json
import lzma
import sys
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Iterator

XZ_MAGIC = b"\xfd7zXZ\x00"
PACKET_FORMAT = "KH-DP-TILE-2"
BLOCK_ROWS = 64
PRESET = 1
HEADER_LIMIT = 8192
ENCODING = {"block_rows": BLOCK_ROWS, "values": "row-delta-planes", "choices": "raw"}


def is_xz(path: Path) -> bool:
    """Return whether path starts with the xz magic that format-2 blobs use."""

    with path.open("rb") as stream:
        return stream.read(len(XZ_MAGIC)) == XZ_MAGIC


def file_rows(path: Path, rows: int, row_bytes: int) -> Iterator[bytes]:
    """Yield rows of exactly row_bytes from a raw native array file."""

    with path.open("rb") as source:
        for _ in range(rows):
            row = source.read(row_bytes)
            if len(row) != row_bytes:
                raise OSError("tile array truncated during encoding")
            yield row


def encode_values(rows: Iterable[bytes], width: int, write: Callable[[bytes], object],
                  check: Callable[[], None] | None = None) -> None:
    """Write rows of native uint64 values as row differences split into byte planes."""

    row_bytes = width * 8
    mask = (1 << (64 * width)) - 1
    previous = 0
    block: list[bytes] = []

    def flush() -> None:
        joined = b"".join(block)
        block.clear()
        for plane in range(8):
            write(joined[plane::8])

    for row in rows:
        if check is not None:
            check()
        current = int.from_bytes(row, sys.byteorder)
        block.append(((current - previous) & mask).to_bytes(row_bytes, sys.byteorder))
        previous = current
        if len(block) == BLOCK_ROWS:
            flush()
    if block:
        flush()


# Choices are categorical and repeat in long runs; xz does better on them untransformed
# than split into byte planes (measured on all four sample tiles).
def encode_choices(rows: Iterable[bytes], write: Callable[[bytes], object],
                   check: Callable[[], None] | None = None) -> None:
    """Write rows of native uint32 choices unchanged."""

    for row in rows:
        if check is not None:
            check()
        write(row)


def read_exact(stream: BinaryIO, size: int) -> bytes:
    """Read exactly size bytes or raise ValueError for a short stream."""

    parts = []
    remaining = size
    while remaining:
        part = stream.read(remaining)
        if not part:
            raise ValueError("encoded tile data is shorter than declared")
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


def decode_values(stream: BinaryIO, rows: int, width: int, write: Callable[[bytes], object],
                  check: Callable[[], None] | None = None) -> None:
    """Inverse of encode_values: write rows*width native uint64 values."""

    row_bytes = width * 8
    mask = (1 << (64 * width)) - 1
    previous = 0
    for start in range(0, rows, BLOCK_ROWS):
        if check is not None:
            check()
        count = min(BLOCK_ROWS, rows - start)
        joined = bytearray(count * row_bytes)
        plane_bytes = count * width
        for plane in range(8):
            joined[plane::8] = read_exact(stream, plane_bytes)
        for index in range(count):
            delta = int.from_bytes(joined[index * row_bytes:(index + 1) * row_bytes], sys.byteorder)
            previous = (previous + delta) & mask
            write(previous.to_bytes(row_bytes, sys.byteorder))


def decode_choices(stream: BinaryIO, rows: int, width: int, write: Callable[[bytes], object],
                   check: Callable[[], None] | None = None) -> None:
    """Inverse of encode_choices: write rows*width native uint32 choices."""

    for start in range(0, rows, BLOCK_ROWS):
        if check is not None:
            check()
        write(read_exact(stream, min(BLOCK_ROWS, rows - start) * width * 4))


def write_header(stream: BinaryIO, header: dict) -> None:
    """Write the canonical one-line JSON identity at the start of a compressed blob."""

    line = json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n"
    if len(line) > HEADER_LIMIT:
        raise ValueError("tile header is too long")
    stream.write(line.encode())


def read_header(stream: BinaryIO) -> dict:
    """Read and parse the one-line JSON identity, refusing an unbounded or invalid line."""

    line = stream.readline(HEADER_LIMIT + 1)
    if len(line) > HEADER_LIMIT or not line.endswith(b"\n"):
        raise ValueError("invalid encoded tile header")
    try:
        header = json.loads(line)
    except ValueError as error:
        raise ValueError("invalid encoded tile header") from error
    if not isinstance(header, dict):
        raise ValueError("invalid encoded tile header")
    return header


def open_writer(path: Path) -> lzma.LZMAFile:
    """Open a fresh deterministic xz stream (fixed preset, no timestamps)."""

    return lzma.LZMAFile(path, "xb", format=lzma.FORMAT_XZ, preset=PRESET)


def open_reader(path: Path) -> lzma.LZMAFile:
    """Open an xz stream for reading."""

    return lzma.LZMAFile(path, "rb", format=lzma.FORMAT_XZ)


# Callers fall back to another source on ValueError/OSError; a damaged xz stream must count too.
@contextmanager
def decoding():
    """Report corrupt or truncated xz data as ValueError."""

    try:
        yield
    except (lzma.LZMAError, EOFError) as error:
        raise ValueError(f"damaged encoded tile data: {error}") from error


def write_packet(directory: Path, rows: int, width: int, header: dict, output: Path,
                 check: Callable[[], None] | None = None) -> None:
    """Encode directory's values.bin, choices.bin and tile.json into one format-2 packet."""

    metadata = (directory / "tile.json").read_text()
    if len(metadata) > 4096:
        raise ValueError("tile metadata is too long")
    with open_writer(output) as stream:
        write_header(stream, {**header, "encoding": ENCODING, "tile_json": metadata})
        encode_values(file_rows(directory / "values.bin", rows, width * 8), width, stream.write, check)
        encode_choices(file_rows(directory / "choices.bin", rows, width * 4), stream.write, check)


def read_packet(packet: Path, directory: Path, rows: int, width: int, expected: dict,
                check: Callable[[], None] | None = None) -> None:
    """Decode a format-2 packet into directory/values.bin, choices.bin and tile.json.

    expected holds every identity and layout field the header must match.
    """

    with decoding(), open_reader(packet) as stream:
        header = read_header(stream)
        if header.get("encoding") != ENCODING or not isinstance(header.get("tile_json"), str) or \
                any(header.get(name) != value for name, value in expected.items()):
            raise ValueError("tile artifact identity or layout mismatch")
        directory.mkdir()
        with (directory / "values.bin").open("xb") as output:
            decode_values(stream, rows, width, output.write, check)
        with (directory / "choices.bin").open("xb") as output:
            decode_choices(stream, rows, width, output.write, check)
        if stream.read(1):
            raise ValueError("encoded tile is longer than declared")
    if len(header["tile_json"]) > 4096:
        raise ValueError("tile metadata is too long")
    (directory / "tile.json").write_text(header["tile_json"])
