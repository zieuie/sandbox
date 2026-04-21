#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <string.h>

int mhx_head_main(int argc, char **argv);
int mhx_worker_main(int argc, char **argv);

static void mhx_usage(FILE *f, const char *argv0) {
  fprintf(f, "usage:\n");
  fprintf(f, "  %s --n N --d D [--workers N] [--port PORT] [--eps 0.1] [--verbose]\n", argv0);
  fprintf(f, "  %s --worker HOST[:PORT] --workers N [--verbose]\n", argv0);
  fprintf(f, "\n");
  fprintf(f, "examples:\n");
  fprintf(f, "  %s --n 12 --d 3 --port 9001 --workers 4\n", argv0);
  fprintf(f, "  %s --worker headbox:9001 --workers 4\n", argv0);
}

int main(int argc, char **argv) {
  if (argc < 2) {
    mhx_usage(stderr, argv[0]);
    return 2;
  }

  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "--worker") == 0) return mhx_worker_main(argc, argv);
  }
  return mhx_head_main(argc, argv);
}
