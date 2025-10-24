#include <time.h>
#include <unistd.h>

#include "chebyshev.h"
#include "lib.h"

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

void hill_climb(const pa_t* pa, const cell_t d) {
  // allocate these huge things up front
  bitset_t* foes = make_bitset((long) pa->m * (pa->m-1) / 2);
  bitset_t* problems = make_bitset((long) pa->m * (pa->m-1) / 2);
  cell_t *pot = (cell_t*) malloc(sizeof(cell_t) * pa->n);

  // make foes
  for(int v = 0; v < pa->m; v++) {
    for (int u = 0; u < v; u++) {
      bool sep = false;
      for (int c = 0; c < pa->n; c++) {
        if (abs( pa_get(pa,v,c)/d - pa_get(pa,u,c)/d ) > 1) {
          sep = true;
          break;
        }
      }
      if (!sep) {
        bit_set(foes, sym_idx(u,v));
      }
    }
  }

  // make problems
  size_t score = 0;
  for(int v = 0; v < pa->m; v++) {
    for (int u = 0; u < v; u++) {
      if (u != v && bit_get(foes, sym_idx(u, v)) && !pair_separated(pa,d,u,v)) {
        bit_set(problems, sym_idx(u,v));
        score += 2;
      }
    }
  }

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

    if (it_count % 100000 == 0 || (score < last_score && score < 200)) {
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
    score -= 2*(num_added - num_removed);
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

  free(foes);
  free(problems);
  free(pot);
  free(added);
  free(removed);
}