CC ?= gcc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -pedantic

.PHONY: all clean

all: odd_c odd_peek odd_pattern

odd_c: odd.c
	$(CC) $(CFLAGS) odd.c -o odd_c

odd_pattern: odd.c
	$(CC) $(CFLAGS) odd.c -o odd_pattern

odd_peek: odd_peek.c
	$(CC) $(CFLAGS) odd_peek.c -o odd_peek

clean:
	rm -f odd_c odd_peek odd_pattern
