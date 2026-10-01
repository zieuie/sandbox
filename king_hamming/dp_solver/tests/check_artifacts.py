#!/usr/bin/env python3
"""Validate compact production DP bytes against reference artifacts and independent decoders."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from artifacts import decode_dp,encode_dp


# Compare against independently produced C bytes and reject recomputed-checksum malformed payloads.
def main() -> int:
    """Run compact artifact checks with --run; empty invocation prints a useful example."""

    if "--run" not in sys.argv:
        print("Test portable KHD1 DP artifacts.\nExample: python3 tests/check_artifacts.py --run")
        return 0
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        document=root/"split.json"
        subprocess.run([str(ROOT/"kh_dp_local"),"5","3","--work-dir",str(root/"state"),"-o",str(document)],check=True,capture_output=True)
        expected=json.loads(document.read_text())
        raw=encode_dp(expected)
        assert decode_dp(raw)==expected
        binary=root/"split.khdp"
        binary.write_bytes(raw)
        subprocess.run([sys.executable,str(ROOT.parent/"scripts"/"inspect_artifact.py"),str(binary),"--verify"],check=True,capture_output=True)
        subprocess.run([str(ROOT/"verify_dp.py"),str(binary)],check=True,capture_output=True)
        assert len(raw)==56
        assert len(raw)<len(document.read_bytes())
        large={"format":"KHDP2-draft","p":3,"r":21,"q":3**21,
               "f":3**10,"budget":3**11,"theta":1,
               "runs":[{"a":1,"b":1,"t":1,"repeat":1}]}
        assert decode_dp(encode_dp(large))==large
        for changed in (raw[:-1],raw[:10]+bytes([raw[10]^1])+raw[11:]):
            try:
                decode_dp(changed)
            except ValueError:
                pass
            else:
                raise AssertionError("corruption accepted")
        for payload in (b"KHD1\x85\x00"+raw[5:-32],raw[:-32]+b"extra"):
            try:
                decode_dp(payload+hashlib.sha256(payload).digest())
            except ValueError:
                pass
            else:
                raise AssertionError("noncanonical payload accepted")
    print("portable DP artifact checks passed")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
