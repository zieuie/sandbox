#include <time.h>
#include <unistd.h>

#include "chebyshev.h"
#include "bitmap.h"

#define BIT_SET_2D(map, r, c) bit_set(map, r*pa->m + c)
#define BIT_GET_2D(map, r, c) bit_get(map, r*pa->m + c)
#define BIT_CLR_2D(map, r, c) bit_clear(map, r*pa->m + c)

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
  // make foes
  unsigned char* foes = (unsigned char*) calloc(pa->m * pa->m, sizeof(unsigned char));
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
        BIT_SET_2D(foes, u, v);
        BIT_SET_2D(foes, v, u);
      }
    }
  }

  // make problems
  int score = 0;
  unsigned char* problems = (unsigned char*) calloc(pa->m * pa->m, sizeof(unsigned char));
  for(int v = 0; v < pa->m; v++) {
    for (int u = 0; u < v; u++) {
      if (u != v && BIT_GET_2D(foes, u, v) && !pair_separated(pa,d,u,v)) {
        BIT_SET_2D(problems, u, v);
        BIT_SET_2D(problems, v, u);
        score += 2;
      }
    }
  }

  // the indices within the row of those symbols in the chosen group
  cell_t *sanity = (cell_t*) malloc(sizeof(cell_t) * pa->n);

  // the potential row to change
  cell_t *pot = (cell_t*) malloc(sizeof(cell_t) * pa->n);

  // the indices within the row of those symbols in the chosen group
  cell_t *digit_indices = (cell_t*) malloc(sizeof(cell_t) * pa->n);
  int len_group = 0;

  // a list of those rows which are separated from pot but not from the original row
  cell_t *added   = (cell_t*) malloc(sizeof(cell_t) * pa->m);
  int num_added = 0;

  // a list of those rows which are separated from the original row but not from pot
  cell_t *removed = (cell_t*) malloc(sizeof(cell_t) * pa->m);
  int num_removed = 0;

  cell_t num_groups = (pa->n/d) + !!(pa->n%d);
  size_t best_score = score;
  size_t last_tweak = 0;
  size_t coverage = 0;

  for(size_t it_count = 0;; it_count++) {
    if (score < best_score) {
      best_score = score;
    }

    if (it_count % 100 == 0 || score <= 0) {
      char time_str[80];
      cur_time(time_str, 80);
      printf("[%s] P(%d,%d) Iteration: %lu Score: %d Best: %li Coverage: %li of %d Last tweak: %li\n", time_str, pa->n, d, it_count, score, best_score, coverage, pa->m, last_tweak);
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
        if (x == r || !BIT_GET_2D(foes, r, x)) {
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
          if (BIT_GET_2D(problems, x, r)) {
            // you were a problem, now you're not (because you're separated)
            added[num_added++] = x;
          }
        } else if (!BIT_GET_2D(problems, x, r)) {
          // you were not a problem, now you are (because you're not separated)
          removed[num_removed++] = x;
        }
      }

      // was it good enough?
      if (num_added > num_removed) {
        break;
      } else if (num_added == num_removed && tries > 10000) {
        break;
      } else if (tries > 10000000 && (num_removed - num_added < 2)) {
        printf("backtracking...\n");
        break;
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
    for (int x = 0; x < num_added; x++) {
      BIT_CLR_2D(problems, added[x], r);
      BIT_CLR_2D(problems, r, added[x]);
    }
    for (int x = 0; x < num_removed; x++) {
      BIT_SET_2D(problems, removed[x], r);
      BIT_SET_2D(problems, r, removed[x]);
    }
  }

  free(foes);
  free(problems);
  free(pot);
  free(digit_indices);
  free(added);
  free(removed);
}