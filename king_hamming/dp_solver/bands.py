"""Compressed edge bands of finished DP tiles: the only part a successor tile's halo needs."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import gzip
import json
from pathlib import Path
import sys
from typing import Callable

from dp_solver import tile_codec
from dp_solver.tiles import band_region, extract_region, piece_t, region_rows, region_t, tile_t

FORMAT = "KH-DP-BAND-1"
FORMAT_2 = "KH-DP-BAND-2"
HEADER_LIMIT = 4096
BLOCK_BYTES = 1024 * 1024


def header(p: int, r: int, rectangle: tile_t, kind: str, region: region_t) -> dict:
    """Return the identity a band blob must declare: field, source tile, kind, cells and layout."""

    return {"format": FORMAT, "p": p, "r": r, "kind": kind,
            "tile_row": rectangle.row, "tile_column": rectangle.column,
            "first_u": region.first_u, "last_u": region.last_u,
            "first_v": region.first_v, "last_v": region.last_v,
            "byteorder": sys.byteorder, "value_bytes": 8}


# The blob is content-addressed, so equal bands must give equal bytes: no timestamp, fixed level.
def write_band(values: Path, p: int, r: int, rectangle: tile_t, kind: str, output: Path,
               check: Callable[[], None] | None = None, tile_format: int = 1) -> region_t:
    """Write a blob holding a JSON identity line and kind's cells; return the region it covers.

    Tile format 1 is gzip of the raw cells; format 2 is the tile_codec row-delta xz encoding.
    """

    region = band_region(p, rectangle, kind)
    if tile_format == 2:
        with tile_codec.open_writer(output) as stream:
            tile_codec.write_header(stream, {**header(p, r, rectangle, kind, region), "format": FORMAT_2,
                                             "encoding": tile_codec.ENCODING})
            tile_codec.encode_values(region_rows(values, rectangle, region, check), region.width,
                                     stream.write, check)
        return region
    line = json.dumps(header(p, r, rectangle, kind, region), sort_keys=True, separators=(",", ":")) + "\n"
    with output.open("xb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, compresslevel=1, mtime=0) as compressed:
            compressed.write(line.encode())
            extract_region(values, rectangle, region, compressed, check)
        stream.flush()
    return region


# Check identity, layout and exact length before any value reaches a halo.
def read_band(blob: Path, directory: Path, p: int, r: int, rectangle: tile_t, kind: str,
              check: Callable[[], None] | None = None) -> piece_t:
    """Unpack a verified blob into directory/band.bin and return the piece it holds.

    The blob's hash was already checked against the leader's descriptor; this
    additionally refuses a blob that names another tile, kind or layout, or whose
    length is not exactly the declared rectangle.
    """

    region = band_region(p, rectangle, kind)
    expected = header(p, r, rectangle, kind, region)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "band.bin"
    if tile_codec.is_xz(blob):
        with tile_codec.decoding(), tile_codec.open_reader(blob) as stream:
            if tile_codec.read_header(stream) != {**expected, "format": FORMAT_2, "encoding": tile_codec.ENCODING}:
                raise ValueError("band identity or layout mismatch")
            with destination.open("xb") as output:
                tile_codec.decode_values(stream, region.last_u - region.first_u + 1, region.width,
                                         output.write, check)
            if stream.read(1):
                raise ValueError("band is longer than its declared region")
        return piece_t(region, destination)
    with gzip.open(blob, "rb") as stream:
        line = stream.readline(HEADER_LIMIT + 1)
        if len(line) > HEADER_LIMIT or not line.endswith(b"\n"):
            raise ValueError("invalid band header")
        try:
            declared = json.loads(line)
        except ValueError as error:
            raise ValueError("invalid band header") from error
        if declared != expected:
            raise ValueError("band identity or layout mismatch")
        remaining = region.value_bytes
        with destination.open("xb") as output:
            while remaining:
                if check is not None:
                    check()
                block = stream.read(min(BLOCK_BYTES, remaining))
                if not block:
                    raise ValueError("band is shorter than its declared region")
                output.write(block)
                remaining -= len(block)
        if stream.read(1):
            raise ValueError("band is longer than its declared region")
    return piece_t(region, destination)
