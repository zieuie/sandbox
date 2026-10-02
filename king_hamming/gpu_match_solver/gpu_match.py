#!/usr/bin/env python3
"""Experimental exact GPU matcher for one KHD1 field, emitting an independently verified KHM1."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np
import cupy as cp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from matching_solver.artifacts import header, load_dp, publish, request_count, verify  # noqa: E402

FREE = 0xFFFFFFFF

# Warp-per-vertex kernels: one warp reads 32 consecutive cell labels (coalesced) and
# gathers 32 random right[] entries; latency hiding across warps is the whole point.
SOURCE = r"""
typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned short u16;
#define FREE 0xFFFFFFFFu
#define RESERVED 0xFFFFFFFEu
#define FULL 0xFFFFFFFFu

struct graph_t {
    const u32 *cells;
    const u64 *bfirst;
    const u32 *bcoset;
    const u32 *bwidth;
    u32 nblocks;
    u32 f;
    u32 qm1;
};

__device__ __forceinline__ void decode(const graph_t &g, u32 u, u32 *coset, u32 *cell) {
    u32 lo = 0, hi = g.nblocks;
    while (lo + 1 < hi) {
        u32 m = (lo + hi) >> 1;
        if (g.bfirst[m] <= u) lo = m; else hi = m;
    }
    u64 off = (u64)u - g.bfirst[lo];
    u32 w = g.bwidth[lo];
    *coset = g.bcoset[lo] + (u32)(off / w);
    *cell = (u32)(off % w);
}

// Same map as kh_neighbor(): zero fixed, nonzero labels multiplied by X^-coset.
__device__ __forceinline__ u32 shift(u32 label, u32 coset, u32 qm1) {
    if (label == 0) return 0;
    u32 x = label - 1;
    x = x >= coset ? x - coset : x + (qm1 - coset);
    return x + 1;
}

__device__ __forceinline__ u32 mix(u32 x) {
    x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16;
    return x;
}

extern "C" __global__ void greedy(graph_t g, u32 n, u32 *left, u16 *choice, u32 *right,
                                  u32 *matched, u64 *scans, u32 salt) {
    u32 warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    u32 lane = threadIdx.x & 31;
    if (warp >= n) return;
    u32 u = warp;
    if (left[u] != FREE) return;
    u32 coset, cell;
    decode(g, u, &coset, &cell);
    const u32 *row = g.cells + (u64)cell * g.f;
    u32 start = salt ? mix(u ^ salt) % g.f : 0;
    u32 scanned = 0;
    for (u32 base = 0; base < g.f; base += 32) {
        u32 k = base + lane;
        u32 kk = k + start;
        if (kk >= g.f) kk -= g.f;
        u32 v = 0;
        bool free = false;
        if (k < g.f) {
            v = shift(row[kk], coset, g.qm1);
            free = right[v] == FREE;
        }
        scanned += min(32u, g.f - base);
        unsigned m = __ballot_sync(FULL, free);
        while (m) {
            int l = __ffs(m) - 1;
            u32 vv = __shfl_sync(FULL, v, l);
            u32 kl = __shfl_sync(FULL, kk, l);
            u32 got = 0;
            if (lane == 0) got = atomicCAS(&right[vv], FREE, u);
            got = __shfl_sync(FULL, got, 0);
            if (got == FREE) {
                if (lane == 0) {
                    left[u] = vv;
                    choice[u] = (u16)kl;
                    atomicAdd(matched, 1u);
                    atomicAdd(scans, (u64)scanned);
                }
                return;
            }
            m &= m - 1;
        }
    }
    if (lane == 0) atomicAdd(scans, (u64)scanned);
}

// One BFS level of the APFB scheme: every tree carries its root's index; a tree stops once
// its root has claimed a free right endpoint, which is reserved until augmentation.
extern "C" __global__ void expand(graph_t g, u32 nfront, const u32 *front, u32 *next, u32 *ntail,
                                  const u32 *left, u32 *right, u32 *root, u32 *parent, u16 *viak,
                                  u32 *rootdone, u32 *end_u, u32 *end_v, u16 *end_k,
                                  u32 *found, u64 *scans) {
    u32 warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    u32 lane = threadIdx.x & 31;
    if (warp >= nfront) return;
    u32 u = front[warp];
    u32 r = root[u];
    volatile u32 *done = rootdone;
    if (done[r]) return;
    u32 coset, cell;
    decode(g, u, &coset, &cell);
    const u32 *row = g.cells + (u64)cell * g.f;
    u32 scanned = 0;
    unsigned lower = (1u << lane) - 1;
    for (u32 base = 0; base < g.f; base += 32) {
        u32 stop = 0;
        if (lane == 0) stop = done[r];
        if (__shfl_sync(FULL, stop, 0)) break;
        u32 k = base + lane;
        u32 v = 0, w = RESERVED;
        if (k < g.f) {
            v = shift(row[k], coset, g.qm1);
            w = right[v];
        }
        scanned += min(32u, g.f - base);
        unsigned m = __ballot_sync(FULL, w == FREE);
        while (m) {
            int l = __ffs(m) - 1;
            u32 vv = __shfl_sync(FULL, v, l);
            u32 status = 0;
            if (lane == 0) {
                if (atomicCAS(&rootdone[r], 0u, 1u) == 0u) {
                    if (atomicCAS(&right[vv], FREE, RESERVED) == FREE) {
                        end_u[r] = u;
                        end_v[r] = vv;
                        end_k[r] = (u16)(base + l);
                        atomicAdd(found, 1u);
                        status = 1;
                    } else {
                        atomicExch(&rootdone[r], 0u);
                    }
                } else {
                    status = 2;
                }
            }
            status = __shfl_sync(FULL, status, 0);
            if (status) {
                if (lane == 0) atomicAdd(scans, (u64)scanned);
                return;
            }
            m &= m - 1;
        }
        bool disc = false;
        if (w != FREE && w != RESERVED && root[w] == FREE && atomicCAS(&root[w], FREE, r) == FREE) {
            parent[w] = u;
            viak[w] = (u16)k;
            disc = true;
        }
        unsigned d = __ballot_sync(FULL, disc);
        if (d) {
            u32 at = 0;
            if (lane == 0) at = atomicAdd(ntail, (u32)__popc(d));
            at = __shfl_sync(FULL, at, 0);
            if (disc) next[at + __popc(d & lower)] = w;
        }
    }
    if (lane == 0) atomicAdd(scans, (u64)scanned);
}

// Vertex-disjoint trees make every claimed path independent; flip each in one thread.
extern "C" __global__ void augment(u32 nroots, const u32 *roots, u32 *left, u16 *choice, u32 *right,
                                   const u32 *parent, const u16 *viak, const u32 *rootdone,
                                   const u32 *end_u, const u32 *end_v, const u16 *end_k,
                                   u32 *matched, u32 *longest) {
    u32 i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= nroots) return;
    u32 r = roots[i];
    if (!rootdone[i]) return;
    u32 cur = end_u[i], v = end_v[i];
    u16 k = end_k[i];
    u32 length = 1;
    for (;;) {
        u32 old = left[cur];
        left[cur] = v;
        choice[cur] = k;
        right[v] = cur;
        if (cur == r) break;
        k = viak[cur];
        v = old;
        cur = parent[cur];
        ++length;
    }
    atomicAdd(matched, 1u);
    atomicMax(longest, length);
}

// Independent on-device consistency check of the final assignment.
extern "C" __global__ void check(graph_t g, u32 n, const u32 *left, const u16 *choice,
                                 const u32 *right, u32 *bad) {
    u32 u = blockIdx.x * blockDim.x + threadIdx.x;
    if (u >= n) return;
    u32 v = left[u];
    if (v == FREE) return;
    u32 coset, cell;
    decode(g, u, &coset, &cell);
    u32 k = choice[u];
    if (k >= g.f || shift(g.cells[(u64)cell * g.f + k], coset, g.qm1) != v || right[v] != u)
        atomicAdd(bad, 1u);
}
"""

MODULE = cp.RawModule(code=SOURCE, options=("-std=c++14",))
GREEDY = MODULE.get_function("greedy")
EXPAND = MODULE.get_function("expand")
AUGMENT = MODULE.get_function("augment")
CHECK = MODULE.get_function("check")

# Must mirror the device graph_t layout (pointer fields 8-byte aligned).
GRAPH_T = np.dtype([("cells", np.uint64), ("bfirst", np.uint64), ("bcoset", np.uint64),
                    ("bwidth", np.uint64), ("nblocks", np.uint32), ("f", np.uint32),
                    ("qm1", np.uint32), ("pad", np.uint32)])


def load_field(path):
    """Return (p, r, q, f, cells) from a KHGF dump written by kh_field_export."""
    with open(path, "rb") as stream:
        if stream.read(4) != b"KHGF":
            raise ValueError("not a KHGF field dump")
        p, r, qlo, qhi, f, _ = np.frombuffer(stream.read(24), np.uint32)
        q = int(qlo) | (int(qhi) << 32)
        cells = np.fromfile(stream, np.uint32, q)
    if cells.size != q:
        raise ValueError("truncated field dump")
    return int(p), int(r), q, int(f), cells


def pack(values, bits, stream):
    """Write values as little-bit-first packed fields, exactly like packed_write() in main.c."""
    shifts = np.arange(bits, dtype=np.uint32)
    chunk = 1 << 20  # multiple of 8 values keeps every chunk byte aligned
    for start in range(0, values.size, chunk):
        part = values[start:start + chunk].astype(np.uint32)
        bitmap = ((part[:, None] >> shifts) & 1).astype(np.uint8).ravel()
        stream.write(np.packbits(bitmap, bitorder="little").tobytes())


class Solver:
    """Owns every device array for one field attempt."""

    def __init__(self, dp, cells, f, q, salt):
        runs = [(run["a"], run["t"] * run["repeat"]) for run in dp["runs"]]
        bfirst, bcoset, bwidth = [], [], []
        stripes = cosets = 0
        for a, copies in runs:
            bfirst.append(stripes * f)
            bcoset.append(cosets)
            bwidth.append(a * f)
            stripes += a * copies
            cosets += copies
        self.n = n = stripes * f
        self.f, self.q, self.salt = f, q, salt
        if f > 65535 or q > 2**32:
            raise ValueError("prototype needs f <= 65535 and q <= 2^32")
        self.cells = cp.asarray(cells)
        self.bfirst = cp.asarray(np.array(bfirst, np.uint64))
        self.bcoset = cp.asarray(np.array(bcoset, np.uint32))
        self.bwidth = cp.asarray(np.array(bwidth, np.uint32))
        g = np.zeros((), GRAPH_T)
        g["cells"], g["bfirst"] = self.cells.data.ptr, self.bfirst.data.ptr
        g["bcoset"], g["bwidth"] = self.bcoset.data.ptr, self.bwidth.data.ptr
        g["nblocks"], g["f"], g["qm1"] = len(runs), f, q - 1
        self.graph = g
        self.left = cp.full(n, FREE, cp.uint32)
        self.choice = cp.zeros(n, cp.uint16)
        self.right = cp.full(q, FREE, cp.uint32)
        self.root = cp.empty(n, cp.uint32)
        self.parent = cp.empty(n, cp.uint32)
        self.viak = cp.empty(n, cp.uint16)
        self.queue_a = cp.empty(n, cp.uint32)
        self.queue_b = cp.empty(n, cp.uint32)
        self.counters = cp.zeros(4, cp.uint32)  # matched, found, ntail, longest
        self.scans = cp.zeros(1, cp.uint64)
        self.matched = 0
        self.phases = []

    @staticmethod
    def device_bytes(n, q):
        """Upper bound on the device arrays allocated above."""
        # cells+right per field label; left, choice, root, parent, viak, two queues per request.
        # Per-root claim state is sized by the free roots of a phase (small after greedy).
        return 8 * q + n * (4 + 2 + 4 + 4 + 2 + 4 + 4) + (1 << 26)

    def warps(self, count):
        return ((count * 32 + 255) // 256,), (256,)

    def run_greedy(self):
        GREEDY(*self.warps(self.n), (self.graph, np.uint32(self.n), self.left, self.choice, self.right,
               self.counters, self.scans, np.uint32(self.salt)))
        self.matched = int(self.counters[0])

    def run_phase(self):
        """One APFB phase; returns (augmented, levels, longest path, visited lefts)."""
        roots = cp.flatnonzero(self.left == FREE).astype(cp.uint32)
        nroots = roots.size
        if nroots == 0:
            return 0, 0, 0, 0
        self.root.fill(FREE)
        self.root[roots] = cp.arange(nroots, dtype=cp.uint32)
        self.rootdone = cp.zeros(nroots, cp.uint32)
        self.end_u = cp.empty(nroots, cp.uint32)
        self.end_v = cp.empty(nroots, cp.uint32)
        self.end_k = cp.empty(nroots, cp.uint16)
        self.counters[1:] = 0
        front, spare = self.queue_a, self.queue_b
        front[:nroots] = roots
        count, levels, visited = nroots, 0, nroots
        while count:
            self.counters[2] = 0
            EXPAND(*self.warps(count), (self.graph, np.uint32(count), front, spare, self.counters[2:3],
                   self.left, self.right, self.root, self.parent, self.viak, self.rootdone,
                   self.end_u, self.end_v, self.end_k, self.counters[1:2], self.scans))
            count = int(self.counters[2])
            visited += count
            front, spare = spare, front
            levels += 1
        found = int(self.counters[1])
        if found:
            AUGMENT(((nroots + 255) // 256,), (256,), (np.uint32(nroots), roots, self.left, self.choice,
                    self.right, self.parent, self.viak, self.rootdone, self.end_u, self.end_v,
                    self.end_k, self.counters[0:1], self.counters[3:4]))
        before = self.matched
        self.matched = int(self.counters[0])
        if self.matched - before != found:
            raise RuntimeError("augmentation count disagrees with claimed paths")
        return found, levels, int(self.counters[3]), visited

    def solve(self, log):
        start = time.perf_counter()
        self.run_greedy()
        cp.cuda.Device().synchronize()
        log(f"greedy matched={self.matched}/{self.n} seconds={time.perf_counter() - start:.3f}")
        while self.matched < self.n:
            t = time.perf_counter()
            found, levels, longest, visited = self.run_phase()
            seconds = time.perf_counter() - t
            self.phases.append(dict(found=found, levels=levels, longest=longest, visited=visited,
                                    seconds=round(seconds, 4)))
            log(f"phase={len(self.phases)} augmented={found} matched={self.matched}/{self.n} "
                f"levels={levels} longest={longest} visited={visited} seconds={seconds:.3f}")
            if found == 0:
                break
        return time.perf_counter() - start

    def check(self):
        bad = cp.zeros(1, cp.uint32)
        CHECK(((self.n + 255) // 256,), (256,), (self.graph, np.uint32(self.n), self.left, self.choice,
              self.right, bad))
        matched_right = int(cp.count_nonzero(self.right != FREE))
        if int(bad[0]) or matched_right != self.matched or int(cp.count_nonzero(self.left != FREE)) != self.matched:
            raise RuntimeError("device self-check failed")

    def hall_bits(self):
        """After an unproductive final phase, every visited left is in the Hall set."""
        return (self.root != FREE).astype(cp.uint8).get()


def main():
    parser = argparse.ArgumentParser(description="Experimental exact GPU matching for one KHD1 field.",
        epilog="Example: .venv/bin/python gpu_match.py ../examples/5_3.khdp --poly 2,3,0,1 -o /tmp/5_3.khmatch")
    parser.add_argument("dp", type=Path, nargs="?")
    parser.add_argument("-o", "--output", type=Path, help="write and independently verify a KHM1 certificate")
    parser.add_argument("--poly", default="auto", help="pinned polynomial (low degree first) or 'auto'")
    parser.add_argument("--field-threads", type=int, default=4, help="CPU threads for the field builder")
    parser.add_argument("--salt", type=int, default=0x9E3779B9, help="greedy start-offset hash salt; 0 disables")
    parser.add_argument("--no-verify", action="store_true", help="skip the (CPU) independent KHM1 verifier")
    parser.add_argument("--force", action="store_true", help="replace an existing output")
    parser.add_argument("--json", type=Path, help="append a one-line JSON summary here")
    parser.add_argument("--keep-field", type=Path, help="reuse/keep the KHGF field dump at this path")
    arguments = parser.parse_args()
    if arguments.dp is None:
        parser.print_help()
        return 0
    log = lambda message: print(message, file=sys.stderr, flush=True)  # noqa: E731
    dp, dp_digest = load_dp(arguments.dp)
    n = request_count(dp)
    summary = dict(field=f"{dp['p']}^{dp['r']}", q=dp["q"], f=dp["f"], requests=n,
                   gpu=cp.cuda.runtime.getDeviceProperties(0)["name"].decode())
    need = Solver.device_bytes(n, dp["q"])
    free_bytes, _ = cp.cuda.runtime.memGetInfo()
    if need > free_bytes:
        raise MemoryError(f"needs ~{need / 2**30:.2f} GiB device memory; {free_bytes / 2**30:.2f} GiB free")

    with tempfile.TemporaryDirectory(prefix=".gpu-match-") as temporary:
        t = time.perf_counter()
        field_path = arguments.keep_field or Path(temporary) / "field.khgf"
        meta_path = Path(str(field_path) + ".json")
        if arguments.keep_field and field_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            completed = subprocess.run([str(HERE / "build" / "kh_field_export"), str(dp["p"]), str(dp["r"]),
                                        arguments.poly, str(field_path), str(arguments.field_threads)],
                                       stdout=subprocess.PIPE, text=True, check=True)
            meta = json.loads(completed.stdout)
            meta_path.write_text(json.dumps(meta))
        p, r, q, f, cells = load_field(field_path)
        if (p, r, q, f) != (dp["p"], dp["r"], dp["q"], dp["f"]):
            raise ValueError("field dump disagrees with DP")
        summary.update(polynomial=meta["polynomial"], field_seconds=round(time.perf_counter() - t, 3))
        log(f"field {summary['field']} q={q} f={f} requests={n} poly={meta['polynomial']} "
            f"built/loaded in {summary['field_seconds']}s")

        t = time.perf_counter()
        solver = Solver(dp, cells, f, q, arguments.salt)
        del cells
        cp.cuda.Device().synchronize()
        summary["upload_seconds"] = round(time.perf_counter() - t, 3)
        summary["solve_seconds"] = round(solver.solve(log), 3)
        solver.check()
        summary.update(matched=solver.matched, phases=len(solver.phases),
                       scans=int(solver.scans[0]), phase_log=solver.phases,
                       peak_device_bytes=cp.get_default_memory_pool().total_bytes())
        log(f"solve {summary['solve_seconds']}s matched={solver.matched}/{n} phases={len(solver.phases)} "
            f"scans={summary['scans']}")

        if arguments.output:
            if arguments.output.exists() and arguments.force:
                arguments.output.unlink()
            obstructed = solver.matched < n
            maximum = f - 1 + obstructed
            bits = maximum.bit_length()
            choice = solver.choice.get()
            matched_mask = (solver.left != FREE).get()
            payload = Path(temporary) / "payload.bin"
            with payload.open("wb") as stream:
                values = np.where(matched_mask, choice.astype(np.uint32) + obstructed, 0)
                pack(values, bits, stream)
                if obstructed:
                    pack(solver.hall_bits(), 1, stream)
            metadata = dict(p=p, r=r, polynomial=meta["polynomial"], status=int(obstructed),
                            required=n, matched=solver.matched)
            publish(arguments.output, header(dp, dp_digest, metadata), payload)
            if not arguments.no_verify:
                t = time.perf_counter()
                verified = verify(arguments.output, dp, dp_digest, max_bytes=2**36)
                summary.update(verified=verified, verify_seconds=round(time.perf_counter() - t, 3))
                log(f"independent KHM1 verification passed in {summary['verify_seconds']}s: {verified}")
    if arguments.json:
        with arguments.json.open("a") as stream:
            stream.write(json.dumps(summary) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "phase_log"}))
    return 0 if summary["matched"] == n else 2


if __name__ == "__main__":
    sys.exit(main())
