# Makefile for megahaxell
# Works on Linux (gcc) and macOS (clang)

CC ?= cc

BUILD_DIR := build
OBJ_DIR := $(BUILD_DIR)/obj
BIN := $(BUILD_DIR)/megahaxell
TEST_BINS := $(BUILD_DIR)/test_smoke $(BUILD_DIR)/test_colors $(BUILD_DIR)/test_haxell_one

WARN := -Wall -Wextra -Wpedantic
STD := -std=c11
OPT ?= -O2
DBG ?= -g

CPPFLAGS := -Iinclude
CFLAGS := $(STD) $(OPT) $(DBG) $(WARN) -Werror
LDFLAGS :=

LDLIBS := -lzmq

# Set to 1 for ASAN builds: `make ASAN=1`
ASAN ?= 0
ifeq ($(ASAN),1)
  CFLAGS += -fsanitize=address -fno-omit-frame-pointer
  LDFLAGS += -fsanitize=address
endif

SRC_LIB := \
  src/client.c \
  src/core.c \
  src/head_coord.c \
  src/math/colors.c \
  src/math/haxell.c \
  src/megahaxell.c \
  src/pool.c \
  src/serial.c \
  src/server.c
SRC_APP := src/main.c src/head.c src/worker.c
SRC_TEST := tests/test_smoke.c tests/test_colors.c tests/test_haxell_one.c

OBJ_LIB := $(patsubst src/%.c,$(OBJ_DIR)/src/%.o,$(SRC_LIB))
OBJ_APP := $(patsubst src/%.c,$(OBJ_DIR)/src/%.o,$(SRC_APP))
OBJ_TEST := $(patsubst tests/%.c,$(OBJ_DIR)/tests/%.o,$(SRC_TEST))

all: $(BIN)

$(BIN): $(OBJ_LIB) $(OBJ_APP)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS) $(LDLIBS)

$(BUILD_DIR)/test_smoke: $(OBJ_LIB) $(OBJ_DIR)/tests/test_smoke.o
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/test_colors: $(OBJ_LIB) $(OBJ_DIR)/tests/test_colors.o
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/test_haxell_one: $(OBJ_LIB) $(OBJ_DIR)/tests/test_haxell_one.o
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(OBJ_DIR)/src/%.o: src/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(OBJ_DIR)/tests/%.o: tests/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

test: $(TEST_BINS)
	$(BUILD_DIR)/test_smoke
	$(BUILD_DIR)/test_colors
	$(BUILD_DIR)/test_haxell_one

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all test clean
