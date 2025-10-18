#ifndef IO_H
#define IO_H

#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <ctype.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

// A cell in a PA. We can change this
typedef int8_t cell_t;

// The permutation array and its data
typedef struct {
  int n;
  int m;
  cell_t* cells;
} pa_t;

void print_pa(const pa_t* pa);
int load_row(const char* line, const int line_limit, cell_t* cell_buffer, const int buffer_limit);
void load_pa(const char* filename, pa_t * pa);

#endif