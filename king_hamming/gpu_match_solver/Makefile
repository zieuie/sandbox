CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -I../dp_solver/include

# Read-only reuse of the production field builder; nothing outside this folder is modified.
SHARED = ../dp_solver/src/field.c ../dp_solver/src/threads.c ../dp_solver/src/common.c

.PHONY: all venv check clean
all: build/kh_field_export build/kh_cpu_reference

build/kh_field_export: src/field_export.c $(SHARED) ../dp_solver/include/kh_field.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread src/field_export.c $(SHARED) -o $@

build/kh_cpu_reference: src/cpu_reference.c
	mkdir -p build
	$(CC) $(CFLAGS) src/cpu_reference.c -o $@

venv:
	python3 -m venv .venv
	.venv/bin/pip install "cupy-cuda12x[ctk]" numpy

check: all
	.venv/bin/python gpu_match.py ../examples/5_3.khdp --poly 2,3,0,1 -o /tmp/gpu_5_3.khmatch --force
	.venv/bin/python gpu_match.py ../examples/7_5.khdp -o /tmp/gpu_7_5.khmatch --force
	.venv/bin/python gpu_match.py ../matching_solver/examples/13_5.khdp --poly 2,4,0,0,0,1 -o /tmp/gpu_13_5.khmatch --force

clean:
	rm -rf build
