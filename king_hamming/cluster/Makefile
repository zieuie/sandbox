.PHONY: check clean

check:
	$(MAKE) -C ../dp_solver all
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
	python3 tests/test_tiles.py --run
	python3 tests/test_tile_driver.py --run
	python3 tests/test_distributed.py --run

clean:
	rm -rf __pycache__ tests/__pycache__ state
