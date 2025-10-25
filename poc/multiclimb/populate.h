#pragma once
#include "lib.h"
#include "pa.h"

size_t sym_idx(size_t u, size_t v);

void* worker_body(const pa_t* pa, const int d, bitlut_t *foes, bitlut_t *problems, const uint64_t start_v, const uint64_t end_v, const int thread_idx);

void parallel_populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, const size_t lut_size, const size_t K);

void populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems);
