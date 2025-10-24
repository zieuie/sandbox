#include "pa.h"
#include "chebyshev.h"
#include <time.h>

void help() {
  printf("Usage:\n");
  printf("  ./multihill FILENAME DISTANCE\n");
  printf("\n");
  exit(1);
}

void resume_computation(pa_t* pa, int n, int d) {
  // make a PA from scratch
  if (2*d >= n) {
    random_pa(pa, n, d);
    printf("Generated a PA\n");
    return;
  }
  
  char filename[1024];
  // try to weave a smaller PA
  sprintf(filename, "pa_%d_choose_%d_unfinished.txt", n, d);
  if (!load_pa(filename, pa)) {
    printf("Loaded %s and continuing\n", filename);
    return;
  }

  // weave PA
  sprintf(filename, "pa_%d_choose_%d_verified.txt", n-d, d);
  if (!load_pa(filename, pa)) {
    printf("Loaded %s and expanding\n", filename);
    weave_pa(pa, d);
    return;
  }

  printf("Failed to find an appropriate file.\n");
}

int main(int argc, char* argv[]) {
  // error checking
  if (argc < 3) {
    help();
  }

  // parse args
  cell_t n = (cell_t) atoi(argv[1]);
  cell_t d = (cell_t) atoi(argv[2]);
  if (d < 2) {
    printf("Error: Distance must be at least 2.\n");
    return 1;
  }

  if (d > n) {
    printf("Error: Distance must be at most N.\n");
    return 1;
  }

  // load PA
  pa_t pa;
  resume_computation(&pa, n, d);

  // print pa
  print_pa(&pa);

  // check separation
  bool sep = pa_separated(&pa, 3);
  printf("Separated is %d\n", sep);

  // seed RNG
  srand((unsigned) time(NULL));

  hill_climb(&pa, d);

  char outfile[1024];
  if (pa_separated(&pa, d)) {
    sprintf(outfile, "pa_%d_choose_%d_verified.txt", pa.n, d);
    printf("Verified!\n");
  } else {
    sprintf(outfile, "pa_%d_choose_%d_unfinished.txt", pa.n, d);
    printf("Failed to verify!\n");
  }
  dump_pa(&pa, outfile);
  printf("Saved to %s\n", outfile);

  // free resources
  free(pa.cells);
}