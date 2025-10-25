#include "populate.h"
#include <sys/wait.h>


// Index for pair (u,v) with u != v; we normalize so u < v
size_t sym_idx(size_t u, size_t v) {
  if (v>u){
    return v * (v - 1) / 2 + u;
  } else {
    return u * (u - 1) / 2 + v;
  }
}


// end_v must be greater than 5
void* worker_body(const pa_t* pa, const int d, bitlut_t *foes, bitlut_t *problems, const uint64_t start_v, const uint64_t end_v, const int thread_idx) {
  {
    // if we're not the first chunk, skip what he bit off
    size_t u = 0;
    size_t v = start_v;
    if (thread_idx > 0) {
      while(sym_idx(u,v) % 8 != 0) {
        u++;
      }
    }

    // then finish this first row
    for (; u < v; u++) {
      bool foe = false;
      bool sep = false;
      for (int c = 0; c < pa->n; c++) {
        int a = pa_get(pa, u, c);
        int b = pa_get(pa, v, c);
        if (abs( a/d - b/d ) > 1) {
          foe = true;
          break;
        } else if ( abs(a-b) >= d ) {
          sep = true;
        }
      }
      if (!foe) {
        bit_set(foes, sym_idx(u,v));
        if (!sep) {
          bit_set(problems, sym_idx(u,v));
        }
      }
    }
  }

  // middle bytes
  for(size_t v = start_v+1; v < end_v; v++) {
    for (size_t u = 0; u < v; u++) {
      bool foe = false;
      bool sep = false;
      for (int c = 0; c < pa->n; c++) {
        int a = pa_get(pa, u, c);
        int b = pa_get(pa, v, c);
        if (abs( a/d - b/d ) > 1) {
          foe = true;
          break;
        } else if ( abs(a-b) >= d ) {
          sep = true;
        }
      }
      if (!foe) {
        bit_set(foes, sym_idx(u,v));
        if (!sep) {
          bit_set(problems, sym_idx(u,v));
        }
      }
    }
  }

  // finish the byte, into the next row, if there is a next row
  if (end_v < (size_t) pa->m) {
    size_t v = end_v;
    for (size_t u = 0; u < v; u++) {
      // bail and don't touch the next byte
      if (sym_idx(u,v) % 8 == 0) {
        break;
      }
      bool foe = false;
      bool sep = false;
      for (int c = 0; c < pa->n; c++) {
        int a = pa_get(pa, u, c);
        int b = pa_get(pa, v, c);
        if (abs( a/d - b/d ) > 1) {
          foe = true;
          break;
        } else if ( abs(a-b) >= d ) {
          sep = true;
        }
      }
      if (!foe) {
        bit_set(foes, sym_idx(u,v));
        if (!sep) {
          bit_set(problems, sym_idx(u,v));
        }
      }
    }
  }

  return NULL;
}

void parallel_populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, const size_t lut_size, const size_t K) {
  pid_t pids[1024];

  // spawn
  uint64_t j = 0;
  size_t partial = 0;
  size_t last_v = 0;
  for (size_t v = 0; v < (size_t) pa->m; v++) {
    // get the partial sum
    partial += v;
    if (partial < lut_size/K && v != (size_t) pa->m - 1) {
      continue;
    }

    // get the new end
    size_t end_v = v+1;
    if (j >= K-1) {
      end_v = pa->m;
      partial = (v * (v+1) / 2) - (last_v * (last_v-1) / 2);
    }

    // fork a child to compute
    printf("Forking from %lu to %lu (total %lu)\n", last_v, end_v, partial);
    pid_t child = fork();
    if (child == 0) {
      worker_body(pa, d, foes, problems, last_v, end_v, j);
      _exit(0);
    }

    // save the child pid and move on
    pids[j] = child;
    last_v = v+1;
    partial = 0;
    j++;
  }

  // join
  int status;
  for (uint64_t j = 0; j < K; j++) {
    if (waitpid(pids[j], &status, 0) < 0) {
      printf("Warning: waitpid %ld failed on pid %u\n", j, pids[j]);
    } else if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
      printf("Warning: waitpid %ld failed on pid %u with status %d\n", j, pids[j], status);
    }
  }
}

void populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems) {
  for(int v = 0; v < pa->m; v++) {
    for (int u = 0; u < v; u++) {
      bool foe = false;
      bool sep = false;
      for (int c = 0; c < pa->n; c++) {
        int a = pa_get(pa,u,c);
        int b = pa_get(pa,v,c);
        if (abs( a/d - b/d ) > 1) {
          foe = true;
          break;
        } else if ( abs(a-b) >= d ) {
          sep = true;
        }
      }
      if (!foe) {
        bit_set(foes, sym_idx(u,v));
        if (!sep) {
          bit_set(problems, sym_idx(u,v));
        }
      }
    }
  }
}
