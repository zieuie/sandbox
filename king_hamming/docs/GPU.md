# GPUs in the cluster

Three GPU solvers are integrated:

- `match_gpu` ([gpu_match_solver](../gpu_match_solver/README.md)) is an exact
  matching program. It runs on one fenced GPU and replaces 9-node
  `match_distributed` runs of 1–2 hours with seconds of GPU work.
- `match_gpu_blocks` ([gpu_block_match_solver](../gpu_block_match_solver/README.md)) matches
  fields that are too big for any one GPU, block by block on the largest GPU. See
  [GPU_BLOCK_MATCHING.md](GPU_BLOCK_MATCHING.md). It never proves that no perfect
  matching exists, so `match_gpu` stays the first choice for anything that fits.
- `kh_gpu_dp_tile` ([gpu_dp_solver](../gpu_dp_solver/README.md)) is a
  byte-identical accelerator for DP tiles. It is 16× (P600) to 143× (RTX 3060)
  faster on production 4096-side tiles.

Both are plain C binaries that load `libcuda.so.1` at run time; see
[cuda/README.md](../cuda/README.md). A host without a usable GPU behaves exactly as
before.

## Fleet (probed 2026-10-02, evening)

Every machine now has a working GPU. Each one passes `cuda/kh_cuda_probe`, the
check an agent runs at start, and reports driver 580.178.04 with a
Canonical-signed kernel module.

| Host | GPU | Usable memory | Registered with the leader |
| --- | --- | --- | --- |
| merlin `.151` | RTX 3060 Laptop, 6 GiB | 5.7 GiB | yes, since the 11:13 deploy |
| `.101`, `.102`, `.104`, `.105` | Quadro P600, 2 GiB | 1.7 GiB each | yes, since the 11:13 deploy |
| `.103`, `.106`, `.107`, `.108` | Quadro P600, 2 GiB | 1.7 GiB each | **no: probes fine, but the agent must restart** |
| pellinore `.152` | GTX 1050 Ti Max-Q, 4 GiB | about 3.9 GiB | **no: probes fine, but the agent must restart** |

An agent detects GPUs only when it starts, and the agents are stopped now, so the
leader's last record still lists only the first five. All ten register when the
agents are next launched (`upgrade-workers`); check afterwards with
`kh.py status --verbose | grep -i gpu`.

Re-probe any host with `cluster/ops/gpu_driver_fix.sh check HOST...` (read-only: it
copies the probe to `/tmp`, runs it and removes it), or run `cuda/kh_cuda_probe` on the
host. Status shows each node's `gpus_json`.

### History: the driver problems, now resolved

- **P600 workers.** On 2026-09-27 an automatic update moved `nvidia-driver-580` from
  580.126 to 580.173 through DKMS, which signed the new module with a per-machine
  key that Secure Boot never enrolled (enrolling needs a console confirmation).
  `.103` and `.106`–`.108`, rebooted since, could not load any NVIDIA module, and
  `.101`, `.102`, `.104` and `.105` were running an old module that would have
  failed the same way at their next reboot.
- **Pellinore** had no NVIDIA driver, with `nouveau` bound to the 1050 Ti.
- **Resolved on 2026-10-02:** drivers were installed on every machine. All ten
  rebooted afterwards (uptimes of about 2 to 6 hours at probe time), so the fix
  survives a reboot.
- **If it recurs:** `modinfo -F signer nvidia` on the host should say
  "Canonical Ltd. Kernel Module Signing". A per-machine "Module Signature key"
  there means a DKMS build that Secure Boot will refuse after the next reboot.
  `gpu_driver_fix.sh` (modes `mok` and `pellinore`) has the earlier repair
  steps; it is kept for reference.

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
| `gpu_block_matching` | `true` | when no GPU holds the whole field, plan `match_gpu_blocks` on the largest GPU |

With today's fleet, Merlin's 3060 admits fields up to about 180 M labels,
including the previously blocked 3^17 and 2^27. The P600 nodes (now all eight)
take fields up to about 55 M, and pellinore's 4 GiB 1050 Ti should take about
110 M (an estimate from its memory; not yet run).

Fields larger than that get the plan `{"program": "match_gpu_blocks", ...}` (second tier
in `gpu_policy.py`) when `gpu_block_matching` is on and some healthy host has the RAM
(`gpu_block_match_solver/adapter.py:host_bytes`: the full field table plus choices,
about 6 bytes per label). **Every host with a usable GPU and that much RAM may take the
run**, so several fields match at once; the lease asks for the smallest eligible device, and
the kernel sizes its blocks from the device it actually gets (merlin's 3060 needs 3 blocks
for 17^7, a P600 needs 9). On today's fleet all five large fields are admitted:
17^7, 2^29 and 19^7 (about 2.4, 3.1 and 5.1 GiB of host RAM) on all ten machines, 7^11
(11.1 GiB) on all ten, and 11^9 (13.3 GiB) on merlin and the P600 machines. The leader only
places a job where its memory fits beside the DP tiles already reserved on that host, so the
big ones in practice land on merlin.

Results are verified by `matching_solver/kh_verify_khm1`, a native streaming verifier
(about 150x faster than the Python one: 0.2 s against 31 s on 2^23). It is written
independently of the solvers and checked against the Python verifier by a differential test
(`matching_solver/tests/check_native_verify.py`). `artifacts.verify` uses it for full matchings
with at least 2^24 labels when the binary is built, and falls back to Python otherwise.

A block run ends "incomplete" (`block matching incomplete: N requests unmatched ...`) if
the exchange does not finish. The feeder then moves to the next primitive polynomial,
and gives up after `max_matching_attempts` such runs. It is never recorded as an
obstruction. A block run holds the device lock for its whole duration (minutes), so DP
tiles on that host fall back to CPU after their 120 s wait.

## Web dashboard

- **Fleet:** each card shows the node's GPUs ("not reported" while the leader
  predates GPU support), marked *busy* while a fenced lease or a GPU tile runs, and a
  24-hour **GPU use** graph beside the CPU one (real utilisation and memory from
  `nvidia-smi`, sampled by the agent on every heartbeat and kept 7 days in the leader's
  `gpu_usage_samples`; it appears once agents and leader run this version).
  Work items carry `GPU n` (fenced lease) or `GPU` (opportunistic tile).
- **Matching:** history has an Engine column (`GPU n` or `CPU · k machines`).
  Finished GPU runs also get the same burndown chart as CPU runs (requests still
  unmatched, log scale), drawn from a `trace` the kernels report: after greedy and each
  augmenting phase for `match_gpu`, after each block and exchange round for
  `match_gpu_blocks`.
  `match_gpu_blocks` runs also show the block count, exchange rounds and requests left
  after block matching. `match_gpu` runs show the device, the augmenting-phase count and per-stage
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
