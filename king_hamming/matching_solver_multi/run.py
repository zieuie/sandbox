#!/usr/bin/env python3
"""Launch isolated native owners, assemble KHM1, and retain measurements."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import heapq
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from matching_solver.artifacts import header, load_dp, publish, request_count, verify
from matching_solver.polynomials import first_primitive


def checked(command, **kwargs):
    return subprocess.run(command, check=True, timeout=30, **kwargs)


def assignments(paths):
    """Merge canonically sorted owned records with O(number of owners) heap state."""
    def records(stream):
        previous = -1
        while raw := stream.read(16):
            if len(raw) != 16:
                raise ValueError("truncated owner output")
            record = struct.unpack("<IIII", raw)
            if record[0] <= previous:
                raise ValueError("noncanonical owner output")
            previous = record[0]
            yield record
    with ExitStack() as stack:
        streams = [records(stack.enter_context(path.open("rb"))) for path in paths]
        yield from heapq.merge(*streams)


def write_packed(stream, values, bits):
    accumulator = available = 0
    buffer = bytearray()
    for value in values:
        if not 0 <= value < 1 << bits:
            raise ValueError("choice outside packed bounds")
        accumulator |= value << available
        available += bits
        while available >= 8:
            buffer.append(accumulator & 255)
            accumulator >>= 8
            available -= 8
        if len(buffer) >= 65536:
            stream.write(buffer)
            buffer.clear()
    if available:
        buffer.append(accumulator)
    stream.write(buffer)


def export(directory, dp, digest, polynomial, summaries):
    n = request_count(dp)
    matched = summaries[0]["matched"]
    if any(s["matched"] != matched or s["required"] != n for s in summaries):
        raise ValueError("owners disagree on cardinality")
    status = int(matched != n)
    paths = [directory / f"owner-{i}.bin" for i in range(len(summaries))]
    def choices():
        count = cardinality = 0
        for uid, mate, choice, _ in assignments(paths):
            if uid != count:
                raise ValueError("missing or repeated canonical request")
            count += 1
            if mate == 2**32 - 1:
                if not status:
                    raise ValueError("unmatched request in a full matching")
                yield 0
            else:
                if mate >= dp["q"] or choice >= dp["f"]:
                    raise ValueError("invalid owned assignment")
                cardinality += 1
                yield choice + status
        if count != n or cardinality != matched:
            raise ValueError("owned output coverage mismatch")
    payload = directory / "payload.bin"
    # Scratch, not a published artifact: a killed export may leave a partial
    # payload behind. Rebuild it on retry; publish() still refuses replacement
    # of an existing immutable certificate.
    with payload.open("wb") as stream:
        write_packed(stream, choices(), (dp["f"] + status - 1).bit_length())
        if status:
            write_packed(stream, (r[3] for r in assignments(paths)), 1)
    metadata = dict(p=dp["p"], r=dp["r"], required=n, matched=matched,
                    status=status, polynomial=polynomial)
    output = directory / "result.khmatch"
    publish(output, header(dp, digest, metadata), payload)
    return output


def run(args):
    dp, digest = load_dp(args.dp)
    if args.timeout <= 0:
        raise ValueError("timeout must be positive; unbounded remote runs are not supported")
    polynomial = ([int(x) for x in args.poly.split(",")] if args.poly else
                  first_primitive(dp["p"], dp["r"], dp["q"]))
    hosts = args.hosts.split(",")
    if not 1 <= len(hosts) <= 16 or not 1 <= args.threads <= 64 or not 1 <= args.batch <= 1048576:
        raise ValueError("invalid worker, thread or batch count")
    if any(host != "local" for host in hosts) and "local" in hosts:
        raise ValueError("use either all local or all remote owners")
    for host in hosts:
        if host != "local":
            ipaddress.IPv4Address(host)
    if hosts[0] != "local" and len(set(hosts)) != len(hosts):
        raise ValueError("one remote owner per machine")
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    graph = directory / "graph.txt"
    graph.write_text("\n".join([
        f"{dp['p']} {dp['r']} {request_count(dp)} {len(dp['runs'])}",
        " ".join(map(str, polynomial)),
        *(f"{r['a']} {r['t'] * r['repeat']}" for r in dp["runs"]),
    ]) + "\n")
    addresses = ["127.0.0.1" if host == "local" else host for host in hosts]
    if args.port:
        port = args.port
    else:
        # Remote owners still validate their own binds; no existing listener is displaced.
        port = 20000 + secrets.randbelow(20000)
    token = secrets.token_hex(16)
    available = sorted(os.sched_getaffinity(0))
    if hosts[0] == "local" and len(hosts) * args.threads > len(available):
        raise ValueError("not enough disjoint local CPUs")
    remote_root = "/tmp/kh-multi-" + token
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if hosts[0] != "local":
        for host in hosts:
            checked([*ssh, host, shlex.join(["mkdir", "-p", remote_root])])
            checked(["scp", "-q", str(ROOT / "kh_match_multi"), str(graph), f"{host}:{remote_root}/"])
    processes = []
    started = time.monotonic()
    with ExitStack() as stack:
        try:
            for rank, host in enumerate(hosts):
                if host == "local":
                    cpus = available[rank*args.threads:(rank+1)*args.threads]
                    executable, input_file = ROOT / "kh_match_multi", graph
                    output = directory / f"owner-{rank}.bin"
                else:
                    cpus = args.remote_cpus.split(",") if args.remote_cpus else range(args.threads)
                    executable, input_file = remote_root + "/kh_match_multi", remote_root + "/graph.txt"
                    output = remote_root + f"/owner-{rank}.bin"
                command = ["timeout", "--kill-after=5", str(args.timeout), "taskset", "-c", ",".join(map(str, cpus)),
                           str(executable), str(input_file), str(rank), str(len(hosts)), ",".join(addresses),
                           str(port), str(args.threads), str(args.batch), token, str(output)]
                if host != "local":
                    command = [*ssh, host, shlex.join(command)]
                stdout = stack.enter_context((directory / f"owner-{rank}.json").open("xb"))
                stderr = stack.enter_context((directory / f"owner-{rank}.log").open("xb"))
                processes.append(subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True))
            deadline = time.monotonic() + args.timeout + 10
            while any(p.poll() is None for p in processes):
                if any(p.poll() not in (None, 0) for p in processes):
                    raise RuntimeError(f"owner failed; inspect logs in {directory}")
                if time.monotonic() > deadline:
                    raise TimeoutError("owner deadline exceeded")
                time.sleep(0.05)
            if any(p.returncode for p in processes):
                raise RuntimeError(f"owner failed; inspect logs in {directory}")
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
            for process in processes:
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    native_wall = time.monotonic() - started
    if hosts[0] != "local":
        for rank, host in enumerate(hosts):
            checked(["scp", "-q", f"{host}:{remote_root}/owner-{rank}.bin", str(directory / f"owner-{rank}.bin")])
    summaries = [json.loads((directory / f"owner-{rank}.json").read_text()) for rank in range(len(hosts))]
    output = export(directory, dp, digest, polynomial, summaries)
    report = dict(dp=str(args.dp.resolve()), polynomial=polynomial, hosts=hosts, threads_per_owner=args.threads,
                  batch=args.batch, native_wall_seconds=native_wall,
                  solve_wall_seconds=time.monotonic() - started, owners=summaries,
                  certificate=str(output), remote_scratch=remote_root if hosts[0] != "local" else None,
                  verified=False)
    if args.verify:
        verify_start = time.monotonic()
        report["verification"] = verify(output, dp, digest)
        report["verify_seconds"] = time.monotonic() - verify_start
        report["verified"] = True
    (directory / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dp", type=Path)
    parser.add_argument("--poly")
    parser.add_argument("--hosts", default="local,local")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--remote-cpus")
    parser.add_argument("--batch", type=int, default=65536)
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
