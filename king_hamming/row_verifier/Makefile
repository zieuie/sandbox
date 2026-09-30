CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -I../matching_solver/include

.PHONY: all check clean

all: kh_verify_rows

kh_verify_rows: verify_rows.c ../matching_solver/src/sha256.c ../matching_solver/include/kh_sha256.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread verify_rows.c ../matching_solver/src/sha256.c -o $@

check: kh_verify_rows
	python3 tests/check.py --run

clean:
	rm -f kh_verify_rows
