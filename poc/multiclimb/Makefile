# Makefile for multihill project
# Works on Linux (gcc) and macOS (clang)

CC      := cc
CFLAGS  := -std=c11 -O3 -Wall -Wextra -pthread -Werror -Wpedantic -D_GNU_SOURCE -I/usr/local/lib -march=native -g
LDFLAGS := -pthread -L/usr/local/lib -lroaring
OBJ     := main.o lib.o chebyshev.o pa.o populate.o
TARGET  := multihill

ASAN_CFLAGS := -fsanitize=address -g
ASAN_LDFLAGS := -fsanitize=address -static-libasan

# CFLAGS := $(CFLAGS) $(ASAN_CFLAGS)
# LDFLAGS := $(LDFLAGS) $(ASAN_LDFLAGS)

all: $(TARGET)

$(TARGET): $(OBJ)
	$(CC) $(CFLAGS) $(OBJ) -o $@ $(LDFLAGS)

main.o: main.c lib.o chebyshev.o pa.o
	$(CC) $(CFLAGS) -c main.c

lib.o: lib.c lib.h
	$(CC) $(CFLAGS) -c lib.c

chebyshev.o: chebyshev.c chebyshev.h populate.o
	$(CC) $(CFLAGS) -c chebyshev.c

pa.o: pa.c pa.h
	$(CC) $(CFLAGS) -c pa.c

populate.o: populate.c populate.h
	$(CC) $(CFLAGS) -c populate.c

clean:
	rm -f $(OBJ) $(TARGET)

.PHONY: all clean
