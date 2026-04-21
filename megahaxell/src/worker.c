#define _POSIX_C_SOURCE 200809L

#include <zmq.h>

#include "megahaxell/math/haxell.h"

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;

static void mhx_on_sigint(int sig) {
  (void)sig;
  g_stop = 1;
}

static void mhx_usage(FILE *f, const char *argv0) {
  fprintf(f, "usage:\n");
  fprintf(f, "  %s <head_host[:port]> [--workers N] [--verbose]\n", argv0);
  fprintf(f, "\n");
  fprintf(f, "notes:\n");
  fprintf(f, "  If :port is omitted, defaults to 9001.\n");
}

static void mhx_die_zmq(const char *what) {
  int e = zmq_errno();
  fprintf(stderr, "%s: %s (%d)\n", what, zmq_strerror(e), e);
  exit(1);
}

static int mhx_recv_text(void *sock, char **out) {
  *out = NULL;
  zmq_msg_t msg;
  if (zmq_msg_init(&msg) != 0) return -1;
  int n = zmq_msg_recv(&msg, sock, 0);
  if (n < 0) {
    zmq_msg_close(&msg);
    return -1;
  }
  size_t sz = zmq_msg_size(&msg);
  char *buf = (char *)malloc(sz + 1);
  if (!buf) {
    zmq_msg_close(&msg);
    return -1;
  }
  memcpy(buf, zmq_msg_data(&msg), sz);
  buf[sz] = '\0';
  zmq_msg_close(&msg);
  *out = buf;
  return (int)sz;
}

static int mhx_send_text(void *sock, const char *s) {
  size_t len = strlen(s);
  int rc = zmq_send(sock, s, len, 0);
  return rc < 0 ? -1 : 0;
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
        /* allow either comma or space separated */
        return -1;
      }
    }
  }
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

static int mhx_worker_handle_job(
    void *sock,
    const char *msg,
    struct mhx_haxell **cached_h,
    int *cached_n,
    int *cached_d,
    double *cached_eps,
    int verbose) {
  /* Parse message into lines. */
  char *copy = strdup(msg);
  if (!copy) return -1;

  char *save = NULL;
  char *line = strtok_r(copy, "\n", &save);
  if (!line) {
    free(copy);
    return -1;
  }

  unsigned long long job_id = 0;
  int n = 0;
  int d = 0;
  double eps = 0.1;
  char acolor_str[4096];
  acolor_str[0] = '\0';

  if (sscanf(line, "JOB %llu %d %d %lf %4095s", &job_id, &n, &d, &eps, acolor_str) != 5) {
    free(copy);
    return -1;
  }

  if (n <= 0 || d <= 0) {
    free(copy);
    return -1;
  }

  uint8_t *A = (uint8_t *)malloc((size_t)n);
  if (!A) {
    free(copy);
    return -1;
  }
  if (mhx_parse_u8_list(acolor_str, n, A) != 0) {
    free(A);
    free(copy);
    return -1;
  }

  line = strtok_r(NULL, "\n", &save);
  size_t mcount = 0;
  if (!line || sscanf(line, "M %zu", &mcount) != 1) {
    free(A);
    free(copy);
    return -1;
  }

  struct mhx_map M = mhx_map_create(n, d);
  for (size_t i = 0; i < mcount; i++) {
    line = strtok_r(NULL, "\n", &save);
    if (!line) {
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }
    char *bar = strchr(line, '|');
    if (!bar) {
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }
    *bar = '\0';
    const char *cstr = line;
    const char *pstr = bar + 1;

    uint8_t *color = (uint8_t *)malloc((size_t)n);
    uint16_t *perm = (uint16_t *)malloc((size_t)n * sizeof(uint16_t));
    if (!color || !perm) {
      free(color);
      free(perm);
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }
    if (mhx_parse_u8_list(cstr, n, color) != 0 || mhx_parse_u16_list(pstr, n, perm) != 0) {
      free(color);
      free(perm);
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }

    struct mhx_perm p = {.n = n, .v = perm};
    int rc = mhx_map_set(&M, color, &p);
    free(color);
    free(perm);
    if (rc != 0) {
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }
  }

  /* Ensure cached haxell instance matches. */
  if (!*cached_h || *cached_n != n || *cached_d != d || *cached_eps != eps) {
    if (*cached_h) mhx_haxell_destroy(*cached_h);
    *cached_h = mhx_haxell_create(n, d, eps);
    *cached_n = n;
    *cached_d = d;
    *cached_eps = eps;
    if (!*cached_h) {
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }
  }

  struct mhx_map diff = mhx_map_create(n, d);
  int grow_rc = mhx_grow_transversal(*cached_h, &M, A, &diff);
  free(A);

  if (verbose) {
    fprintf(stderr, "worker[%ld]: job=%llu grow_rc=%d diff_len=%zu\n", (long)getpid(), job_id, grow_rc, diff.len);
  }

  /* Serialize response (robust: count lines actually serialized). */
  const char *status = (grow_rc == 1) ? "OK" : (grow_rc == 0 ? "FAIL" : "ERR");

  char *body = NULL;
  size_t body_cap = 0;
  size_t body_off = 0;
  size_t written = 0;

  if (grow_rc == 1) {
    for (size_t i = 0; i < diff.len; i++) {
      char cbuf[4096];
      char pbuf[8192];
      if (mhx_color_to_string(diff.e[i].color, n, cbuf, sizeof(cbuf)) != 0) {
        if (verbose) fprintf(stderr, "worker[%ld]: serialize skip color\n", (long)getpid());
        continue;
      }
      if (mhx_perm_to_string(diff.e[i].perm.v, n, pbuf, sizeof(pbuf)) != 0) {
        if (verbose) fprintf(stderr, "worker[%ld]: serialize skip perm\n", (long)getpid());
        continue;
      }

      char linebuf[12288];
      int lw = snprintf(linebuf, sizeof(linebuf), "%s|%s\n", cbuf, pbuf);
      if (lw <= 0) continue;

      size_t need = body_off + (size_t)lw + 1;
      if (need > body_cap) {
        size_t new_cap = body_cap ? body_cap * 2 : 2048;
        while (new_cap < need) new_cap *= 2;
        char *p = (char *)realloc(body, new_cap);
        if (!p) {
          free(body);
          mhx_map_destroy(&diff);
          mhx_map_destroy(&M);
          free(copy);
          return -1;
        }
        body = p;
        body_cap = new_cap;
      }
      memcpy(body + body_off, linebuf, (size_t)lw);
      body_off += (size_t)lw;
      body[body_off] = '\0';
      written++;
    }
  }

  char header[128];
  int hw = snprintf(header, sizeof(header), "RES %llu %s %zu\n", job_id, status, written);
  if (hw <= 0) hw = 0;

  size_t out_len = (size_t)hw + body_off + strlen("END\n");
  char *out = (char *)malloc(out_len + 1);
  if (!out) {
    free(body);
    mhx_map_destroy(&diff);
    mhx_map_destroy(&M);
    free(copy);
    return -1;
  }
  size_t o = 0;
  memcpy(out + o, header, (size_t)hw);
  o += (size_t)hw;
  if (body_off) {
    memcpy(out + o, body, body_off);
    o += body_off;
  }
  memcpy(out + o, "END\n", 4);
  o += 4;
  out[o] = '\0';
  free(body);

  if (mhx_send_text(sock, out) != 0) mhx_die_zmq("zmq_send(RES)");
  free(out);

  mhx_map_destroy(&diff);
  mhx_map_destroy(&M);
  free(copy);
  return 0;
}

static void mhx_worker_loop(const char *head, int idx, int verbose) {
  void *ctx = zmq_ctx_new();
  if (!ctx) mhx_die_zmq("zmq_ctx_new");

  void *sock = zmq_socket(ctx, ZMQ_DEALER);
  if (!sock) mhx_die_zmq("zmq_socket(DEALER)");

  if (zmq_connect(sock, head) != 0) mhx_die_zmq("zmq_connect");

  char hello[128];
  snprintf(hello, sizeof(hello), "READY pid=%ld idx=%d\n", (long)getpid(), idx);
  if (mhx_send_text(sock, hello) != 0) mhx_die_zmq("zmq_send(READY)");

  struct mhx_haxell *cached_h = NULL;
  int cached_n = 0;
  int cached_d = 0;
  double cached_eps = 0.0;
  while (!g_stop) {
    char *msg = NULL;
    int n = mhx_recv_text(sock, &msg);
    if (n < 0) mhx_die_zmq("zmq_recv");
    if (!msg) continue;

    if (strncmp(msg, "JOB ", 4) == 0) {
      if (mhx_worker_handle_job(sock, msg, &cached_h, &cached_n, &cached_d, &cached_eps, verbose) != 0) {
        /* tell head we failed and keep going */
        mhx_send_text(sock, "RES 0 ERR 0\nEND\n");
      }
      mhx_send_text(sock, "READY\n");
    } else if (strncmp(msg, "DONE", 4) == 0) {
      free(msg);
      break;
    }

    free(msg);
  }

  if (cached_h) mhx_haxell_destroy(cached_h);
  zmq_close(sock);
  zmq_ctx_term(ctx);
  _exit(0);
}

int main(int argc, char **argv) {
  if (argc == 1) {
    mhx_usage(stderr, argv[0]);
    return 2;
  }

  const char *head_hostport = NULL;
  int workers = 1;
  int verbose = 0;

  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
      mhx_usage(stdout, argv[0]);
      return 0;
    } else if (strcmp(argv[i], "--workers") == 0 && i + 1 < argc) {
      workers = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--verbose") == 0) {
      verbose = 1;
    } else if (argv[i][0] == '-') {
      mhx_usage(stderr, argv[0]);
      return 2;
    } else {
      if (head_hostport) {
        mhx_usage(stderr, argv[0]);
        return 2;
      }
      head_hostport = argv[i];
    }
  }

  if (workers <= 0) workers = 1;
  if (!head_hostport || head_hostport[0] == '\0') {
    mhx_usage(stderr, argv[0]);
    return 2;
  }

  const char *host = head_hostport;
  const char *port = "9001";

  char host_buf[128];
  const char *last_colon = strrchr(head_hostport, ':');
  if (last_colon && last_colon != head_hostport && *(last_colon + 1) != '\0') {
    int all_digits = 1;
    for (const char *p = last_colon + 1; *p; p++) {
      if (*p < '0' || *p > '9') {
        all_digits = 0;
        break;
      }
    }
    if (all_digits) {
      size_t host_len = (size_t)(last_colon - head_hostport);
      if (host_len == 0 || host_len >= sizeof(host_buf)) {
        fprintf(stderr, "bad head host\n");
        return 2;
      }
      memcpy(host_buf, head_hostport, host_len);
      host_buf[host_len] = '\0';
      host = host_buf;
      port = last_colon + 1;
    }
  }

  signal(SIGINT, mhx_on_sigint);
  signal(SIGTERM, mhx_on_sigint);

  char endpoint[256];
  int n = snprintf(endpoint, sizeof(endpoint), "tcp://%s:%s", host, port);
  if (n <= 0 || (size_t)n >= sizeof(endpoint)) {
    fprintf(stderr, "head address too long\n");
    return 2;
  }

  fprintf(stderr, "worker: head=%s workers=%d\n", endpoint, workers);

  for (int i = 0; i < workers; i++) {
    pid_t p = fork();
    if (p == 0) {
      mhx_worker_loop(endpoint, i, verbose);
    } else if (p < 0) {
      fprintf(stderr, "fork failed: %s\n", strerror(errno));
      g_stop = 1;
      break;
    }
  }

  while (!g_stop) sleep(1);

  while (waitpid(-1, NULL, WNOHANG) > 0) {}

  return 0;
}
