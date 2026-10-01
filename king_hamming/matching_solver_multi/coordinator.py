#!/usr/bin/env python3
"""Lease-fenced native owners with a durable all-owner phase barrier."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from matching_solver.adapter import decode_input
from matching_solver.artifacts import verify
from matching_solver.native_coordinator import peer, process_worker
from matching_solver_multi import state
from matching_solver_multi.peer import line, copy_exact
from matching_solver_multi.run import export


@contextmanager
def active_stage(phase, done, total):
    """Report actual Python CPU work during streaming export/verification.

    These stages can take hours on large fields. Silence must not look like a
    dead solver, but a timer alone must not conceal a genuinely stalled one.
    """
    finished = threading.Event()
    def report():
        previous = time.process_time()
        while not finished.wait(10):
            current = time.process_time()
            if current > previous + 0.01:
                print(json.dumps(dict(done=done, total=total, phase=phase,
                                      heartbeat=True, units="requests")), flush=True)
            previous = current
    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    try:
        yield
    finally:
        finished.set()
        reporter.join()


def run(args):
    spec = json.loads(args.specification.read_text())
    from matching_solver_multi.adapter import PartitionedAdapter
    PartitionedAdapter().validate(spec)
    dp, _, digest = decode_input(spec)
    a = spec["arguments"]
    count = a["workers"]
    participants = json.loads(args.participants.read_text())
    if len(participants) != count:
        raise ValueError("wrong participant count")
    root = args.output.parent
    images = root / "owner-state"
    restored = images.exists()
    paths = {state.name(i): images / state.name(i) for i in range(count)}
    phase, matched = state.validate(paths, dp, digest, a["poly"], count) if restored else (0, 0)
    if args.output.exists():
        with active_stage("verify retained certificate", matched, dp["q"]):
            proof = verify(args.output, dp, digest, a["coordinator_max_bytes"])
        if proof["polynomial"] != a["poly"]:
            raise ValueError("existing result polynomial mismatch")
        print(json.dumps(dict(done=proof["matched"], total=proof["required"], phase="complete")), flush=True)
        return 0
    images.mkdir(exist_ok=True)
    workers, readers, writers, threads, locks = [], [], [], [], []
    events = queue.Queue()
    closing = threading.Event()
    stopping = threading.Event()
    activity = [time.monotonic()]
    total = sum(state.owned_count(dp, count, i) for i in range(count))
    identity = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    config = dict(specification=spec, hosts=[p["host"] for p in participants],
                  port=20000 + secrets.randbelow(20000), token=secrets.token_hex(16),
                  checkpoint_seconds=args.checkpoint_seconds)

    def control(rank, message):
        with locks[rank]:
            writers[rank].write((json.dumps(message) + "\n").encode())
            writers[rank].flush()

    def receive(rank):
        try:
            while not closing.is_set():
                message = line(readers[rank], 65536)
                event = message.get("event")
                if event in {"checkpoint", "result"}:
                    expected = (state.size(dp, count, rank) if event == "checkpoint"
                                else 16 * state.owned_count(dp, count, rank))
                    if type(message.get("bytes")) is not int or message["bytes"] != expected:
                        raise ValueError("owner binary size mismatch")
                    target = paths[state.name(rank)] if event == "checkpoint" else root / f"owner-{rank}.bin"
                    temporary = target.with_suffix(target.suffix + ".incoming")
                    if shutil.disk_usage(root).free < expected + 1024**2:
                        raise OSError("insufficient disk for owned transfer")
                    with temporary.open("wb") as output:
                        remaining = expected
                        while remaining:
                            chunk = readers[rank].read(min(remaining, 1024**2))
                            if not chunk:
                                raise ValueError("truncated owned transfer")
                            output.write(chunk)
                            remaining -= len(chunk)
                            activity[0] = time.monotonic()
                        output.flush()
                        os.fsync(output.fileno())
                    temporary.replace(target)
                activity[0] = time.monotonic()
                events.put((rank, message))
                if event == "exit":
                    return
        except Exception as error:
            events.put((rank, {"event": "error", "message": str(error)}))

    def stop_all():
        stopping.wait()
        if closing.is_set():
            return
        for rank in range(count):
            try:
                control(rank, {"action": "stop"})
            except (OSError, ValueError):
                pass

    def signal_stop(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGTERM, signal_stop)
    signal.signal(signal.SIGINT, signal_stop)
    try:
        for rank, participant in enumerate(participants):
            cpus = participant["cpus"]
            if participant.get("address"):
                if not args.run_id or not args.lease_token or rank == 0:
                    raise ValueError("missing fenced participant lease")
                worker = peer(participant["address"], args.run_id, args.lease_token, rank, count)
            else:
                command = [sys.executable, str(ROOT / "cluster/affinity_exec.py"), "--parent-pid", str(os.getpid()),
                           "--cpus", cpus, "--", sys.executable, str(ROOT / "matching_solver_multi/peer.py"),
                           "--rank", str(rank), "--workers", str(count), "--cpus", cpus, "--spec-hash", identity]
                worker = process_worker(command)
            workers.append(worker)
            readers.append(os.fdopen(os.dup(worker.read_descriptor), "rb"))
            writers.append(os.fdopen(os.dup(worker.write_descriptor), "wb"))
            locks.append(threading.Lock())
            with locks[rank]:
                resume = paths[state.name(rank)] if restored else None
                writers[rank].write((json.dumps({**config, "resume_bytes": resume.stat().st_size if resume else 0}) + "\n").encode())
                if resume:
                    with resume.open("rb") as source:
                        copy_exact(source, writers[rank], resume.stat().st_size)
                writers[rank].flush()
            reader = threading.Thread(target=receive, args=(rank,), daemon=True)
            reader.start()
            threads.append(reader)
        threading.Thread(target=stop_all, daemon=True).start()
        exited, pending, results = {}, {}, {}
        last_report = 0
        while len(exited) != count:
            try:
                rank, message = events.get(timeout=1)
            except queue.Empty:
                if time.monotonic() - activity[0] > 300:
                    raise TimeoutError("partitioned group made no observable progress for five minutes")
                if time.monotonic() - last_report >= 10 and activity[0] > last_report:
                    print(json.dumps(dict(done=matched, total=total, checkpoint_done=matched if restored else 0,
                                          phase="partitioned matching", heartbeat=True)), flush=True)
                    last_report = time.monotonic()
                continue
            event = message.get("event")
            if event == "error":
                raise RuntimeError(f"owner {rank}: {message['message']}")
            if event == "exit":
                if message["code"] not in (0, 75):
                    raise RuntimeError(f"owner {rank} failed: exit={message['code']}")
                exited[rank] = message["code"]
            elif event == "progress":
                if rank == 0:
                    print(json.dumps({**message, "checkpoint_done": matched, "phase": "partitioned matching", "units": "requests"}), flush=True)
            elif event == "checkpoint":
                if rank in pending:
                    raise ValueError("overlapping owner checkpoint")
                pending[rank] = (message["cursor"], message["done"])
                if len(pending) == count:
                    if len(set(pending.values())) != 1:
                        raise ValueError("owners disagree on checkpoint progress")
                    next_phase, done = pending[0]
                    if next_phase < phase:
                        raise ValueError("checkpoint cursor moved backwards")
                    state.validate(paths, dp, digest, a["poly"], count, next_phase, done)
                    # The generic supervisor hashes, replicates and publishes
                    # this immutable phase before returning the acknowledgement.
                    print(json.dumps(dict(event="checkpoint", cursor=next_phase, done=done, total=total)), flush=True)
                    if args.checkpoint_handshake and sys.stdin.readline() != "\n":
                        raise RuntimeError("durable checkpoint acknowledgement missing")
                    phase, matched, restored = next_phase, done, True
                    pending.clear()
                    print(json.dumps(dict(done=done, total=total, checkpoint_done=done,
                                          phase="checkpoint committed", units="requests")), flush=True)
                    for owner in range(count):
                        control(owner, {"action": "ack"})
            elif event == "result":
                if rank in results:
                    raise ValueError("duplicate owner result")
                results[rank] = message["summary"]
        if any(code == 75 for code in exited.values()):
            if not all(code == 75 for code in exited.values()) or not restored:
                raise RuntimeError("inconsistent group pause")
            return 75
        if pending or len(results) != count:
            raise RuntimeError("incomplete group result")
    finally:
        closing.set()
        stopping.set()
        for writer in writers:
            try:
                writer.close()
            except OSError:
                pass
        for worker in workers:
            worker.close()
        for thread in threads:
            thread.join(timeout=3)
        for reader in readers:
            reader.close()
    # No native owner remains resident while the independent verifier builds
    # its field arrays. Export uses a streaming canonical merge.
    certificate = root / "result.khmatch"
    if not certificate.exists():
        with active_stage("pack certificate", matched, total):
            certificate = export(root, dp, digest, a["poly"], [results[i] for i in range(count)])
    with active_stage("verify certificate", matched, total):
        proof = verify(certificate, dp, digest, a["coordinator_max_bytes"])
    if certificate != args.output:
        os.replace(certificate, args.output)
    print(json.dumps(dict(done=proof["matched"], total=proof["required"], checkpoint_done=matched,
                          phase="complete", units="requests")), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specification", type=Path, required=True)
    parser.add_argument("--participants", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint-seconds", type=int, default=1800)
    parser.add_argument("--checkpoint-handshake", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--lease-token")
    return run(parser.parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"partitioned coordinator: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
