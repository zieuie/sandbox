CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
SHARED = ../dp_solver/src/field.c ../dp_solver/src/common.c ../dp_solver/src/threads.c

.PHONY: all check clean baseline
all: kh_match_multi kh_check_images

kh_check_images: check_images.c
	$(CC) $(CFLAGS) check_images.c -o $@

kh_match_multi: main.c $(SHARED)
	$(CC) $(CFLAGS) -I../dp_solver/include -pthread main.c $(SHARED) -o $@

check: all
	python3 tests/check.py
	python3 tests/test_runtime.py
	python3 tests/test_cluster.py

# Separate measured copies: never overwrite the running solver's executables.
baseline: benchbin/kh_match_worker benchbin/kh_match_distributed

benchbin/kh_match_worker: io_meter.c ../matching_solver/src/distributed_worker.c ../matching_solver/src/shard.c ../matching_solver/src/wire.c ../matching_solver/src/resource.c $(SHARED)
	mkdir -p benchbin
	$(CC) $(CFLAGS) -I../matching_solver/include -I../dp_solver/include -pthread $^ -Wl,--wrap=read,--wrap=write -o $@

benchbin/kh_match_distributed: io_meter.c ../matching_solver/src/distributed.c ../matching_solver/src/wire.c ../matching_solver/src/sha256.c ../matching_solver/src/resource.c ../dp_solver/src/common.c
	mkdir -p benchbin
	$(CC) $(CFLAGS) -I../matching_solver/include -I../dp_solver/include -pthread $^ -Wl,--wrap=read,--wrap=write -o $@

clean:
	rm -f kh_match_multi kh_check_images benchbin/kh_match_worker benchbin/kh_match_distributed
