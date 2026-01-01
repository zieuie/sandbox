#include "populate.h"
#include <sys/wait.h>


// Index for pair (u,v) with u != v; we normalize so u < v
ssize_t sym_idx(ssize_t u, ssize_t v) {
  if (v>u){
    return v * (v - 1) / 2 + u;
  } else {
    return u * (u - 1) / 2 + v;
  }
}


// end_v must be greater than 5
void* worker_body(const pa_t* pa, const int d, bitlut_t *foes, bitlut_t *problems, const int64_t start_v, const int64_t end_v, const int thread_idx) {
  {
    // if we're not the first chunk, skip what he bit off
    ssize_t u = 0;
    ssize_t v = start_v;
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
  for(ssize_t v = start_v+1; v < end_v; v++) {
    for (ssize_t u = 0; u < v; u++) {
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
  if (end_v < (ssize_t) pa->m) {
    ssize_t v = end_v;
    for (ssize_t u = 0; u < v; u++) {
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

void parallel_populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, const ssize_t lut_size, const ssize_t K) {
  pid_t pids[1024];

  // spawn
  int64_t j = 0;
  ssize_t partial = 0;
  ssize_t last_v = 0;
  for (ssize_t v = 0; v < (ssize_t) pa->m; v++) {
    // get the partial sum
    partial += v;
    if (partial < lut_size/K && v != (ssize_t) pa->m - 1) {
      continue;
    }

    // get the new end
    ssize_t end_v = v+1;
    if (j >= K-1) {
      end_v = pa->m;
      partial = (v * (v+1) / 2) - (last_v * (last_v-1) / 2);
    }

    // fork a child to compute
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
  for (int64_t j = 0; j < K; j++) {
    if (waitpid(pids[j], &status, 0) < 0) {
      printf("Warning: waitpid %ld failed on pid %u\n", j, pids[j]);
    } else if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
      printf("Warning: waitpid %ld failed on pid %u with status %d\n", j, pids[j], status);
    }
  }
}

void populate(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems) {
  for(ssize_t v = 0; v < pa->m; v++) {
    for (ssize_t u = 0; u < v; u++) {
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
