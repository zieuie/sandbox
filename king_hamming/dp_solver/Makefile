CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
THREAD_FLAGS = -pthread
CPPFLAGS ?= -Iinclude

PROGRAMS = kh_estimate kh_dp_local kh_dp_tile kh_field
COMMON_OBJECTS = build/common.o build/transitions.o

.PHONY: all check clean

all: $(PROGRAMS)

kh_estimate: build/estimate.o $(COMMON_OBJECTS)
	$(CC) $(CFLAGS) $^ -o $@

kh_dp_local: build/dp_local.o build/threads.o build/progress.o $(COMMON_OBJECTS)
	$(CC) $(CFLAGS) $(THREAD_FLAGS) $^ -o $@

kh_dp_tile: build/dp_tile.o build/threads.o build/progress.o $(COMMON_OBJECTS)
	$(CC) $(CFLAGS) $(THREAD_FLAGS) $^ -o $@

kh_field: build/field_build.o build/field.o build/threads.o $(COMMON_OBJECTS)
	$(CC) $(CFLAGS) $(THREAD_FLAGS) $^ -o $@

build/%.o: src/%.c include/kh_solver.h include/kh_threads.h include/kh_progress.h include/kh_field.h
	@mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(THREAD_FLAGS) -c $< -o $@

build/progress_unit: tests/progress_unit.c build/threads.o build/progress.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(THREAD_FLAGS) $^ -o $@

build/transitions_unit: tests/transitions_unit.c $(COMMON_OBJECTS)
	$(CC) $(CPPFLAGS) $(CFLAGS) $^ -o $@

check: all build/progress_unit build/transitions_unit
	@for program in $(PROGRAMS); do \
		./$$program >/dev/null; \
	done
	./verify_dp.py >/dev/null
	./benchmark.py >/dev/null
	./print_dp.py >/dev/null
	python3 -m py_compile *.py tests/*.py
	python3 tests/check.py
	python3 tests/check_tiles.py --run
	python3 tests/check_artifacts.py --run
	python3 tests/check_field.py --run
	@for program in launch_dp.py tile_driver.py distributed_solver.py cluster_smoke.py queue_tiles_smoke.py; do \
		./$$program >/dev/null; \
	done
	python3 ../cluster/tests/test_scheduling.py --run
	python3 ../cluster/tests/test_tiles.py --run
	python3 ../cluster/tests/test_tile_driver.py --run

clean:
	rm -rf build $(PROGRAMS)
