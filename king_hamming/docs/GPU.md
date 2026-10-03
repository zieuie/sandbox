# GPUs in the cluster

Two GPU solvers are integrated:

- `match_gpu` ([gpu_match_solver](../gpu_match_solver/README.md)) is an exact
  matching program. It runs on one fenced GPU and replaces 9-node
  `match_distributed` runs of 1–2 hours with seconds of GPU work.
- `kh_gpu_dp_tile` ([gpu_dp_solver](../gpu_dp_solver/README.md)) is a
  byte-identical accelerator for DP tiles. It is 16× (P600) to 143× (RTX 3060)
  faster on production 4096-side tiles.

Both are plain C binaries that load `libcuda.so.1` at run time; see
[cuda/README.md](../cuda/README.md). Hosts without a usable GPU behave exactly as
before.

## Fleet (deployed 2026-10-02 11:13 CDT)

| Host | GPU | Registered | Notes |
| --- | --- | --- | --- |
| merlin `.151` | RTX 3060 Laptop, 6 GiB (5.7 usable) | yes | driver 580.178 |
| `.101`, `.102`, `.104`, `.105` | Quadro P600, 2 GiB (1.7 usable) | yes | running the old 580.126 module loaded before Sep 27; **will lose the GPU at next reboot** (see below) |
| `.103`, `.106`, `.107`, `.108` | Quadro P600 | no | module cannot load under Secure Boot (see below) |
| pellinore `.152` | GTX 1050 Ti Mobile, 4 GiB | no | no NVIDIA driver installed; `nouveau` bound |

Re-probe any host with `cuda/kh_cuda_probe`. Agents run it at registration and
advertise the result, and status shows each node's `gpus_json`. An agent
detects GPUs only when it starts, so relaunch it after fixing a driver.

### Why the P600 workers lose their GPU (Secure Boot + DKMS)

`cluster/ops/gpu_driver_fix.sh` runs the fixes below interactively from merlin
(`mok HOST...`, `pellinore`), and its `check HOST...` mode reports read-only whether each GPU
is usable.

On 2026-09-27 between about 06:36 and 06:46 (the key files' timestamps), an automatic update upgraded `nvidia-driver-580`
from 580.126 to 580.173 through `nvidia-dkms-580`. DKMS rebuilt the module and
signed it with a new per-machine key (`/var/lib/shim-signed/mok/MOK.der`,
"<host> Secure Boot Module Signature key"). Enrolling that key needs a console
confirmation (the blue MOK manager) at the next boot, and that never happened.
`mokutil --list-enrolled` still shows only Canonical's key on every worker.
Because `modprobe` prefers `updates/dkms/nvidia.ko.zst`, a node rebooted since
then cannot load any NVIDIA module under Secure Boot. Nodes not rebooted since
(`.101`, `.102`, `.104`, `.105`) still run the old 580.126 module, which is why
`.104`'s `nvidia-smi` reports an NVML/driver mismatch. They will fail the same
way at their next reboot.

The Canonical-signed module in `linux-modules-nvidia-580-6.17.0-20-generic` is
580.126, which does not match the installed 580.173 userspace. So the fix is
one of these, per worker (needs sudo; I don't have passwordless sudo on the
workers):

1. **Recommended: enroll the existing key, at the console.**
   `sudo mokutil --import /var/lib/shim-signed/mok/MOK.der` (choose a
   one-time password), reboot, choose *Enroll MOK* → *Continue* → enter the
   password. Keeps DKMS; future driver updates just work.
2. **Remote, no console (unverified):** drop DKMS and boot a kernel whose
   Canonical-signed module matches the 580.173 userspace. The signed modules
   for 6.17.0-20 and 6.17.0-22 are both still 580.126, so this works only if
   the 7.0.0-34 build (`linux-modules-nvidia-580-generic-hwe-24.04` candidate)
   carries 580.173. Check with `modinfo -F version` on the installed module
   before removing `nvidia-dkms-580`. Without that match, `cuInit` may fail
   with a driver/library mismatch.
3. **Disable Secure Boot** in each machine's firmware setup (console).

After any of these, relaunch the node's agent (or run `upgrade-workers`) so it
re-registers its GPU.

### Pellinore (no driver)

Pellinore runs Ubuntu 26.04 with Secure Boot off. A dry run of this install is
clean: Canonical-signed modules for its kernel (7.0.0-38), userspace
580.178.04, no DKMS and no X driver, so the display stays on the Intel GPU.

```sh
sudo apt-get install --no-install-recommends linux-modules-nvidia-580-generic nvidia-headless-no-dkms-580 nvidia-utils-580
sudo reboot   # nouveau is bound to the 1050 Ti, so a reboot is needed
```

After the reboot, `nvidia-smi` and `kh_cuda_probe` should list the GTX 1050 Ti
(4 GiB, enough for fields up to ~110 M labels). Then relaunch its agent.

## Scheduling model

- **Registration.** `agent.py` runs `cuda/kh_cuda_probe` at startup (skipped if
  `KH_DISABLE_GPU=1` or `--storage-only`) and sends
  `gpus: [{index, name, arch, total_bytes}]`. The leader validates the list
  (`cluster/gpus.py:normalized`), stores it in `nodes.gpus_json` and keeps it
  across heartbeats. The leader advertises the `gpu-leases-v1` capability.
- **Fencing.** An adapter may return `gpu_memory_bytes` from
  `resource_requirements()` (`resources.ResourceRequest`). For such a run, the
  dispatch transaction picks the smallest device whose `total_bytes − 256 MiB`
  fits, and that no other running lease on the host holds. It records
  `runs.gpu_index` and returns `gpu_index` in the job. The agent passes it to
  the solver as `KH_GPU_DEVICE`. GPU runs must be single-node. They share the
  host's CPUs with DP tiles (`allows_host_sharing`), take their own disjoint
  CPU team (`cpu_width` = requested field-build threads), and reserve host
  memory as usual.
- **Opportunistic DP tiles.** Tiles do not request a GPU, because the GPU
  result is identical. In `dp_solver/distributed_solver.py`, a tile lease takes
  the host-wide lock `/tmp/kh-gpu-<index>.lock` (`gpus.DeviceLock`). It waits up
  to `KH_GPU_DP_WAIT_SECONDS` (default 120 s), honouring stop requests, then
  runs `kh_gpu_dp_tile`. If the lock times out or the GPU kernel fails, it runs
  `kh_dp_tile` on the leased CPUs. Exit 3 (no usable GPU) marks the device
  unavailable for 10 minutes, so broken hosts don't retry every tile. The final
  tile progress record carries `"engine": "gpu"|"cpu"`, which is kept in
  `progress_details`.
- **Coexistence.** `match_gpu`'s bridge takes the same lock before launching
  its kernel (waiting up to 30 minutes), so a matching and a tile never share
  device memory. Tiles hold the lock for seconds; a matching holds it for
  seconds to about half a minute.

`KH_DISABLE_GPU_DP=1` in an agent's environment forces CPU tiles on that host.

## Feeder routing

`campaigns/gpu_policy.py` runs before the existing policy, in both the legacy
and capacity modes. If any healthy compute node advertises a GPU with
`usable ≥ device_bytes(dp)`, and enough host RAM for `host_bytes(dp)`, the
field gets the plan `{"program": "match_gpu", "workers": 1, ...}`. This plan is
not subject to `max_field_elements`. Otherwise the existing CPU plan and limits
apply unchanged.

Pipeline settings (optional; defaults shown):

| Setting | Default | Meaning |
| --- | --- | --- |
| `gpu_matching` | `true` | route GPU-sized fields to `match_gpu` |
| `gpu_matching_threads` | `4` | CPU threads for field construction |

With today's fleet, Merlin's 3060 admits fields up to about 180 M labels,
including the previously blocked 3^17 and 2^27. P600 nodes take fields up to
about 55 M. 17^7, 19^7, 2^29, 7^11 and 11^9 still exceed every GPU and keep
their CPU `field limit`. See
[gpu_match_solver/README.md](../gpu_match_solver/README.md) for the
recommended CPU-side follow-up.

## Web dashboard

- **Fleet:** each card shows the node's GPUs ("not reported" while the leader
  predates GPU support), marked *busy* while a fenced lease or a GPU tile runs.
  Work items carry `GPU n` (fenced lease) or `GPU` (opportunistic tile).
- **Matching:** history has an Engine column (`GPU n` or `CPU · k machines`).
  `match_gpu` runs show the device, the augmenting-phase count and per-stage
  seconds instead of the phase-checkpoint chart they don't have. The waiting
  table explains GPU admission ("GPU on merlin"), or how much GPU memory a field
  needs compared with the largest live GPU.
- **DP tiles:** each root counts the tiles computed or computing on a GPU, and
  the tile detail tags GPU tiles.
- **Feeder:** GPU plans read "GPU on <host>" and count toward the matchable
  backlog.

## Tests

- `cluster/tests/test_gpus.py` (part of `make -C cluster check`) covers record
  validation, device choice, the lock, resource validation, fencing, sharing
  with tiles, heartbeat retention and feeder routing.
- `gpu_match_solver/tests/check_cluster.py --run` runs a real leader with a GPU
  agent and a CPU-only agent. It checks a `match_gpu` certificate, and a
  distributed DP root that mixes GPU and CPU tiles against `kh_dp_local`.
