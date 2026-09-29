"""Private, self-cleaning SSH staging for leased matching worker processes."""

from __future__ import annotations

from contextlib import contextmanager
import io
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parent.parent
NATIVE_FILES = (ROOT / "matching_solver/kh_match_worker",)
STAGE = '''import pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1])
if root.parent!=pathlib.Path('/tmp') or not root.name.startswith('kh-match-worker-'):
    raise ValueError('invalid private stage path')
root.mkdir(mode=0o700)
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
    archive.extractall(root,filter='data')
'''
CLEAN = '''import pathlib,shutil,sys
root=pathlib.Path(sys.argv[1])
if root.parent!=pathlib.Path('/tmp') or not root.name.startswith('kh-match-worker-'):
    raise ValueError('invalid private stage path')
if root.exists():
    shutil.rmtree(root)
'''


# Bundle only exact trusted runtime files needed by an SSH shard.
def archive(files=NATIVE_FILES) -> bytes:
    """Return a small tar byte string with the native worker."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as bundle:
        for source in files:
            bundle.add(source, arcname=str(source.relative_to(ROOT)))
    return output.getvalue()


# Use one shell-quoted remote command with no interpolation of untrusted paths.
def remote(host: str, code: str, directory: str, data: bytes = b"") -> None:
    """Run trusted Python code on host with one owned directory and optional stdin."""
    if not isinstance(host, str) or re.fullmatch(r"[A-Za-z0-9.@_-]+", host) is None or host.startswith("-"):
        raise ValueError("invalid SSH host")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
               shlex.join(["python3", "-c", code, directory])]
    subprocess.run(command, input=data, check=True, timeout=30)


# A solver lease owns only a fresh temporary directory on the selected partner.
@contextmanager
def staged(host: str, native: bool = True):
    """Yield a fresh remote runtime path and remove it after worker shutdown."""
    if not native:
        raise ValueError("only native matching workers are supported")
    directory = f"/tmp/kh-match-worker-{uuid.uuid4().hex}"
    try:
        remote(host, STAGE, directory, archive())
        yield directory
    finally:
        remote(host, CLEAN, directory)
