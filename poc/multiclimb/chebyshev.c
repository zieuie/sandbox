#include <time.h>
#include <unistd.h>
#include <sys/select.h> // For select() and fd_set macros
#include <sys/wait.h>
#include <fcntl.h>

#include "chebyshev.h"
#include "lib.h"
#include "populate.h"

void do_climb(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, ssize_t score, int num_forks);

bool pa_separated(const pa_t* pa, const cell_t d) {
  for (ssize_t u = 0; u < pa->m; u++) {
    for (ssize_t v = 0; v < u; v++) {
      if (!pair_separated(pa, d, u, v)) {
        return false;
      }
    }
  }
  return true;
}


void hill_climb(const pa_t* pa, const cell_t d, int num_forks) {
  // allocate these huge things up front
  ssize_t lut_size = (long) pa->m * (pa->m-1) / 2;
  bitlut_t* foes = make_bitset(lut_size);
  bitlut_t* problems = make_bitset(lut_size);

  parallel_populate(pa, d, foes, problems, lut_size, 16);

  ssize_t foe_count = bit_sum(foes, lut_size);
  ssize_t score = bit_sum(problems, lut_size);
  printf("problems: %lu, foes: %lu, lut: %lu\n", score, foe_count, lut_size);

  do_climb(pa, d, foes, problems, score, num_forks);

  bitmap_free(foes, lut_size);
  bitmap_free(problems, lut_size);
}

typedef struct {
  cell_t row[1024];
  int32_t row_idx;
  int32_t added[1024];
  int32_t num_added;
  int32_t removed[1024];
  int32_t num_removed;
  ssize_t lamport;
  bool    overflow;
} disturb_t;

void pot_finder(const pa_t* pa, const cell_t d, const bitlut_t* foes, const bitlut_t* problems, const int outfd, const ssize_t* lamport) {

  disturb_t ret;
  cell_t* pot = ret.row;

  cell_t group[128];
  ssize_t len_group = 0;

  cell_t num_groups = (pa->n/d) + !!(pa->n%d);
  cell_t g;
  ssize_t tries = 0;
  ssize_t last_sent = -1;
  (void) last_sent;

  for(;;) {
    tries++;
    ret.lamport = *lamport;
    if (ret.lamport < 0) {
      break;
    }

    // pick a random row to improve
    ret.row_idx = (int) (rand() % pa->m);

    // pick a random group of digits to terrorize
    g = (cell_t) (rand() % num_groups);
    len_group = 0;
    for (int c = 0; c < pa->n; c++) {
      if (pa_get(pa,ret.row_idx,c) / d == g) {
        group[len_group++] = c;
      }
    }

    // shuffle dst - TODO, make sure we don't have the identity
    pa_row_copy_out(pa, ret.row, ret.row_idx);
    for (ssize_t right = 1; right < len_group; right++) {
      ssize_t left = (ssize_t) (rand() % (right+1));
      pot[group[right]] = pot[group[left]];
      pot[group[left ]] = pa_get(pa, ret.row_idx, group[right]);
    }

    // evaluate the permutation
    ret.num_added = 0;
    ret.num_removed = 0;

    for (ssize_t x = 0; x < pa->m; x++) {
      if (x == ret.row_idx || !bit_get(foes, sym_idx(ret.row_idx, x))) {
        continue;
      }

      bool new_separation = false;
      for (ssize_t c = 0; c < pa->n; c++) {
        if (abs(pa_get(pa, x, c) - pot[c]) >= d) {
          new_separation = true;
          break;
        }
      }

      if (new_separation) {
        if (bit_get(problems, sym_idx(x, ret.row_idx))) {
          // you were a problem, now you're not (because you're separated)
          ret.added[ret.num_added++] = x;
        }
      } else if (!bit_get(problems, sym_idx(x, ret.row_idx))) {
        // you were not a problem, now you are (because you're not separated)
        ret.removed[ret.num_removed++] = x;
      }
    }

    // reject bad options
    if (*lamport < 0) {
      // our parent terminated
      break;
    } else if (ret.lamport < *lamport) {
      // we are out of date
      continue;
    } else if (ret.num_added < ret.num_removed) {
      // this option sucks
      continue;
    } else if (ret.num_added == ret.num_removed && tries < 10) {
      // this option sucks
      continue;
    }

    // send!
    if (write(outfd, &ret, sizeof(ret)) != (ssize_t) sizeof(ret)) {
      continue;
    }

    tries = 0;
    last_sent = ret.lamport;
  }
}

void do_climb(const pa_t* pa, const cell_t d, bitlut_t* foes, bitlut_t* problems, ssize_t score, int num_forks) {
  // begin lamport's clock
  ssize_t *lamport = (ssize_t*) zmalloc(sizeof(ssize_t));
  disturb_t* changes = malloc(num_forks * sizeof(disturb_t));
  ssize_t sizes[1024];
  int reward[1024];
  bool trust[1024];

  for (int x = 0; x < num_forks; x++) {
    sizes[x] = 0;
    reward[x] = 0;
    trust[x] = true;
  }

  // make pipes
  int max_fd = -1;
  int pipes[1024];
  for (int x = 0; x < num_forks; x++) {
    if (pipe(&pipes[2*x]) == -1) {
        printf("Failed to make pipe. Dying.\n");
        exit(1);
    }
    if (pipes[2*x] > max_fd) {
      max_fd = pipes[2*x];
    }
  }

  // fork children
  pid_t pids[1024];
  for (int x = 0; x < num_forks; x++) {
    pid_t pid = fork();
    if(pid == 0) {
      // you are... not the father!
      close(pipes[2*x]);
      pot_finder(pa, d, foes, problems, pipes[2*x+1], lamport);
      close(pipes[2*x+1]);
      exit(0);
    }
    close(pipes[2*x+1]);
    pids[x] = pid;
  }


  struct timeval timeout;
  fd_set readfds;

  // start making changes
  ssize_t best_score = score;
  ssize_t last_score = score;
  ssize_t last_write_score = score;
  time_t last_write_time = time(NULL);
  char outfile[1024];
  sprintf(outfile, "pa_%d_choose_%d_unfinished.txt", pa->n, d);

  char time_str[80];

  for(ssize_t it_count = 0;; it_count++) {

    *lamport = it_count;
    if (score < best_score) {
      best_score = score;
    }

    bool should_backup = time(NULL) - last_write_time > 2 && last_write_score > score;
    if (should_backup) {
      printf("Periodic backup to %s\n", outfile);
      dump_pa(pa, outfile);
      time(&last_write_time);
      last_write_score = score;
    }

    if (should_backup || it_count % 100000 == 0 || (score < last_score && score < 100)) {
      cur_time(time_str, 80);
      printf("[%s] P(%d,%d) Iteration: %lu Score: %lu Best: %li\n", time_str, pa->n, d, it_count, score, best_score);
      last_score = score;

      // for(int x = 0; x < num_forks; x++) {
      //   printf("%d ", reward[x]);
      // }
      // printf("\n");
    }

    if (score == 0) {
      break;
    }

    // wait for a child to respond
    disturb_t change;

    for(;;) {
      // set up the select
      timeout.tv_sec = 1;
      timeout.tv_usec = 0;
      FD_ZERO(&readfds);
      for (int x = 0; x < num_forks; x++) {
        if (!trust[x]) {
          continue;
        }
        FD_SET(pipes[2*x], &readfds);
      }

      // do the select
      if (select(max_fd + 1, &readfds, NULL, NULL, &timeout) < 0) {
        printf("Failed to select!\n");
        fflush(stdout);
        continue;
      }

      // find the chosen one
      int chosen = -1;
      for (int x = 0; x < num_forks; x++) {
        if (FD_ISSET(pipes[2*x], &readfds)) {
          chosen = x;
          break;
        }
      }

      // no chosen one
      if (chosen < 0) {
        printf("No pipes were ready\n");
        fflush(stdout);
        continue;
      }

      // pull the data
      ssize_t bytes_read = read(pipes[2*chosen], ((char*) &changes[chosen]) + sizes[chosen], ((ssize_t) sizeof(disturb_t)) - sizes[chosen]);

      if (bytes_read <= 0) {
        printf("Failed to read from %d; %lu\n", chosen, bytes_read);
      }

      sizes[chosen] += bytes_read;
      if (sizes[chosen] < (ssize_t) sizeof(disturb_t)) {
        continue;
      } else if (sizes[chosen] > (ssize_t) sizeof(disturb_t)) {
        printf("Buffer overrun in child %d\n", chosen);
        fflush(stdout);
        sizes[chosen] = 0;
        continue;
      }

      change = changes[chosen];
      sizes[chosen] = 0;

      if (change.lamport != it_count) {
        continue;
      } else {
        reward[chosen]++;
        break;
      }
    }

    pa_row_copy_in(pa, change.row, change.row_idx);
    score -= change.num_added - change.num_removed;
    for (ssize_t x = 0; x < change.num_added; x++) {
      bit_clear(problems, sym_idx(change.added[x], change.row_idx));
      bit_clear(problems, sym_idx(change.row_idx, change.added[x]));
    }
    for (ssize_t x = 0; x < change.num_removed; x++) {
      bit_set(problems, sym_idx(change.removed[x], change.row_idx));
      bit_set(problems, sym_idx(change.row_idx, change.removed[x]));
    }
  }

  *lamport = -1;

  printf("Periodic backup to %s\n", outfile);
  dump_pa(pa, outfile);

  int status;
  for (int64_t j = 0; j < num_forks; j++) {
    if (waitpid(pids[j], &status, 0) < 0) {
      printf("Warning: waitpid %ld failed on pid %u\n", j, pids[j]);
    } else if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
      printf("Warning: waitpid %ld failed on pid %u with status %d\n", j, pids[j], status);
    }
    fflush(stdout);
  }

  for (int x = 0; x < num_forks; x++) {
    close(pipes[2*x]);
  }
  free(changes);
  zfree(lamport, sizeof(ssize_t));
}