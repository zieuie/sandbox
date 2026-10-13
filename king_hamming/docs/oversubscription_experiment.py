import os, sys, time, subprocess, numpy as np, signal
sys.path.insert(0, os.path.expanduser("~/zz/sandbox/king_hamming/cluster"))
import gpus
GPU = os.path.expanduser("~/zz/sandbox/king_hamming/gpu_dp_solver/kh_gpu_dp_tile")
p, r = 61, 5
radius = p * p
first_u, last_u, first_v, last_v = 100000, 100000 + 2047, 120000, 120000 + 2047
rows = last_u - (first_u - radius) + 1; cols = last_v - (first_v - radius) + 1
rng = np.random.default_rng(1)
halo = rng.integers(0, 2**40, size=rows * cols, dtype=np.uint64)
halo.tofile("halo.bin")
print("halo", rows, cols, rows * cols * 8 / 1e6, "MB", flush=True)
def run(tag, env_extra, ballast=None):
    env = dict(os.environ, **env_extra)
    b = None
    if ballast:
        b = subprocess.Popen([sys.executable, "ballast.py", str(ballast)], stdout=subprocess.PIPE, text=True)
        print(b.stdout.readline().strip(), flush=True)
    out = f"out-{tag}"
    t = time.time()
    c = subprocess.run([GPU, str(p), str(r), str(first_u), str(last_u), str(first_v), str(last_v), "halo.bin", out, "2", str(2**31)], env=env, capture_output=True, text=True)
    el = time.time() - t
    if b: b.kill(); b.wait()
    print(tag, "rc", c.returncode, f"{el:.1f}s", c.stderr.strip()[:200], flush=True)
    return out
lock = gpus.DeviceLock(0)
assert lock.acquire(600, hold="long")
try:
    a = run("normal", {})
    need = rows * cols * 8 + 2048 * 2048 * 4 + 40 * 1024**2
    print("value+choice bytes", need, flush=True)
    for ratio in (4, 8):
        m1 = run(f"managed-ratio{ratio}", {"KH_GPU_DP_MANAGED": "1"}, ballast=int(130 * 1024**2 + need / ratio))
        same = all(open(f"{a}/{n}","rb").read() == open(f"{m1}/{n}","rb").read() for n in ("values.bin","choices.bin","tile.json"))
        print("ratio", ratio, "identical" if same else "DIFFERENT", flush=True)
finally:
    lock.release()
