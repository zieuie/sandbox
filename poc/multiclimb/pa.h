#ifndef IO_H
#define IO_H

// #define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <ctype.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

// A cell in a PA. Make sure this is signed!
typedef int8_t cell_t;

// The permutation array and its data
typedef struct {
  int16_t n;
  int32_t m;
  cell_t* cells;
} pa_t;

inline cell_t pa_get(const pa_t* pa, const int r, const int c) {
  return pa->cells[ pa->n * r + c ];
}

inline void pa_set(const pa_t* pa, const int r, const int c, const cell_t val) {
  pa->cells[ pa->n * r + c ] = val;
}

inline void pa_row_copy_out(const pa_t* pa, cell_t* dst, const int row_idx) {
  memcpy(dst, pa->cells + pa->n * row_idx, sizeof(cell_t) * (unsigned int) pa->n);
}

inline void pa_row_copy_in(const pa_t* pa, cell_t* src, const int row_idx) {
  memcpy(&pa->cells[ pa->n * row_idx ], src, sizeof(cell_t) * (unsigned int) pa->n);
}

void print_pa(const pa_t* pa);
int load_row(const char* line, const int line_limit, cell_t* cell_buffer, const int buffer_limit);
int load_pa(const char* filename, pa_t * pa);
time_t cur_time(char* buffer, ssize_t bufsize);
void dump_pa(const pa_t* pa, const char* filename);
void weave_pa(pa_t* pa, cell_t d);
void random_pa(pa_t* pa, cell_t n, cell_t d);
void free_pa(pa_t *pa);


#endif