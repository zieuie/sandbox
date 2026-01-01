#include "pa.h"
#include "lib.h"
#include <time.h>

#define FILE_LINE_MAX 1024

// print a PA
void print_pa(const pa_t* pa) {
  for (int r = 0; r < pa->m; r++) {
    for (int c = 0; c < pa->n; c++) {
      printf("%d ", pa->cells[r*pa->n + c]);
    }
    printf("\n");
  }
}

// load one row of a PA. No leaks here
int load_row(const char* line, const int line_limit, cell_t* cell_buffer, const int buffer_limit) {
  int ret = 0;
  bool inside_digit = false;
  cell_t num = 0;

  for (int x = 0; x < line_limit; x++) {
    char c = line[x];
    if (isdigit(c)) {
      // we're inside a number
      inside_digit = true;
      num = 10 * num + (cell_t) (c-'0');
    } else if (inside_digit) {
      // we just finished a number
      cell_buffer[ret] = num;
      ret++;
      inside_digit = false;
      num = 0;
      if (ret > buffer_limit) {
        break;
      }
    } else if (c == 0) {
      break;
    }
  }

  if (ret < buffer_limit && inside_digit) {
    cell_buffer[ret] = num;
    ret++;
  }

  return ret;
}

// load a PA from a file.
// ! Be sure to free that pa.cells pointer!
int load_pa(const char* filename, pa_t * pa) {
  // open the file
  FILE* fp = fopen(filename, "r");
  if (fp == NULL) {
    return 1;
  }

  // find the size of the PA
  int n = 0;
  int m = 0;
  char line[FILE_LINE_MAX];
  cell_t* cell_buffer = (cell_t*) malloc(sizeof(cell_t) * FILE_LINE_MAX);
  while (fgets(line, sizeof line, fp) != NULL) {
    if (strlen(line) && line[0] != '#') {
      // see if this is a valid line
      int tmp = load_row(line, FILE_LINE_MAX, cell_buffer, FILE_LINE_MAX);
      if (n == 0) {
        n = tmp;
      } else if (n != tmp) {
        continue;
      }
      m++;
    }
  }
  rewind(fp);

  // allocate and parse the PA
  cell_t* cells = (cell_t*) zmalloc(sizeof(cell_t) * n * m);
  int row_idx = 0;
  while (fgets(line, sizeof line, fp) != NULL) {
    if (strlen(line) && line[0] != '#') {
      // see if this is a valid line
      int tmp = load_row(line, FILE_LINE_MAX, cell_buffer, FILE_LINE_MAX);
      if (n != tmp) {
        continue;
      }
      if (row_idx >= m) {
        printf("Warning: There are more than %d rows in this file\n", m);
        break;
      }

      memcpy(&cells[n*row_idx], cell_buffer, n * sizeof(cell_t));
      row_idx++;
    }
  }

  // free resources
  free(cell_buffer);
  fclose(fp);

  // set the return parameter
  pa->n = n;
  pa->m = m;
  pa->cells = cells;

  return 0;
}

void dump_pa(const pa_t* pa, const char* filename) {
  FILE* fp = fopen(filename, "w+");
  if (fp == NULL) {
    printf("Unable to open file %s! Exiting.", filename);
    exit(1);
  }

  cell_t* ptr = pa->cells;
  for (int r = 0; r < pa->m; r++) {
    for (int c = 0; c < pa->n; c++) {
      fprintf(fp, "%d ", *ptr++);
    }
    fprintf(fp, "\n");
  }

  fclose(fp);
}

void weave_pa(pa_t* pa, cell_t d) {
  // allocate new PA's resources
  long long n = pa->n + d;
  long long m = pa->m * nCr(n, d);
  cell_t* cells = (cell_t*) zcalloc(n*m, sizeof(cell_t));

  // prepare to iterate over combinations
  int comb[1024];
  for (int c = 0; c < d; c++) {
    comb[c] = c;
  }

  // fill the PA
  cell_t* cell = cells;
  
  // for every combination...
  do {
    // for every row of old...  
    for (int oldr = 0; oldr < pa->m; oldr++) {
      int l = 0;
      int h = 0;
      // for every column of new...
      for (int c = 0; c < n; c++) {
        // check if we're using one of the "chosen" symbols
        bool chosen = false;
        for (int x = 0; x < d; x++) {
          if (c == comb[x]) {
            chosen = true;
            break;
          }
        }

        // add the next symbol
        if (chosen) {
          *cell = h++;
        } else {
          *cell = pa_get(pa, oldr, l++) + d;
        }
        cell++;
      }
    }
  } while (next_combination(comb, n, d));

  // free resources
  free_pa(pa);
  pa->n = n;
  pa->m = m;
  pa->cells = cells;
}

void random_pa(pa_t* pa, cell_t n, cell_t d) {
  int m = nCr(n, d);
  cell_t* cells = (cell_t*) zcalloc(n*m, sizeof(cell_t));
  int comb[1024];
  for (int c = 0; c < d; c++) {
    comb[c] = c;
  }

  // fill the PA
  cell_t* cell = cells;
  
  // for every combination...
  do {
    int l = 0;
    int h = 0;
    // for every column of new...
    for (int c = 0; c < n; c++) {
      // check if we're using one of the "chosen" symbols
      bool chosen = false;
      for (int x = 0; x < d; x++) {
        if (c == comb[x]) {
          chosen = true;
          break;
        }
      }

      // add the next symbol
      if (chosen) {
        *cell = h++;
      } else {
        *cell = d + l++;
      }
      cell++;
    }
  } while (next_combination(comb, n, d));

  pa->n = n;
  pa->m = m;
  pa->cells = cells;
}

time_t cur_time(char* buffer, ssize_t bufsize) {
  time_t rawtime;
  struct tm *info;

  // Get the current raw time
  if (time(&rawtime) == (time_t)-1) {
      fprintf(stderr, "Error: Could not get raw time.\n");
      return -1;
  }

  // Convert raw time to local time structure
  info = localtime(&rawtime);
  if (info == NULL) {
      fprintf(stderr, "Error: Could not convert to local time.\n");
      return -1;
  }

  // Format the time using strftime
  // Example format: "YYYY-MM-DD HH:MM:SS"
  strftime(buffer, bufsize, "%Y-%m-%d %H:%M:%S", info);
  return rawtime;
}

void free_pa(pa_t *pa) {
    zfree(pa->cells, sizeof(cell_t) * pa->n * pa->m);
}