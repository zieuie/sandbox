CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Wpedantic -Werror

.PHONY: all check clean

all: kh_rehydrate

kh_rehydrate: kh_rehydrate.c
	$(CC) $(CFLAGS) kh_rehydrate.c -o $@

check: kh_rehydrate
	python3 tests/check.py --run

clean:
	rm -f kh_rehydrate
