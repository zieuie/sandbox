# Makefile for multihill project (main.c + io.c)
# Works on Linux (gcc) and macOS (clang)

CC      := cc
CFLAGS  := -std=c99 -O3 -Wall -Wextra -pthread
LDFLAGS := -pthread
OBJ     := main.o io.o
TARGET  := multihill

all: $(TARGET)

$(TARGET): $(OBJ)
	$(CC) $(CFLAGS) $(OBJ) -o $@ $(LDFLAGS)

main.o: main.c io.h
	$(CC) $(CFLAGS) -c main.c

io.o: io.c io.h
	$(CC) $(CFLAGS) -c io.c

clean:
	rm -f $(OBJ) $(TARGET)

.PHONY: all clean
