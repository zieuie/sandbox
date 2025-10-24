#include "io.h"
#include "chebyshev.h"
#include <time.h>

void help() {
  printf("Usage:\n");
  printf("  ./multihill FILENAME DISTANCE\n");
  printf("\n");
  exit(1);
}

int main(int argc, char* argv[]) {
  // parse args
  printf("argc: %d\n", argc);
  if (argc < 3) {
    help();
  }

  char* filename = argv[1];
  cell_t d = (cell_t) atoi(argv[2]);
  if (d < 2) {
    printf("Error: Distance must be at least 2.\n");
    return 1;
  }

  // load PA
  printf("Starting\n");
  pa_t pa;
  load_pa(filename, &pa);
  printf("Loaded PA with %d rows and %d columns\n", pa.m, pa.n);

  if (d > pa.n) {
    printf("Error: Distance must be at most N.\n");
    return 1;
  }

  // print pa
  print_pa(&pa);

  // check separation
  bool sep = pa_separated(&pa, 3);
  printf("Separated is %d\n", sep);

  // seed RNG
  srand((unsigned) time(NULL));

  hill_climb(&pa, d);

  char outfile[1024];
  sprintf(outfile, "pa_attempt_%d_choose_%d", pa.n, d);
  dump_pa(&pa, outfile);

  // free resources
  free(pa.cells);
}