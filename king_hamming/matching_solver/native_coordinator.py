#!/usr/bin/env python3
"""Establish cluster streams, then hand all matching work to native C processes."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matching_solver import worker_transport


class NativeWorker:
    """Own a native worker transport while exposing only inherited descriptors."""

    def __init__(self, process=None, connection=None, reader=None):
        self.process = process
        self.connection = connection
        self.reader = reader

    @property
    def read_descriptor(self) -> int:
        if self.connection is not None:
            return self.connection.fileno()
        assert self.process is not None and self.process.stdout is not None
        return self.process.stdout.fileno()

    @property
    def write_descriptor(self) -> int:
        if self.connection is not None:
            return self.connection.fileno()
        assert self.process is not None and self.process.stdin is not None
        return self.process.stdin.fileno()

    def close(self) -> None:
        """Release descriptors and stop only a worker still left behind by its coordinator."""
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if self.reader is not None:
                self.reader.close()
            self.connection.close()
            return
        assert self.process is not None
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def peer(address: str, run_id: str, lease_token: str,
         index: int, count: int) -> NativeWorker:
    """Authenticate one reserved agent stream without interpreting its binary payload."""
    parsed = urlparse(address)
    if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
        raise ValueError("peer address must be an HTTP agent URL with a port")
    connection = socket.create_connection((parsed.hostname, parsed.port), timeout=15)
    connection.settimeout(None)
    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    request = (f"CONNECT /v1/peer HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
               f"X-Run-Id: {run_id}\r\nX-Lease-Token: {lease_token}\r\n"
               f"X-Worker-Index: {index}\r\nX-Worker-Count: {count}\r\n\r\n")
    connection.sendall(request.encode("ascii"))
    reader = connection.makefile("rb", buffering=0)
    status = reader.readline(4096)
    if not status.startswith((b"HTTP/1.0 200 ", b"HTTP/1.1 200 ")):
        reader.close()
        connection.close()
        raise RuntimeError(f"reserved peer rejected native stream: {status[:200]!r}")
    for _ in range(100):
        line = reader.readline(4096)
        if line in (b"\r\n", b"\n"):
            return NativeWorker(connection=connection, reader=reader)
        if not line:
            break
    reader.close()
    connection.close()
    raise RuntimeError("reserved peer returned an invalid HTTP handshake")


def process_worker(command: list[str]) -> NativeWorker:
    """Launch one native shard with binary stdin/stdout and inherited diagnostics."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=sys.stderr, bufsize=0)
    return NativeWorker(process=process)


def run(args: argparse.Namespace) -> int:
    """Connect workers and transfer their streams to the native coordinator."""
    if not 2 <= len(args.worker) <= 256:
        raise ValueError("native distributed matching requires two to 256 workers")
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    available = sorted(os.sched_getaffinity(0))
    worker_threads = ([args.threads_per_worker] * len(args.worker)
                      if args.worker_threads is None else args.worker_threads)
    if (len(worker_threads) != len(args.worker) or
            any(not 1 <= value <= 1024 for value in worker_threads) or
            sum(worker_threads) > 256):
        raise ValueError("--worker-threads must give one positive count per worker")
    if sum(worker_threads[index] for index, host in enumerate(args.worker) if host == "local") > len(available):
        raise ValueError("local native workers exceed assigned CPU affinity")
    workers: list[NativeWorker] = []
    local_offset = 0
    try:
        for index, host in enumerate(args.worker):
            if host.startswith("http://"):
                if not args.peer_run_id or not args.peer_lease_token or index == 0:
                    raise ValueError("reserved native peer lacks its fenced lease")
                workers.append(peer(host, args.peer_run_id, args.peer_lease_token,
                                    index, len(args.worker)))
                continue
            if host == "local":
                cpus = available[local_offset:local_offset + worker_threads[index]]
                local_offset += worker_threads[index]
                executable = Path(__file__).with_name("kh_match_worker")
                command = [str(executable), "--index", str(index), "--count", str(len(args.worker)),
                           "--cpus", ",".join(map(str, cpus))]
            else:
                if args.remote_root is None:
                    raise ValueError("--remote-root is required for a native SSH worker")
                executable = Path(args.remote_root) / "matching_solver/kh_match_worker"
                command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                           str(executable), "--index", str(index), "--count", str(len(args.worker))]
                if args.remote_cpus:
                    command.extend(["--cpus", args.remote_cpus])
            workers.append(process_worker(command))
        coordinator = [str(Path(__file__).with_name("kh_match_distributed")),
                       str(args.dp), str(args.output), "--poly", args.poly,
                       "--threads-per-worker", str(args.threads_per_worker),
                       "--worker-threads", ",".join(map(str, worker_threads)),
                       "--max-bytes", str(args.max_bytes),
                       "--max-edges", str(args.max_edges),
                       "--max-field-elements", str(args.max_field_elements),
                       "--checkpoint-seconds", str(args.checkpoint_seconds)]
        coordinator.extend(["--phase-batch-roots", str(args.phase_batch_roots)])
        if args.checkpoint_dir:
            coordinator.extend(["--checkpoint-dir", str(args.checkpoint_dir)])
        if args.resume:
            coordinator.extend(["--resume", str(args.resume)])
        if args.stop_after_phases:
            coordinator.extend(["--stop-after-phases", str(args.stop_after_phases)])
        if args.checkpoint_handshake:
            coordinator.append("--checkpoint-handshake")
        descriptors = set()
        for worker in workers:
            read_fd = worker.read_descriptor
            write_fd = worker.write_descriptor
            coordinator.extend(["--worker-fd", f"{read_fd},{write_fd}"])
            descriptors.update((read_fd, write_fd))
        completed = subprocess.run(coordinator, pass_fds=tuple(descriptors), check=False)
        return completed.returncode
    finally:
        for worker in reversed(workers):
            worker.close()


def main() -> int:
    """Parse cluster-only controls and launch the native computation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dp", type=Path, nargs="?")
    parser.add_argument("--poly")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--worker", action="append", default=[])
    parser.add_argument("--remote-root")
    parser.add_argument("--stage-remote", action="store_true")
    parser.add_argument("--remote-cpus")
    parser.add_argument("--threads-per-worker", type=int, default=1)
    parser.add_argument("--worker-threads", type=lambda value: [int(item) for item in value.split(",")])
    parser.add_argument("--peer-run-id")
    parser.add_argument("--peer-lease-token")
    parser.add_argument("--max-edges", type=int, default=100_000_000)
    parser.add_argument("--max-field-elements", type=int, default=100_000_000)
    parser.add_argument("--max-bytes", type=int, default=2**31)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--stop-after-phases", type=int, default=0)
    parser.add_argument("--restart-attempts", type=int, default=0)
    parser.add_argument("--checkpoint-seconds", type=int, default=0)
    parser.add_argument("--phase-batch-roots", type=int, default=65536)
    parser.add_argument("--checkpoint-handshake", action="store_true")
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args()
    if args.dp is None or args.poly is None or args.output is None:
        parser.error("DP, --poly, and --output are required")
    if (not 1 <= args.threads_per_worker <= 1024 or args.max_edges < 1 or
            args.max_field_elements < 1 or args.max_bytes < 1 or
            args.stop_after_phases < 0 or args.checkpoint_seconds < 0 or
            args.phase_batch_roots < 1 or args.restart_attempts != 0):
        parser.error("invalid native resource controls")
    if args.stage_remote:
        remote_hosts = sorted({host for host in args.worker if host != "local"
                               and not host.startswith("http://")})
        if len(remote_hosts) != 1:
            parser.error("native automatic staging requires exactly one SSH partner")
        with worker_transport.staged(remote_hosts[0], native=True) as root:
            args.remote_root = root
            return run(args)
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"native_coordinator.py: {error}", file=sys.stderr)
        raise SystemExit(1)
