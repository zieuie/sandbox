#pragma once
#include "lib.h"
#include "pa.h"

ssize_t sym_idx(ssize_t u, ssize_t v);

void* worker_body(const pa_t* pa, const int d, bitlut_t *foes, bitlut_t *problems, const int64_t start_v, const int64_t end_v, const int thread_idx);

void parallel_populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, const ssize_t lut_size, const ssize_t K);

void populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems);
