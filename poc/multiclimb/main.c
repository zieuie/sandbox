#include "io.h"

void help() {
  printf("Usage:\n");
  printf("  ./multihill FILENAME\n");
  printf("\n");
  exit(1);
}

int main(int argc, char* argv[]) {
  // parse args
  printf("argc: %d\n", argc);
  if (argc < 2) {
    help();
  }

  char* filename = argv[1];
  
  // load PA
  printf("Starting\n");
  pa_t pa;
  load_pa(filename, &pa);
  printf("Loaded PA with %d rows and %d columns\n", pa.m, pa.n);

  // print pa
  print_pa(&pa);

  bool sep = pa_separated(&pa, 3);
  printf("Separated is %d\n", sep);

  // free resources
  free(pa.cells);
}