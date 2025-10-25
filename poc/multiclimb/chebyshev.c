#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/wait.h>

#include "chebyshev.h"
#include "lib.h"

void do_climb(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, size_t score);

// Index for pair (u,v) with u != v; we normalize so u < v
static inline size_t sym_idx(size_t u, size_t v) {
  if (v>u){
    return v * (v - 1) / 2 + u;
  } else {
    return u * (u - 1) / 2 + v;
  }
}


bool pa_separated(const pa_t* pa, const cell_t d) {
  for (int u = 0; u < pa->m; u++) {
    for (int v = 0; v < u; v++) {
      if (!pair_separated(pa, d, u, v)) {
        return false;
      }
    }
  }
  return true;
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


void hill_climb(const pa_t* pa, const cell_t d) {
  // allocate these huge things up front
  size_t lut_size = (long) pa->m * (pa->m-1) / 2;
  bitlut_t* foes = make_bitset(lut_size);
  bitlut_t* problems = make_bitset(lut_size);

  // populate(pa, d, foes, problems);
  parallel_populate(pa, d, foes, problems, lut_size, 16);

  size_t foe_count = bit_sum(foes, lut_size);
  size_t score = bit_sum(problems, lut_size);
  printf("problems: %lu, foes: %lu, lut: %lu\n", score, foe_count, lut_size);

  do_climb(pa, d, foes, problems, score);

  bitmap_free(foes, lut_size);
  bitmap_free(problems, lut_size);
}

void do_climb(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, size_t score) {
  // our next row
  cell_t *pot = (cell_t*) malloc(sizeof(cell_t) * pa->n);

  // the indices within the row of those symbols in the chosen group
  cell_t sanity[1024];

  // the indices within the row of those symbols in the chosen group
  cell_t digit_indices[1024];
  int len_group = 0;

  // a list of those rows which are separated from pot but not from the original row
  long long *added = (long long*) malloc(sizeof(long long) * pa->m);
  int num_added = 0;

  // a list of those rows which are separated from the original row but not from pot
  long long *removed = (long long*) malloc(sizeof(long long) * pa->m);
  int num_removed = 0;

  cell_t num_groups = (pa->n/d) + !!(pa->n%d);
  size_t best_score = score;
  size_t last_score = score;
  size_t last_tweak = 0;
  size_t coverage = 0;

  for(size_t it_count = 0;; it_count++) {
    if (score < best_score) {
      best_score = score;
    }

    if (it_count % 100000 == 0 || (score < last_score && score < 100)) {
      char time_str[80];
      cur_time(time_str, 80);
      printf("[%s] P(%d,%d) Iteration: %lu Score: %lu Best: %li Coverage: %li of %d Last tweak: %li\n", time_str, pa->n, d, it_count, score, best_score, coverage, pa->m, last_tweak);
      last_score = score;
    }

    if (score == 0) {
      break;
    }

    // pick a disturbance
    int r;
    cell_t group;

    for (size_t tries = 0; ; tries++) {
      // pick a random row to improve
      r = (int) (rand() % pa->m);

      // pick a random group of digits to terrorize
      group = (cell_t) (rand() % num_groups);
      len_group = 0;
      for (int c = 0; c < pa->n; c++) {
        if (pa_get(pa,r,c) / d == group) {
          digit_indices[len_group++] = c;
        }
      }

      // shuffle dst - TODO, make sure we don't have the identity
      pa_row_copy_out(pa, pot, r);
      for (int right = 1; right < len_group; right++) {
        int left = (int) (rand() % (right+1));
        pot[digit_indices[right]] = pot[digit_indices[left]];
        pot[digit_indices[left ]] = pa_get(pa, r, digit_indices[right]);
      }

      // evaluate the permutation
      num_added = 0;
      num_removed = 0;

      for (int x = 0; x < pa->m; x++) {
        if (x == r || !bit_get(foes, sym_idx(r, x))) {
          continue;
        }

        bool new_separation = false;
        for (int c = 0; c < pa->n; c++) {
          if (abs(pa_get(pa, x, c) - pot[c]) >= d) {
            new_separation = true;
            break;
          }
        }

        if (new_separation) {
          if (bit_get(problems, sym_idx(x, r))) {
            // you were a problem, now you're not (because you're separated)
            added[num_added++] = x;
          }
        } else if (!bit_get(problems, sym_idx(x, r))) {
          // you were not a problem, now you are (because you're not separated)
          removed[num_removed++] = x;
        }
      }

      // was it good enough?
      if (num_added > num_removed) {
        break;
      } else if (num_added == num_removed && tries > 10) {
        break;
      // } else if (tries > 10000000 && (num_removed - num_added < 2)) {
      //   printf("backtracking...\n");
      //   break;
      }
    } // end of choosing a disturbance

    for (int c = 0; c < pa->n; c++) {
      sanity[c] = 0;
    }
    bool sane = true;
    for (int c = 0; c < pa->n; c++) {
      if (sanity[pot[c]]) {
        sane = false;
      }
      sanity[pot[c]] = 1;
    }

    if (!sane) {
      printf("NOT SANE!\n");
    // }

    // if (true) {
      // display for debug
      printf("---\n");
      printf("Mutating row %d\n", r);
      for (int c = 0; c < pa->n; c++) {
        printf("%d ", pa_get(pa, r, c));
      }
      printf("\n");
      for (int c = 0; c < pa->n; c++) {
        printf("%d ", pot[c]);
      }
      printf("\n");
      printf("\n");
      printf("Added %d\n", num_added);
      for (int u = 0; u < num_added; u++) {
        for (int c =0 ; c < pa->n; c++) {
          printf("%d ", pa_get(pa, added[u], c));
        }
        printf("\n");
      }
      printf("\n");
      printf("Removed %d\n", num_removed);
      for (int u = 0; u < num_removed; u++) {
        for (int c = 0; c < pa->n; c++) {

          printf("%d ", pa_get(pa, removed[u], c));
        }
        printf("\n");
      }

      getchar();
    }

    // apply the change
    pa_row_copy_in(pa, pot, r);
    score -= num_added - num_removed;
    // printf("added: %d, removed: %d\n", num_added, num_removed);
    for (int x = 0; x < num_added; x++) {
      // printf("Clear added[%d] = %lli (r = %d)\n", x, added[x], r);
      // fflush(stdout);
      bit_clear(problems, sym_idx(added[x], r));
      bit_clear(problems, sym_idx(r, added[x]));
    }
    for (int x = 0; x < num_removed; x++) {
      // printf("Clear removed[%d] = %lli (r = %d)\n", x, removed[x], r);
      // fflush(stdout);
      bit_set(problems, sym_idx(removed[x], r));
      bit_set(problems, sym_idx(r, removed[x]));
    }
  }

  free(pot);
  free(added);
  free(removed);
}