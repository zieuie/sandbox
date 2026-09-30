CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -Iinclude -I../dp_solver/include

SHARED = ../dp_solver/src/field.c ../dp_solver/src/threads.c ../dp_solver/src/common.c

.PHONY: all check clean
all: kh_match_kernel kh_match_shard.so kh_match_worker kh_match_distributed

kh_match_kernel: src/main.c src/matching.c src/checkpoint.c src/resource.c include/kh_matching.h include/kh_checkpoint.h include/kh_resource.h $(SHARED) ../dp_solver/include/kh_field.h ../dp_solver/include/kh_threads.h ../dp_solver/include/kh_solver.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/main.c src/matching.c src/checkpoint.c src/resource.c $(SHARED) -o $@

kh_match_shard.so: src/shard.c include/kh_shard.h include/kh_matching.h ../dp_solver/include/kh_field.h ../dp_solver/include/kh_threads.h ../dp_solver/include/kh_solver.h $(SHARED)
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared -pthread src/shard.c $(SHARED) -o $@

kh_match_worker: src/distributed_worker.c src/wire.c src/shard.c src/resource.c include/kh_wire.h include/kh_shard.h include/kh_matching.h include/kh_resource.h ../dp_solver/include/kh_field.h ../dp_solver/include/kh_threads.h ../dp_solver/include/kh_solver.h $(SHARED)
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/distributed_worker.c src/wire.c src/shard.c src/resource.c $(SHARED) -o $@

kh_match_distributed: src/distributed.c src/wire.c src/sha256.c src/resource.c include/kh_wire.h include/kh_shard.h include/kh_sha256.h include/kh_resource.h ../dp_solver/include/kh_solver.h ../dp_solver/src/common.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/distributed.c src/wire.c src/sha256.c src/resource.c ../dp_solver/src/common.c -o $@

tests/matching_unit: tests/matching_unit.c src/matching.c include/kh_matching.h ../dp_solver/src/threads.c ../dp_solver/include/kh_threads.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread tests/matching_unit.c src/matching.c ../dp_solver/src/threads.c -o $@

tests/parallel_unit: tests/parallel_unit.c src/matching.c include/kh_matching.h ../dp_solver/src/threads.c ../dp_solver/include/kh_threads.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread tests/parallel_unit.c src/matching.c ../dp_solver/src/threads.c -o $@

tests/wire_unit: tests/wire_unit.c src/wire.c include/kh_wire.h
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/wire_unit.c src/wire.c -o $@

tests/shard_pool_unit: tests/shard_pool_unit.c src/shard.c include/kh_shard.h $(SHARED)
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread tests/shard_pool_unit.c src/shard.c $(SHARED) -o $@

check: all tests/matching_unit tests/parallel_unit tests/wire_unit tests/shard_pool_unit
	./tests/shard_pool_unit
	./tests/wire_unit
	./tests/parallel_unit --run 2>/dev/null
	./tests/matching_unit --run
	./kh_match_kernel >/dev/null
	python3 match.py >/dev/null
	python3 verify_match.py >/dev/null
	python3 tests/check.py --run
	python3 tests/check_checkpoints.py --run
	python3 tests/check_cluster.py --run
	python3 tests/check_group_cluster.py --run
	python3 tests/check_native_distributed.py --run
	python3 tests/check_witch_hunt.py --run

clean:
	rm -f kh_match_kernel kh_match_shard.so kh_match_worker kh_match_distributed tests/matching_unit tests/parallel_unit tests/wire_unit tests/shard_pool_unit
