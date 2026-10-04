#!/usr/bin/env python3
"""Cluster bridge for match_gpu_blocks: lock the leased GPU, run the block kernel, publish KHM1."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "cluster"))
from matching_solver.artifacts import header, load_dp, publish, request_count, verify  # noqa: E402
import gpus  # noqa: E402

KERNEL = HERE / "kh_gpu_block_kernel"


def emit(record: dict) -> None:
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


# Stages of a block run in order, with the phase text the leader shows. The agent adds
# "verify" after this bridge exits; the dashboard knows the whole list (static/matching.js).
STAGES = {"gpu_wait": "waiting for the GPU", "field": "building field rows", "blocks": "matching blocks",
          "exchange": "exchange rounds", "write": "writing the result", "publish": "publishing"}
EMIT_SECONDS = 10.0


class Live:
    """Where a block run is: per-stage progress and the unmatched-request burndown.

    Sent as the progress message (JSON), so the dashboard can draw every stage and the
    burndown while the run is still going. Stage changes and finished blocks are sent at once;
    progress inside a stage at most every EMIT_SECONDS.
    """

    def __init__(self, total: int) -> None:
        self.total, self.matched = total, 0
        self.stages: dict[str, dict] = {}
        self.current: str | None = None
        self.trace = [[0, total]]
        self.last_emit = 0.0
        self.extra: dict = {}
        self.phase = ""
        self.lock = threading.Lock()
        # Re-send the current state when a stage is quiet (a block can take minutes), so the
        # leader keeps seeing the solver alive.
        threading.Thread(target=self.heartbeat, daemon=True).start()

    def heartbeat(self) -> None:
        while True:
            time.sleep(EMIT_SECONDS)
            with self.lock:
                if self.current is not None and time.time() - self.last_emit >= EMIT_SECONDS:
                    self.send()

    def send(self) -> None:
        self.last_emit = time.time()
        emit({"done": self.matched, "total": self.total, "checkpoint_done": 0,
              "phase": self.phase, "units": "requests", "message": self.message()})

    def update(self, key: str, done: int, total: int | None, phase: str | None = None, force: bool = False) -> None:
        with self.lock:
            self.advance(key, done, total, phase, force)

    def advance(self, key: str, done: int, total: int | None, phase: str | None, force: bool) -> None:
        now = time.time()
        if key != self.current:
            if self.current is not None:
                self.stages[self.current]["finished"] = now
                self.stages[self.current]["done"] = self.stages[self.current].get("total") or \
                    self.stages[self.current]["done"]
            self.stages.setdefault(key, {"started": now, "finished": None})
            self.current, force = key, True
        self.stages[key].update(done=done, total=total, updated=now)
        self.phase = phase or STAGES[key]
        if force or now - self.last_emit >= EMIT_SECONDS:
            self.send()

    def finish(self) -> None:
        with self.lock:
            if self.current is not None:
                self.stages[self.current]["finished"] = time.time()
            self.current = None   # stops the heartbeat; the final record follows

    def record(self) -> dict:
        return {**self.extra, "stages": [{"key": key, **value} for key, value in self.stages.items()],
                "trace": self.trace}

    def message(self) -> str:
        return json.dumps(self.record(), separators=(",", ":"))

    def kernel_line(self, record: dict) -> None:
        """Fold one kernel progress line into the stages and the burndown."""
        stage = record.get("stage")
        if stage not in STAGES:
            return
        self.matched = int(record.get("done", self.matched))
        done, total = int(record.get("stage_done", 0)), int(record.get("stage_total", 0)) or None
        step = stage in ("blocks", "exchange") and done > 0
        if step and (stage, done) != getattr(self, "_last_step", None):
            self._last_step = (stage, done)
            self.trace.append([len(self.trace), self.total - self.matched])
        phase = f"block {done}/{total}" if stage == "blocks" else STAGES[stage]
        self.update(stage, done, total, phase, force=step)


def run(arguments: argparse.Namespace) -> int:
    dp, digest = load_dp(arguments.dp)
    total = request_count(dp)
    polynomial = [int(value) for value in arguments.poly.split(",")]

    # A local restart may find a fully published certificate; the agent validates it again.
    if arguments.output.exists():
        summary = verify(arguments.output, dp, digest, arguments.max_bytes)
        if summary["polynomial"] != polynomial:
            raise ValueError("existing certificate uses a different pinned field")
        emit({"done": summary["matched"], "total": total, "checkpoint_done": 0,
              "phase": "complete", "units": "requests"})
        return 0

    blocks = arguments.output.with_name("blocks.txt")
    raw = arguments.output.with_name("native-choices.bin")
    scratch = arguments.output.with_name("choices.scratch")   # the kernel maps, then unlinks it
    raw.unlink(missing_ok=True)
    scratch.unlink(missing_ok=True)
    with blocks.open("w") as stream:
        stream.write(f"{len(dp['runs'])}\n")
        for entry in dp["runs"]:
            stream.write(f"{entry['a']} {entry['t'] * entry['repeat']}\n")

    device = int(os.environ.get("KH_GPU_DEVICE", "0"))
    lock = gpus.DeviceLock(device)
    live = Live(total)
    live.update("gpu_wait", 0, None)
    # Opportunistic DP tiles hold the device for seconds at a time; wait for them.
    if not lock.acquire(timeout=arguments.lock_seconds, hold="long"):
        raise RuntimeError(f"GPU {device} stayed busy for {arguments.lock_seconds} s")
    metadata = None
    try:
        command = [str(KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(raw),
                   "--poly", arguments.poly, "--threads", str(arguments.threads),
                   "--max-bytes", str(arguments.max_bytes), "--device", str(device),
                   "--choice-file", str(scratch)]
        if arguments.block_device_bytes:
            command += ["--block-device-bytes", str(arguments.block_device_bytes)]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=sys.stderr, text=True) as child:
            assert child.stdout is not None
            for line in child.stdout:
                record = json.loads(line)
                if "polynomial" in record and "status" in record:
                    metadata = record
                elif record.get("event") == "resource_usage":
                    emit(record)
                elif "stage" in record:
                    live.kernel_line(record)
                elif "done" in record:
                    emit(record)
            code = child.wait()
    finally:
        lock.release()
    if code == 4 and metadata is not None:
        # Block matching is a heuristic exchange: incomplete never means that no matching exists.
        raise RuntimeError(f"block matching incomplete: {metadata['residual']} requests unmatched after "
                           f"{metadata['rounds']} rounds in {metadata['blocks']} blocks (not an obstruction)")
    if code != 0 or metadata is None or metadata["status"] != 0:
        raise RuntimeError(f"GPU block matching kernel failed: exit={code}")
    if metadata["polynomial"] != polynomial:
        raise RuntimeError("GPU kernel used a different polynomial")

    # The agent independently verifies the KHM1 (validate_result) before publication.
    live.matched = metadata["matched"]
    live.update("publish", 0, raw.stat().st_size)
    publish(arguments.output, header(dp, digest, metadata), raw,
            progress=lambda copied, size: live.update("publish", copied, size))
    live.finish()
    raw.unlink(missing_ok=True)
    summary = {key: metadata[key] for key in ("matched", "required", "phases", "scans", "engine", "device",
                                             "blocks", "rounds", "residual_round1", "imported")}
    summary["seconds"] = metadata.get("seconds")
    live.extra = summary
    live.trace = metadata.get("trace") or live.trace  # the kernel's own [[step, unmatched], ...] record
    emit({"done": metadata["matched"], "total": metadata["required"], "checkpoint_done": 0,
          "phase": "complete", "units": "requests", "message": live.message()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: python3 cluster_solver.py --dp input.khdp --output result.khmatch "
        "--poly 2,3,0,1 --threads 4 --max-bytes 4294967296"))
    parser.add_argument("--dp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poly", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--block-device-bytes", type=int, default=0)
    parser.add_argument("--lock-seconds", type=float, default=1800)
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    arguments = parser.parse_args()
    if arguments.threads < 1 or arguments.max_bytes < 1:
        parser.error("invalid resource controls")
    return run(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"gpu_block_match_solver/cluster_solver.py: {error}", file=sys.stderr)
        raise SystemExit(1)
