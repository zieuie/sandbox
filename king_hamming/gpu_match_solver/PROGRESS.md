# GPU solvers — overnight progress log (2026-10-02)

Request (Zooey, ~01:20 CDT): build production `gpu_match_solver` and a GPU DP
solver ("dp_match_solver" — interpreted as a GPU DP tile solver, named
`gpu_dp_solver/`; confirm), document design and measurements vs the CPU
solvers, integrate with `cluster/` (and web UI if needed), deploy. A
`send_later` check-in fires at 06:00 CDT.

## Plan

1. `cuda/`: shared driver-API loader (dlopen `libcuda.so.1`) and build-time
   NVRTC embedding (PTX + cubins). Deployed binaries need only the NVIDIA driver;
   no CUDA toolkit or CuPy on nodes. (pip's CUDA 12 nvcc wheel has only ptxas,
   so no nvcc.)
2. `gpu_match_solver/kh_gpu_match_kernel`: native port of the prototype, same CLI
   and payload contract as `matching_solver/kh_match_kernel`.
3. `gpu_dp_solver/kh_gpu_dp_tile`: same CLI and output bytes as
   `dp_solver/kh_dp_tile` (bit-identical values and choices).
4. Cluster: agents advertise GPUs; the scheduler fences GPU leases; new `match_gpu`
   program; DP tiles use the GPU opportunistically through a host GPU lock (results
   identical, so no spec change); feeder routes fields that fit a GPU to
   `match_gpu`; web UI shows GPUs.
5. Benchmarks and docs; then deploy with `launch_dp.py drain` / `upgrade-workers`.

## Status

- [x] Prototype + measurements (README.md), committed `6e913ac` and pushed.
- [x] cuda/ infrastructure (driver-API loader, NVRTC embed, kh_cuda_probe)
- [x] kh_gpu_match_kernel — tests/check.py: fixtures + 60 synthetic incl. 35 Hall obstructions
- [x] kh_gpu_dp_tile — byte-identical to kh_dp_tile (48 random tiles); 65x (13^9) / 144x (23^7) on 4096 tiles
- [x] verified on P600 (.101, .104): identical tiles, verified KHM1
- [x] cluster: gpus.py, leader GPU fence (gpu_index, gpus_json, capability gpu-leases-v1), agent detect + KH_GPU_DEVICE,
      match_gpu adapter, opportunistic GPU tiles in dp_solver/distributed_solver.py, feeder gpu_policy
- [x] tests: cluster/tests/test_gpus.py, gpu_match_solver/tests/check_cluster.py (e2e)
- [x] web UI: GPU line on fleet cards, GPU tags on work items, match_gpu in matching view
- [x] full suites green: cluster (incl. test_gpus, test_rollout), dp_solver, matching_solver, web,
      gpu_match_solver, gpu_dp_solver; e2e tests/check_cluster.py
- [x] docs: gpu_match_solver/README.md, gpu_dp_solver/README.md, cuda/README.md, docs/GPU.md;
      links in README.md and docs/CONTINUOUS_CAMPAIGN.md
- [x] deploy — 2026-10-02 11:13 CDT with Zooey's go-ahead (drain 10:5x, upgrade-workers 11:13,
      resume). GPUs registered on merlin and .101/.102/.104/.105. First live results: 3^17 matched
      (455 s incl. verification) and archived; 2^27 matched. Median tile time in the first hour:
      13^9 30 s on GPU vs 115 s on CPU; 23^7 49 s vs 632 s.
- [ ] drivers on .103/.106-.108 (Secure Boot + unenrolled DKMS key) and pellinore (no driver):
      diagnosed in docs/GPU.md; package install was blocked by the permission check. Commands
      for Zooey are in docs/GPU.md.

## Deploy runbook (run from the repo root on merlin)

```sh
# 1. Stop granting leases; running tiles finish normally (23^7 tiles take up to ~15 min).
python3 king_hamming/dp_solver/launch_dp.py --state king_hamming/cluster/deployments/continuous-campaign drain
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8061 status   # wait: no running runs
# 2. Build (incl. GPU binaries), restart leader, replace all worker runtimes, restart feeder.
python3 king_hamming/dp_solver/launch_dp.py --state king_hamming/cluster/deployments/continuous-campaign upgrade-workers
# 3. Check GPUs registered (gpus_json on dp-101..105 and dp-151), then resume dispatch.
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8061 status --verbose | grep -i gpu
python3 king_hamming/cluster/kh.py --leader http://192.168.4.151:8061 resume --all
# 4. Restart the dashboard (currently a foreground process in a terminal tab) to load snapshot.py.
```

Expected after resume: the feeder plans `match_gpu` for 3^17 and 2^27 (Merlin's 3060) and
queues them at priority 100; DP tiles on .101-.105 and Merlin report `"engine":"gpu"` in
progress_details. Rollback: check out the previous commit and rerun drain + upgrade-workers;
GPU columns are additive and ignored by older code.

Fleet GPU probe (01:40): usable P600 on .101-.105, RTX 3060 on merlin. .106-.108: nvidia
kernel module not loaded; pellinore: no NVIDIA driver. Needs sudo/reboot — left for Zooey.
