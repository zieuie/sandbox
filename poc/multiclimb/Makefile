# Makefile for multihill project
# Works on Linux (gcc) and macOS (clang)

CC      := cc
CFLAGS  := -std=c99 -O3 -Wall -Wextra -pthread -Werror -Wpedantic
LDFLAGS := -pthread
OBJ     := main.o lib.o chebyshev.o pa.o
TARGET  := multihill

ASAN_CFLAGS := -fsanitize=address -g
ASAN_LDFLAGS := -fsanitize=address -static-libasan

CFLAGS := $(CFLAGS) $(ASAN_CFLAGS)
LDFLAGS := $(LDFLAGS) $(ASAN_LDFLAGS)

all: $(TARGET)

$(TARGET): $(OBJ)
	$(CC) $(CFLAGS) $(OBJ) -o $@ $(LDFLAGS)

main.o: main.c lib.o chebyshev.o pa.o
	$(CC) $(CFLAGS) -c main.c

lib.o: lib.c lib.h
	$(CC) $(CFLAGS) -c lib.c

chebyshev.o: chebyshev.c chebyshev.h
	$(CC) $(CFLAGS) -c chebyshev.c

pa.o: pa.c pa.h
	$(CC) $(CFLAGS) -c pa.c

clean:
	rm -f $(OBJ) $(TARGET)

.PHONY: all clean
