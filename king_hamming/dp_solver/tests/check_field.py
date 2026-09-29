#!/usr/bin/env python3
"""Compare the shared field builder with independent polynomial arithmetic and reference SUD sets."""

from __future__ import annotations

from array import array
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


# Construct fields through the C CLI and compare every bucket with the independent reference.
def main() -> int:
    """Run fixed-X and threading checks with --run; otherwise print an example."""

    if "--run" not in sys.argv:
        print("Test shared primitive-X field tables.\nExample: python3 tests/check_field.py --run")
        return 0
    spec=importlib.util.spec_from_file_location("reference_field",ROOT.parent/"scripts"/"inspect_artifact.py")
    reference=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    workers=min(2,len(os.sched_getaffinity(0)))
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        for p,r in ((2,3),(2,5),(3,3),(5,3),(7,5)):
            expected=None
            for threads in sorted({1,workers}):
                output=root/f"field-{p}-{r}-{threads}.bin"
                completed=subprocess.run([str(ROOT/"kh_field"),str(p),str(r),"--threads",str(threads),"-o",str(output)],check=True,capture_output=True)
                metadata=json.loads(completed.stdout.splitlines()[0])
                polynomial=metadata["polynomial"]
                independent=reference.Field(p,r,polynomial)
                assert independent.coefficients[2]==(0,1)+(0,)*(r-2),"generator is not X"
                entries=array("I")
                entries.frombytes(output.read_bytes())
                flattened=[label for g in range(p) for suffix in range(p**(r//2)) for label in independent.cells[g,suffix]]
                assert list(entries)==flattened,(p,r,threads)
                if expected is not None:
                    assert entries.tobytes()==expected
                expected=entries.tobytes()
                rejected=subprocess.run([str(ROOT/"kh_field"),str(p),str(r),"-o",str(output)],capture_output=True)
                assert rejected.returncode!=0
        rejected=subprocess.run([str(ROOT/"kh_field"),"13","5","--max-bytes","1024"],capture_output=True)
        assert rejected.returncode!=0
        rejected=subprocess.run([str(ROOT/"kh_field"),"4","3"],capture_output=True)
        assert rejected.returncode!=0
    print("shared field checks passed")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
