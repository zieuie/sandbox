#!/usr/bin/env python3
"""Print or convert portable binary DP artifacts, optionally proving optimality independently."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from artifacts import decode_dp, encode_dp, validate
from verify_dp import verify


# Keep artifact publication exclusive and durable for manual utility use.
def publish(path: Path, content: bytes) -> None:
    """Publish content at a fresh path atomically, preserving every previous output."""

    temporary=path.with_name(path.name+".tmp-"+uuid.uuid4().hex)
    try:
        with temporary.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary,path)
        descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


# Expose human-readable printing, binary conversion and independent verification in one utility.
def main() -> int:
    """Print/convert the requested artifact, or show command help without arguments."""

    parser=argparse.ArgumentParser(description="Print KHD1/JSON DP artifacts and convert compact binary output.",
        epilog="Example: ./print_dp.py split.khdp --verify; ./print_dp.py split.json --binary-out split.khdp")
    parser.add_argument("artifact",type=Path,nargs="?")
    parser.add_argument("--verify",action="store_true",help="independently recompute the optimum and tie-selected split")
    parser.add_argument("--max-visits",type=int,default=200_000_000)
    parser.add_argument("--binary-out",type=Path)
    parser.add_argument("--json-out",type=Path)
    arguments=parser.parse_args()
    if arguments.artifact is None:
        parser.print_help()
        return 0
    raw=arguments.artifact.read_bytes()
    document=decode_dp(raw) if raw.startswith(b"KHD1") else json.loads(raw)
    validate(document)
    if arguments.verify:
        verify(document,arguments.max_visits)
    if arguments.binary_out:
        publish(arguments.binary_out,encode_dp(document))
    if arguments.json_out:
        publish(arguments.json_out,(json.dumps(document,indent=2)+"\n").encode())
    print(json.dumps({**document,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"optimality_checked":arguments.verify},indent=2))
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"print_dp.py: {error}",file=sys.stderr)
        raise SystemExit(1)
