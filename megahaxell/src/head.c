#define _POSIX_C_SOURCE 200809L

#include <zmq.h>

#include "megahaxell/math/colors.h"
#include "megahaxell/math/haxell.h"

#include <errno.h>
#include <ctype.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;

static void mhx_on_sigint(int sig) {
  (void)sig;
  g_stop = 1;
}

static void mhx_usage(FILE *f, const char *argv0) {
  fprintf(f, "usage:\n");
  fprintf(f,
          "  %s --n N --d D [--eps 0.1] [--bind tcp://*:9001] [--local-workers N] [--verbose]\n",
          argv0);
  fprintf(f, "    [--state FILE] [--save-interval SECONDS] [--no-resume]\n");
}

static void mhx_die_zmq(const char *what) {
  int e = zmq_errno();
  fprintf(stderr, "%s: %s (%d)\n", what, zmq_strerror(e), e);
  exit(1);
}

static int mhx_recv_frame(void *sock, void **data, size_t *len) {
  *data = NULL;
  *len = 0;
  zmq_msg_t msg;
  if (zmq_msg_init(&msg) != 0) return -1;
  int n = zmq_msg_recv(&msg, sock, 0);
  if (n < 0) {
    zmq_msg_close(&msg);
    return -1;
  }
  size_t sz = zmq_msg_size(&msg);
  void *buf = malloc(sz);
  if (!buf) {
    zmq_msg_close(&msg);
    return -1;
  }
  memcpy(buf, zmq_msg_data(&msg), sz);
  zmq_msg_close(&msg);
  *data = buf;
  *len = sz;
  return 0;
}

static int mhx_send_frame(void *sock, const void *data, size_t len, int flags) {
  int rc = zmq_send(sock, data, len, flags);
  return rc < 0 ? -1 : 0;
}

static int mhx_send_text_to(void *router, const void *ident, size_t ident_len, const char *text) {
  if (mhx_send_frame(router, ident, ident_len, ZMQ_SNDMORE) != 0) return -1;
  if (mhx_send_frame(router, text, strlen(text), 0) != 0) return -1;
  return 0;
}

static int mhx_color_to_string(const uint8_t *c, int n, char *out, size_t out_len) {
  size_t off = 0;
  for (int i = 0; i < n; i++) {
    int w = snprintf(out + off, out_len - off, "%s%u", (i ? "," : ""), (unsigned)c[i]);
    if (w <= 0) return -1;
    off += (size_t)w;
    if (off >= out_len) return -1;
  }
  return 0;
}

static int mhx_perm_to_string(const uint16_t *p, int n, char *out, size_t out_len) {
  size_t off = 0;
  for (int i = 0; i < n; i++) {
    int w = snprintf(out + off, out_len - off, "%s%u", (i ? " " : ""), (unsigned)p[i]);
    if (w <= 0) return -1;
    off += (size_t)w;
    if (off >= out_len) return -1;
  }
  return 0;
}

static int mhx_parse_res_header(const char *line, unsigned long long *job_id, char status[8], size_t *diff_len) {
  status[0] = '\0';
  if (sscanf(line, "RES %llu %7s %zu", job_id, status, diff_len) != 3) return -1;
  return 0;
}

static int mhx_parse_u8_list(const char *s, int n, uint8_t *out) {
  const char *p = s;
  for (int i = 0; i < n; i++) {
    while (*p == ' ' || *p == '\t') p++;
    char *end = NULL;
    long v = strtol(p, &end, 10);
    if (!end || end == p) return -1;
    if (v < 0 || v > 255) return -1;
    out[i] = (uint8_t)v;
    p = end;
    while (*p == ' ' || *p == '\t') p++;
    if (i != n - 1) {
      if (*p != ',') return -1;
      p++;
    }
  }
  return 0;
}

static int mhx_parse_u16_list(const char *s, int n, uint16_t *out) {
  const char *p = s;
  for (int i = 0; i < n; i++) {
    while (*p == ' ' || *p == '\t') p++;
    char *end = NULL;
    long v = strtol(p, &end, 10);
    if (!end || end == p) return -1;
    if (v < 0 || v > 65535) return -1;
    out[i] = (uint16_t)v;
    p = end;
    int saw_ws = 0;
    while (*p == ' ' || *p == '\t') {
      saw_ws = 1;
      p++;
    }
    if (i != n - 1) {
      if (*p == ',') {
        p++;
      } else if (saw_ws) {
        /* ok: space-separated list */
      } else {
        return -1;
      }
    }
  }
  return 0;
}

static int mhx_parse_perm_line(const char *line, int n, uint16_t *out_perm) {
  const char *p = line;
  for (int i = 0; i < n; i++) {
    while (*p == ' ' || *p == '\t') p++;
    if (*p == '\0') return -1;
    char *end = NULL;
    long v = strtol(p, &end, 10);
    if (!end || end == p) return -1;
    if (v < 0 || v > 65535) return -1;
    out_perm[i] = (uint16_t)v;
    p = end;
  }
  return 0;
}

static void mhx_perm_to_color(int n, int d, const uint16_t *perm, uint8_t *out_color) {
  for (int i = 0; i < n; i++) {
    out_color[i] = (uint8_t)(perm[i] / (uint16_t)d);
  }
}

// static int mhx_validate_M(int n, int d, const struct mhx_map *M) {
//   for (size_t i = 0; i < M->len; i++) {
//     if (M->e[i].perm.n != n) return -1;
//     for (size_t j = 0; j < i; j++) {
//       if (mhx_perm_edge(n, d, M->e[i].perm.v, M->e[j].perm.v)) {
//         return -1;
//       }
//     }
//   }
//   return 0;
// }

static int mhx_file_exists(const char *path) {
  return access(path, R_OK) == 0;
}

static int mhx_load_state(const char *path, int n, int d, struct mhx_map *M) {
  FILE *f = fopen(path, "r");
  if (!f) return -1;

  char *line = NULL;
  size_t cap = 0;
  ssize_t got;

  uint16_t *perm = (uint16_t *)malloc((size_t)n * sizeof(uint16_t));
  uint8_t *color = (uint8_t *)malloc((size_t)n);
  if (!perm || !color) {
    free(perm);
    free(color);
    fclose(f);
    return -1;
  }

  while ((got = getline(&line, &cap, f)) > 0) {
    (void)got;
    /* strip comments */
    char *hash = strchr(line, '#');
    if (hash) *hash = '\0';
    /* trim leading whitespace */
    char *p = line;
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p == '\0') continue;

    if (mhx_parse_perm_line(p, n, perm) != 0) {
      free(line);
      free(perm);
      free(color);
      fclose(f);
      return -1;
    }
    mhx_perm_to_color(n, d, perm, color);

    struct mhx_perm pv = {.n = n, .v = perm};
    if (mhx_map_set(M, color, &pv) != 0) {
      free(line);
      free(perm);
      free(color);
      fclose(f);
      return -1;
    }
  }

  free(line);
  free(perm);
  free(color);
  fclose(f);

  return 0; // mhx_validate_M(n, d, M);
}

static int mhx_save_state_atomic(const char *path, const struct mhx_map *M) {
  char tmp[1024];
  int n = snprintf(tmp, sizeof(tmp), "%s.tmp", path);
  if (n <= 0 || (size_t)n >= sizeof(tmp)) return -1;

  FILE *f = fopen(tmp, "w");
  if (!f) return -1;

  for (size_t i = 0; i < M->len; i++) {
    for (int j = 0; j < M->n; j++) {
      fprintf(f, "%s%u", (j ? " " : ""), (unsigned)M->e[i].perm.v[j]);
    }
    fputc('\n', f);
  }

  if (fclose(f) != 0) return -1;
  if (rename(tmp, path) != 0) return -1;
  return 0;
}

static int mhx_merge_diff(int n, int d, struct mhx_map *M, const struct mhx_map *diff) {
  if (diff->len == 0) return 0;
  /* validate: no edges vs rows that will remain, and no edges within diff */
  for (size_t i = 0; i < diff->len; i++) {
    for (size_t j = 0; j < M->len; j++) {
      /* If this M key is being replaced by the diff, skip checking against the old value. */
      if (mhx_map_get(diff, M->e[j].color) != NULL) continue;
      if (mhx_perm_edge(n, d, diff->e[i].perm.v, M->e[j].perm.v)) return 0;
    }
  }
  for (size_t i = 0; i < diff->len; i++) {
    for (size_t j = 0; j < i; j++) {
      if (mhx_perm_edge(n, d, diff->e[i].perm.v, diff->e[j].perm.v)) return 0;
    }
  }

  for (size_t i = 0; i < diff->len; i++) {
    if (mhx_map_set(M, diff->e[i].color, &diff->e[i].perm) != 0) return 0;
  }
  return 1;
}

static char *mhx_build_job(unsigned long long job_id, int n, int d, double eps, const uint8_t *A, const struct mhx_map *M) {
  size_t cap = 1024 + (size_t)M->len * (size_t)n * 16;
  char *buf = (char *)malloc(cap);
  if (!buf) return NULL;
  size_t off = 0;

  char acol[4096];
  if (mhx_color_to_string(A, n, acol, sizeof(acol)) != 0) {
    free(buf);
    return NULL;
  }
  int w = snprintf(buf + off, cap - off, "JOB %llu %d %d %.17g %s\n", job_id, n, d, eps, acol);
  if (w <= 0) w = 0;
  off += (size_t)w;
  w = snprintf(buf + off, cap - off, "M %zu\n", M->len);
  if (w <= 0) w = 0;
  off += (size_t)w;

  for (size_t i = 0; i < M->len; i++) {
    char cbuf[4096];
    char pbuf[8192];
    if (mhx_color_to_string(M->e[i].color, n, cbuf, sizeof(cbuf)) != 0) continue;
    if (mhx_perm_to_string(M->e[i].perm.v, n, pbuf, sizeof(pbuf)) != 0) continue;
    w = snprintf(buf + off, cap - off, "%s|%s\n", cbuf, pbuf);
    if (w <= 0) w = 0;
    off += (size_t)w;
    if (off + 64 >= cap) break;
  }
  snprintf(buf + off, cap - off, "END\n");
  return buf;
}

static int mhx_find_next_color(const struct mhx_colors *colors, const struct mhx_map *M, size_t *cursor, const uint8_t **out) {
  size_t start = *cursor;
  for (size_t i = 0; i < colors->count; i++) {
    size_t idx = (start + i) % colors->count;
    const uint8_t *c = mhx_color_at(colors, idx);
    if (!mhx_map_get(M, c)) {
      *cursor = (idx + 1) % colors->count;
      *out = c;
      return 1;
    }
  }
  return 0;
}

static int mhx_extract_port(const char *bind, char *out_port, size_t out_len) {
  const char *last = strrchr(bind, ':');
  if (!last || !*(last + 1)) return -1;
  last++;
  size_t len = strlen(last);
  if (len == 0 || len + 1 > out_len) return -1;
  for (size_t i = 0; i < len; i++) {
    if (last[i] < '0' || last[i] > '9') return -1;
  }
  memcpy(out_port, last, len + 1);
  return 0;
}

static int mhx_build_worker_path(const char *argv0, char *out, size_t out_len) {
  /* Prefer the actual path of the running executable when available. */
#if defined(__linux__)
  {
    char exe[512];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (n > 0) {
      exe[n] = '\0';
      const char *slash = strrchr(exe, '/');
      if (slash) {
        size_t dir_len = (size_t)(slash - exe);
        if (dir_len + 1 + strlen("megahaxell-worker") + 1 <= out_len) {
          memcpy(out, exe, dir_len);
          out[dir_len] = '\0';
          strcat(out, "/megahaxell-worker");
          return 0;
        }
      }
    }
  }
#endif

  const char *slash = strrchr(argv0, '/');
  if (!slash) {
    /* Rely on PATH. */
    int n = snprintf(out, out_len, "megahaxell-worker");
    return (n > 0 && (size_t)n < out_len) ? 0 : -1;
  }
  size_t dir_len = (size_t)(slash - argv0);
  if (dir_len + 1 + strlen("megahaxell-worker") + 1 > out_len) return -1;
  memcpy(out, argv0, dir_len);
  out[dir_len] = '\0';
  strcat(out, "/megahaxell-worker");
  return 0;
}

int main(int argc, char **argv) {
  if (argc == 1) {
    mhx_usage(stderr, argv[0]);
    return 2;
  }

  int n = 0;
  int d = 0;
  double eps = 0.1;
  const char *bind = "tcp://*:9001";
  int local_workers = 0;
  int verbose = 0;
  const char *state_path = NULL;
  int save_interval_sec = 60;
  int resume_enabled = 1;

  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
      mhx_usage(stdout, argv[0]);
      return 0;
    } else if (strcmp(argv[i], "--n") == 0 && i + 1 < argc) {
      n = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--d") == 0 && i + 1 < argc) {
      d = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--eps") == 0 && i + 1 < argc) {
      eps = atof(argv[++i]);
    } else if (strcmp(argv[i], "--bind") == 0 && i + 1 < argc) {
      bind = argv[++i];
    } else if (strcmp(argv[i], "--local-workers") == 0 && i + 1 < argc) {
      local_workers = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--verbose") == 0) {
      verbose = 1;
    } else if (strcmp(argv[i], "--state") == 0 && i + 1 < argc) {
      state_path = argv[++i];
    } else if (strcmp(argv[i], "--save-interval") == 0 && i + 1 < argc) {
      save_interval_sec = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--no-resume") == 0) {
      resume_enabled = 0;
    } else {
      mhx_usage(stderr, argv[0]);
      return 2;
    }
  }

  if (n <= 0 || d <= 0) {
    mhx_usage(stderr, argv[0]);
    return 2;
  }
  if (save_interval_sec < 1) save_interval_sec = 1;

  char state_buf[256];
  if (!state_path) {
    snprintf(state_buf, sizeof(state_buf), "partial_pa_%d_%d.txt", n, d);
    state_path = state_buf;
  }

  signal(SIGINT, mhx_on_sigint);
  signal(SIGTERM, mhx_on_sigint);

  struct mhx_colors colors;
  if (mhx_make_colors(n, d, &colors) != 0) {
    fprintf(stderr, "failed to generate colors\n");
    return 1;
  }

  struct mhx_map M = mhx_map_create(n, d);
  if (resume_enabled && mhx_file_exists(state_path)) {
    if (mhx_load_state(state_path, n, d, &M) != 0) {
      fprintf(stderr, "head: failed to resume from %s\n", state_path);
      mhx_colors_free(&colors);
      mhx_map_destroy(&M);
      return 1;
    }
    fprintf(stderr, "head: resumed %zu/%zu from %s\n", M.len, colors.count, state_path);
  }

  void *ctx = zmq_ctx_new();
  if (!ctx) mhx_die_zmq("zmq_ctx_new");

  void *router = zmq_socket(ctx, ZMQ_ROUTER);
  if (!router) mhx_die_zmq("zmq_socket(ROUTER)");
  if (zmq_bind(router, bind) != 0) mhx_die_zmq("zmq_bind");

  fprintf(stderr, "head: n=%d d=%d eps=%.6g colors=%zu bind=%s\n", n, d, eps, colors.count, bind);

  /* Optional local workers: fork/exec the worker binary next to us. */
  if (local_workers > 0) {
    char port[16];
    if (mhx_extract_port(bind, port, sizeof(port)) != 0) {
      fprintf(stderr, "head: can't parse port from bind '%s'\n", bind);
      return 2;
    }
    char worker_path[512];
    if (mhx_build_worker_path(argv[0], worker_path, sizeof(worker_path)) != 0) {
      fprintf(stderr, "head: can't build worker path\n");
      return 2;
    }
    char hostport[64];
    snprintf(hostport, sizeof(hostport), "127.0.0.1:%s", port);

    if (verbose) {
      fprintf(stderr, "head: spawning local workers via %s (connect %s)\n", worker_path, hostport);
    }

    for (int i = 0; i < local_workers; i++) {
      pid_t p = fork();
      if (p == 0) {
        if (verbose) {
          execl(worker_path, worker_path, hostport, "--workers", "1", "--verbose", (char *)NULL);
        } else {
          execl(worker_path, worker_path, hostport, "--workers", "1", (char *)NULL);
        }
        _exit(127);
      } else if (p < 0) {
        fprintf(stderr, "head: fork failed: %s\n", strerror(errno));
        break;
      }
    }
  }

  unsigned long long next_job_id = 1;
  size_t color_cursor = 0;
  unsigned long long jobs_sent = 0;
  unsigned long long res_ok = 0;
  unsigned long long res_fail = 0;
  unsigned long long res_err = 0;
  unsigned long long merges = 0;
  time_t last_progress = time(NULL);
  time_t last_save = time(NULL);

  while (!g_stop) {
    void *ident = NULL;
    size_t ident_len = 0;
    if (mhx_recv_frame(router, &ident, &ident_len) != 0) mhx_die_zmq("zmq_recv(identity)");

    void *payload = NULL;
    size_t payload_len = 0;
    if (mhx_recv_frame(router, &payload, &payload_len) != 0) mhx_die_zmq("zmq_recv(payload)");
    char *msg = (char *)malloc(payload_len + 1);
    if (!msg) mhx_die_zmq("malloc");
    memcpy(msg, payload, payload_len);
    msg[payload_len] = '\0';
    free(payload);

    if (strncmp(msg, "READY", 5) == 0) {
      if (verbose) fprintf(stderr, "head: READY from worker\n");
      const uint8_t *A = NULL;
      if (!mhx_find_next_color(&colors, &M, &color_cursor, &A)) {
        mhx_send_text_to(router, ident, ident_len, "DONE\n");
        free(ident);
        free(msg);
        continue;
      }
      char *job = mhx_build_job(next_job_id++, n, d, eps, A, &M);
      if (!job) {
        mhx_send_text_to(router, ident, ident_len, "DONE\n");
      } else {
        mhx_send_text_to(router, ident, ident_len, job);
        jobs_sent++;
        if (verbose) fprintf(stderr, "head: sent JOB id=%llu M=%zu/%zu\n", (unsigned long long)(next_job_id - 1), M.len, colors.count);
        free(job);
      }
    } else if (strncmp(msg, "RES ", 4) == 0) {
      char *copy = strdup(msg);
      if (copy) {
        char *save = NULL;
        char *line = strtok_r(copy, "\n", &save);
        unsigned long long job_id = 0;
        char status[8];
        size_t diff_len = 0;
        if (line && mhx_parse_res_header(line, &job_id, status, &diff_len) == 0) {
          if (strcmp(status, "OK") == 0) {
            res_ok++;
          } else if (strcmp(status, "FAIL") == 0) {
            res_fail++;
          } else {
            res_err++;
          }

          if (verbose) fprintf(stderr, "head: got RES id=%llu status=%s diff_len=%zu\n", job_id, status, diff_len);

          if (strcmp(status, "OK") == 0 && diff_len > 0) {
            struct mhx_map diff = mhx_map_create(n, d);
            size_t parsed = 0;
            for (size_t i = 0; i < diff_len; i++) {
              line = strtok_r(NULL, "\n", &save);
              if (!line) break;
              char *bar = strchr(line, '|');
              if (!bar) break;
              *bar = '\0';
              const char *cstr = line;
              const char *pstr = bar + 1;
              uint8_t *cc = (uint8_t *)malloc((size_t)n);
              uint16_t *pp = (uint16_t *)malloc((size_t)n * sizeof(uint16_t));
              if (!cc || !pp) {
                free(cc);
                free(pp);
                break;
              }
              if (mhx_parse_u8_list(cstr, n, cc) != 0 || mhx_parse_u16_list(pstr, n, pp) != 0) {
                free(cc);
                free(pp);
                break;
              }
              struct mhx_perm pv = {.n = n, .v = pp};
              if (mhx_map_set(&diff, cc, &pv) != 0) {
                free(cc);
                free(pp);
                break;
              }
              free(cc);
              free(pp);
              parsed++;
            }

            if (parsed != diff_len) {
              if (verbose) {
                size_t nl = 0;
                for (size_t i = 0; i < payload_len; i++) {
                  if (msg[i] == '\n') nl++;
                }
                fprintf(stderr,
                        "head: diff parse failed job=%llu expected=%zu got=%zu raw_len=%zu newlines=%zu\n",
                        job_id,
                        diff_len,
                        parsed,
                        payload_len,
                        nl);
                fprintf(stderr, "head: raw RES snippet: %.300s\n", msg);
                fprintf(stderr, "head: hint: this often means you are running an older megahaxell-worker binary.\n");
              }
            } else {
              size_t before = M.len;
              int merged = mhx_merge_diff(n, d, &M, &diff);
              if (merged) {
                merges++;
                if (M.len > before) {
                  fprintf(stderr, "head: merged job=%llu M=%zu/%zu\n", job_id, M.len, colors.count);
                } else if (verbose) {
                  fprintf(stderr, "head: applied diff for job=%llu (no new colors) M=%zu/%zu\n", job_id, M.len, colors.count);
                }
              } else if (verbose) {
                fprintf(stderr, "head: rejected diff for job=%llu (merged=%d before=%zu after=%zu)\n", job_id, merged, before, M.len);
              }
            }
            mhx_map_destroy(&diff);
          }
        }
        free(copy);
      }
    } else {
      mhx_send_text_to(router, ident, ident_len, "READY?\n");
    }

    free(ident);
    free(msg);

    time_t now = time(NULL);
    if (now != (time_t)-1 && now - last_progress >= 5) {
      fprintf(stderr,
              "head: progress M=%zu/%zu jobs_sent=%llu res_ok=%llu res_fail=%llu res_err=%llu merges=%llu\n",
              M.len,
              colors.count,
              jobs_sent,
              res_ok,
              res_fail,
              res_err,
              merges);
      last_progress = now;
    }

    if (now != (time_t)-1 && now - last_save >= save_interval_sec) {
      if (mhx_save_state_atomic(state_path, &M) == 0) {
        if (verbose) fprintf(stderr, "head: saved %zu/%zu to %s\n", M.len, colors.count, state_path);
      } else {
        fprintf(stderr, "head: failed to save to %s: %s\n", state_path, strerror(errno));
      }
      last_save = now;
    }

    if (M.len == colors.count) {
      fprintf(stderr, "head: done\n");
      break;
    }
  }

  zmq_close(router);
  zmq_ctx_term(ctx);

  /* Final save on exit. */
  if (mhx_save_state_atomic(state_path, &M) == 0) {
    fprintf(stderr, "head: saved final %zu/%zu to %s\n", M.len, colors.count, state_path);
  }

  mhx_map_destroy(&M);
  mhx_colors_free(&colors);

  while (waitpid(-1, NULL, WNOHANG) > 0) {}
  return 0;
}
