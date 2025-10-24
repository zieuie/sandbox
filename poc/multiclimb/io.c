#include "io.h"
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
void load_pa(const char* filename, pa_t * pa) {
  // open the file
  FILE* fp = fopen(filename, "r");
  if (fp == NULL) {
    printf("Unable to open file %s! Exiting.", filename);
    exit(1);
  }

  // find the size of the PA
  int n = 0;
  int m = 0;
  char* line = (char*) malloc(sizeof(char) * FILE_LINE_MAX);
  cell_t* cell_buffer = (cell_t*) malloc(sizeof(cell_t) * FILE_LINE_MAX);
  size_t line_size = FILE_LINE_MAX;

  while (getline(&line, &line_size, fp) >= 0) {
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
  cell_t* cells = (cell_t*) malloc(sizeof(cell_t) * n * m);
  int row_idx = 0;
  while (getline(&line, &line_size, fp) >= 0) {
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
  free(line);
  fclose(fp);

  // set the return parameter
  pa->n = n;
  pa->m = m;
  pa->cells = cells;
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
}

void cur_time(char* buffer, size_t bufsize) {
  time_t rawtime;
  struct tm *info;

  // Get the current raw time
  if (time(&rawtime) == (time_t)-1) {
      fprintf(stderr, "Error: Could not get raw time.\n");
      return;
  }

  // Convert raw time to local time structure
  info = localtime(&rawtime);
  if (info == NULL) {
      fprintf(stderr, "Error: Could not convert to local time.\n");
      return;
  }

  // Format the time using strftime
  // Example format: "YYYY-MM-DD HH:MM:SS"
  strftime(buffer, bufsize, "%Y-%m-%d %H:%M:%S", info);
}