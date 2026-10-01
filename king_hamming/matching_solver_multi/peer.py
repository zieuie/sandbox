#!/usr/bin/env python3
"""A lease-owned peer: bounded control framing, pinned native child, EOF cleanup."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from matching_solver.adapter import decode_input
from matching_solver.cluster_solver import cpu_ticks
from matching_solver_multi import state


def line(stream, maximum=1_100_000):
    raw = stream.readline(maximum + 1)
    if not raw or len(raw) > maximum or not raw.endswith(b"\n"):
        raise ValueError("invalid peer control frame")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("peer control must be an object")
    return value


def copy_exact(source, target, size):
    while size:
        chunk = source.read(min(size, 1024 * 1024))
        if not chunk:
            raise ValueError("truncated peer binary frame")
        target.write(chunk)
        size -= len(chunk)


def run(args):
    # FileIO avoids a daemon control reader holding a buffered-stdin lock during
    # interpreter shutdown, and does not read ahead into binary resume payloads.
    incoming = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    config = line(incoming)
    spec = config["specification"]
    dp, _, digest = decode_input(spec)
    # The peer command carries the leased specification hash. The coordinator
    # cannot turn an authorized tunnel into an unrelated computation.
    import hashlib
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    if args.spec_hash and hashlib.sha256(canonical).hexdigest() != args.spec_hash:
        raise ValueError("peer specification differs from fenced lease")
    a = spec["arguments"]
    workers, rank = a["workers"], args.rank
    if workers != args.workers or not 0 <= rank < workers:
        raise ValueError("peer rank mismatch")
    polynomial = a["poly"]
    cpus = [int(cpu) for cpu in args.cpus.split(",")]
    threads = min(a["threads"], len(cpus))
    write_lock = threading.Lock()
    stopped = threading.Event()
    child = None

    def send(value, path=None):
        with write_lock:
            sys.stdout.buffer.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
            if path is not None:
                with path.open("rb") as source:
                    copy_exact(source, sys.stdout.buffer, value["bytes"])
            sys.stdout.buffer.flush()

    with tempfile.TemporaryDirectory(prefix="kh-multi-peer-") as folder:
        root = Path(folder)
        graph, output, image = root / "graph.txt", root / "result.bin", root / "phase.kmp"
        graph.write_text("\n".join([
            f"{dp['p']} {dp['r']} {sum(state.owned_count(dp, workers, i) for i in range(workers))} {len(dp['runs'])}",
            " ".join(map(str, polynomial)),
            *(f"{run['a']} {run['t'] * run['repeat']}" for run in dp["runs"]),
        ]) + "\n")
        resume_bytes = config.get("resume_bytes", 0)
        if resume_bytes:
            if resume_bytes != state.size(dp, workers, rank):
                raise ValueError("invalid resume byte count")
            with image.open("xb") as target:
                copy_exact(incoming, target, resume_bytes)
            with image.open("rb") as source:
                state.inspect_header(source, dp, digest, polynomial, workers, rank)
        executable = ROOT / "matching_solver_multi/kh_match_multi"
        command = [sys.executable, str(ROOT / "cluster/affinity_exec.py"), "--parent-pid", str(os.getpid()),
                   "--cpus", ",".join(map(str, cpus)), "--", str(executable), str(graph), str(rank), str(workers),
                   ",".join(config["hosts"]), str(config["port"]), str(threads), str(a["batch"]),
                   config["token"], str(output), "--checkpoint", str(image), "--checkpoint-seconds",
                   str(config["checkpoint_seconds"]), "--identity", digest.hex(), "--max-bytes",
                   str(a["owner_max_bytes"])]
        command += ["--checkpoint-phases", str(a.get("checkpoint_phases", 0))]
        if resume_bytes:
            command += ["--resume", str(image)]
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                                 env={**os.environ, "MALLOC_ARENA_MAX": "2"})

        def stop(_signum, _frame):
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def controls():
            try:
                while not stopped.is_set():
                    request = line(incoming, 1024)
                    if request == {"action": "ack"}:
                        child.stdin.write(b"\n")
                        child.stdin.flush()
                    elif request == {"action": "stop"}:
                        stop(None, None)
                    else:
                        raise ValueError("unknown peer control")
            except (OSError, ValueError, BrokenPipeError):
                # EOF means the lease-owned tunnel is gone, not graceful pause.
                if child.poll() is None:
                    child.kill()

        def heartbeat():
            previous = cpu_ticks(child.pid)
            while not stopped.wait(10):
                current = cpu_ticks(child.pid)
                if current is not None and previous is not None and current > previous:
                    send({"event": "heartbeat", "message": "native owner CPU active"})
                previous = current

        threading.Thread(target=controls, daemon=True).start()
        reporter = threading.Thread(target=heartbeat, daemon=True)
        reporter.start()
        try:
            while raw := child.stdout.readline(65537):
                if len(raw) > 65536 or not raw.endswith(b"\n"):
                    raise ValueError("oversized native report")
                value = json.loads(raw)
                if value.get("event") == "checkpoint":
                    if image.stat().st_size != state.size(dp, workers, rank):
                        raise ValueError("native image size mismatch")
                    send({**value, "bytes": image.stat().st_size}, image)
                elif "matched" in value:
                    if output.stat().st_size != 16 * state.owned_count(dp, workers, rank):
                        raise ValueError("native result size mismatch")
                    send({"event": "result", "summary": value, "bytes": output.stat().st_size}, output)
                else:
                    send(value)
            code = child.wait()
            send({"event": "exit", "code": code})
            return 0 if code in (0, 75) else 1
        finally:
            stopped.set()
            if child.poll() is None:
                child.kill()
            child.wait()
            reporter.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--cpus", required=True)
    parser.add_argument("--spec-hash")
    return run(parser.parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"partitioned peer: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
