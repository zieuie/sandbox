# Makefile for multihill project (main.c + io.c)
# Works on Linux (gcc) and macOS (clang)

CC      := cc
CFLAGS  := -std=c99 -O3 -Wall -Wextra -pthread
LDFLAGS := -pthread
OBJ     := main.o bitmap.o chebyshev.o io.o
TARGET  := multihill

ASAN_CFLAGS := -fsanitize=address -g
ASAN_LDFLAGS := -fsanitize=address -static-libasan

CFLAGS := $(CFLAGS) $(ASAN_CFLAGS)
LDFLAGS := $(LDFLAGS) $(ASAN_LDFLAGS)

all: $(TARGET)

$(TARGET): $(OBJ)
	$(CC) $(CFLAGS) $(OBJ) -o $@ $(LDFLAGS)

main.o: main.c bitmap.o chebyshev.o io.o
	$(CC) $(CFLAGS) -c main.c

bitmap.o: bitmap.c bitmap.h
	$(CC) $(CFLAGS) -c bitmap.c

chebyshev.o: chebyshev.c chebyshev.h
	$(CC) $(CFLAGS) -c chebyshev.c

io.o: io.c io.h
	$(CC) $(CFLAGS) -c io.c

clean:
	rm -f $(OBJ) $(TARGET)

.PHONY: all clean
