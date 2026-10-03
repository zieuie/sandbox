.PHONY: check clean

check:
	$(MAKE) -C ../dp_solver all
	$(MAKE) -C ../matching_solver all
	$(MAKE) -C ../matching_solver_multi all
	$(MAKE) -C ../cuda all
	$(MAKE) -C ../gpu_match_solver kh_gpu_match_kernel
	$(MAKE) -C ../gpu_dp_solver all
	python3 -m py_compile *.py tests/*.py tests/fixtures/*.py ../dp_solver/*.py ../adapter_config.py
	@for program in leader.py agent.py kh.py verify_artifact.py affinity_exec.py cluster_smoke.py tile_driver.py distributed_solver.py queue_tiles_smoke.py launch_dp.py; do \
		./$$program >/dev/null; \
	done
	python3 tests/test_adapters.py --run
	python3 tests/test_supervision.py --run
	python3 tests/test_integration.py
	python3 tests/test_recovery.py --run
	python3 tests/test_retention.py --run
	python3 tests/test_scheduling.py --run
	python3 tests/test_continuous_campaign.py --run
	python3 tests/test_capacity_campaign.py
	python3 tests/test_rollout.py --run
	python3 tests/test_status.py --run
	python3 tests/test_canary.py
	python3 tests/test_resources.py
	python3 tests/test_gpus.py
	python3 tests/test_tiles.py --run
	python3 tests/test_tile_driver.py --run
	python3 tests/test_distributed.py --run
	python3 tests/test_tile_reuse.py

clean:
	rm -rf __pycache__ tests/__pycache__ state
