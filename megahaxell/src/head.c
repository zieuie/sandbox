#define _POSIX_C_SOURCE 200809L

#include <zmq.h>

#include "megahaxell/math/colors.h"
#include "megahaxell/math/haxell.h"

#include <ctype.h>
#include <errno.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;

int mhx_worker_main(int argc, char **argv);

enum {
  MHX_WORKER_LEASE_SEC = 15,
  MHX_POLL_MS = 1000,
};

struct mhx_ident {
  size_t len;
  unsigned char *b;
};

struct mhx_host_state {
  struct mhx_ident ident;
  time_t last_seen;
  size_t capacity;
  char addr[64];
};

struct mhx_host_set {
  size_t len;
  size_t cap;
  struct mhx_host_state *items;
};

struct mhx_inflight_job {
  unsigned long long job_id;
  uint8_t *color;
  struct mhx_ident host;
  time_t lease_deadline;
};

struct mhx_inflight_set {
  size_t len;
  size_t cap;
  struct mhx_inflight_job *items;
};

struct mhx_reported_job {
  unsigned long long job_id;
  uint8_t *color;
};

struct mhx_reported_jobs {
  size_t len;
  struct mhx_reported_job *items;
};

static void mhx_on_sigint(int sig) {
  (void)sig;
  g_stop = 1;
}

static void mhx_arm_parent_deathsig(pid_t expected_parent) {
  if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) _exit(126);
  if (getppid() != expected_parent) _exit(0);
}

static void mhx_usage(FILE *f, const char *argv0) {
  fprintf(f, "usage:\n");
  fprintf(f,
          "  %s --n N --d D [--eps 0.1] [--port PORT] [--workers N] [--verbose]\n",
          argv0);
  fprintf(f, "    [--state FILE] [--save-interval SECONDS] [--no-resume]\n");
}

static void mhx_print_time_prefix(FILE *f) {
  time_t now = time(NULL);
  struct tm tm;
  if (now == (time_t)-1 || !localtime_r(&now, &tm)) {
    fputs("[time?] ", f);
    return;
  }
  char buf[64];
  if (strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tm) == 0) {
    fputs("[time?] ", f);
    return;
  }
  fprintf(f, "[%s] ", buf);
}

static void mhx_log(int verbose, FILE *f, const char *fmt, ...) {
  if (!verbose) mhx_print_time_prefix(f);
  va_list ap;
  va_start(ap, fmt);
  vfprintf(f, fmt, ap);
  va_end(ap);
}

static void mhx_die_zmq(const char *what) {
  int e = zmq_errno();
  fprintf(stderr, "%s: %s (%d)\n", what, zmq_strerror(e), e);
  exit(1);
}

static void mhx_ident_destroy(struct mhx_ident *ident) {
  if (!ident) return;
  free(ident->b);
  ident->b = NULL;
  ident->len = 0;
}

static int mhx_ident_copy(struct mhx_ident *dst, const void *b, size_t n) {
  unsigned char *copy = (unsigned char *)malloc(n);
  if (!copy) return -1;
  memcpy(copy, b, n);
  dst->b = copy;
  dst->len = n;
  return 0;
}

static int mhx_ident_eq(const struct mhx_ident *ident, const void *b, size_t n) {
  return ident->len == n && memcmp(ident->b, b, n) == 0;
}

static void mhx_host_set_destroy(struct mhx_host_set *s) {
  if (!s) return;
  for (size_t i = 0; i < s->len; i++) mhx_ident_destroy(&s->items[i].ident);
  free(s->items);
  memset(s, 0, sizeof(*s));
}

static struct mhx_host_state *mhx_host_set_upsert(struct mhx_host_set *s, const void *b, size_t n) {
  for (size_t i = 0; i < s->len; i++) {
    if (mhx_ident_eq(&s->items[i].ident, b, n)) return &s->items[i];
  }
  if (s->len == s->cap) {
    size_t new_cap = s->cap ? s->cap * 2 : 8;
    void *p = realloc(s->items, new_cap * sizeof(*s->items));
    if (!p) return NULL;
    s->items = (struct mhx_host_state *)p;
    s->cap = new_cap;
  }
  struct mhx_host_state *host = &s->items[s->len];
  memset(host, 0, sizeof(*host));
  if (mhx_ident_copy(&host->ident, b, n) != 0) return NULL;
  s->len++;
  return host;
}

static size_t mhx_host_set_live_hosts(const struct mhx_host_set *s, time_t now) {
  size_t count = 0;
  for (size_t i = 0; i < s->len; i++) {
    if (s->items[i].last_seen != 0 && now - s->items[i].last_seen <= MHX_WORKER_LEASE_SEC) count++;
  }
  return count;
}

static size_t mhx_host_set_live_capacity(const struct mhx_host_set *s, time_t now) {
  size_t total = 0;
  for (size_t i = 0; i < s->len; i++) {
    if (s->items[i].last_seen != 0 && now - s->items[i].last_seen <= MHX_WORKER_LEASE_SEC) total += s->items[i].capacity;
  }
  return total;
}

static void mhx_inflight_set_destroy(struct mhx_inflight_set *s) {
  if (!s) return;
  for (size_t i = 0; i < s->len; i++) {
    free(s->items[i].color);
    mhx_ident_destroy(&s->items[i].host);
  }
  free(s->items);
  memset(s, 0, sizeof(*s));
}

static ssize_t mhx_inflight_find_by_color(const struct mhx_inflight_set *s, const uint8_t *color, int n) {
  for (size_t i = 0; i < s->len; i++) {
    if (memcmp(s->items[i].color, color, (size_t)n) == 0) return (ssize_t)i;
  }
  return -1;
}

static ssize_t mhx_inflight_find_by_job_id(const struct mhx_inflight_set *s, unsigned long long job_id) {
  for (size_t i = 0; i < s->len; i++) {
    if (s->items[i].job_id == job_id) return (ssize_t)i;
  }
  return -1;
}

static void mhx_inflight_remove_idx(struct mhx_inflight_set *s, size_t idx) {
  if (idx >= s->len) return;
  free(s->items[idx].color);
  mhx_ident_destroy(&s->items[idx].host);
  if (idx + 1 < s->len) {
    memmove(&s->items[idx], &s->items[idx + 1], (s->len - idx - 1) * sizeof(*s->items));
  }
  s->len--;
}

static int mhx_inflight_assign(
    struct mhx_inflight_set *s,
    unsigned long long job_id,
    const uint8_t *color,
    int n,
    const void *host_ident,
    size_t host_ident_len,
    time_t deadline) {
  ssize_t idx = mhx_inflight_find_by_job_id(s, job_id);
  if (idx < 0) idx = mhx_inflight_find_by_color(s, color, n);

  struct mhx_inflight_job *job = NULL;
  if (idx >= 0) {
    job = &s->items[idx];
    free(job->color);
    mhx_ident_destroy(&job->host);
  } else {
    if (s->len == s->cap) {
      size_t new_cap = s->cap ? s->cap * 2 : 8;
      void *p = realloc(s->items, new_cap * sizeof(*s->items));
      if (!p) return -1;
      s->items = (struct mhx_inflight_job *)p;
      s->cap = new_cap;
    }
    job = &s->items[s->len++];
    memset(job, 0, sizeof(*job));
  }

  job->color = (uint8_t *)malloc((size_t)n);
  if (!job->color) return -1;
  memcpy(job->color, color, (size_t)n);
  if (mhx_ident_copy(&job->host, host_ident, host_ident_len) != 0) {
    free(job->color);
    job->color = NULL;
    return -1;
  }
  job->job_id = job_id;
  job->lease_deadline = deadline;
  return 0;
}

static int mhx_reported_contains(const struct mhx_reported_jobs *reported, unsigned long long job_id, const uint8_t *color, int n) {
  for (size_t i = 0; i < reported->len; i++) {
    if (reported->items[i].job_id == job_id && memcmp(reported->items[i].color, color, (size_t)n) == 0) return 1;
  }
  return 0;
}

static void mhx_reported_jobs_destroy(struct mhx_reported_jobs *reported) {
  if (!reported) return;
  for (size_t i = 0; i < reported->len; i++) free(reported->items[i].color);
  free(reported->items);
  memset(reported, 0, sizeof(*reported));
}

static void mhx_reconcile_host_jobs(
    struct mhx_inflight_set *inflight,
    const void *ident,
    size_t ident_len,
    const struct mhx_reported_jobs *reported,
    int n,
    time_t deadline) {
  size_t i = 0;
  while (i < inflight->len) {
    struct mhx_inflight_job *job = &inflight->items[i];
    if (!mhx_ident_eq(&job->host, ident, ident_len)) {
      i++;
      continue;
    }
    if (mhx_reported_contains(reported, job->job_id, job->color, n)) {
      job->lease_deadline = deadline;
      i++;
      continue;
    }
    mhx_inflight_remove_idx(inflight, i);
  }

  for (size_t j = 0; j < reported->len; j++) {
    (void)mhx_inflight_assign(
        inflight,
        reported->items[j].job_id,
        reported->items[j].color,
        n,
        ident,
        ident_len,
        deadline);
  }
}

static void mhx_inflight_expire(struct mhx_inflight_set *s, time_t now, int verbose) {
  size_t i = 0;
  while (i < s->len) {
    if (s->items[i].lease_deadline > now) {
      i++;
      continue;
    }
    mhx_log(verbose, stderr, "head: lease expired for job=%llu\n", s->items[i].job_id);
    mhx_inflight_remove_idx(s, i);
  }
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
    int w = snprintf(out + off, out_len - off, "%s%u", i ? "," : "", (unsigned)c[i]);
    if (w <= 0) return -1;
    off += (size_t)w;
    if (off >= out_len) return -1;
  }
  return 0;
}

static int mhx_perm_to_string(const uint16_t *p, int n, char *out, size_t out_len) {
  size_t off = 0;
  for (int i = 0; i < n; i++) {
    int w = snprintf(out + off, out_len - off, "%s%u", i ? " " : "", (unsigned)p[i]);
    if (w <= 0) return -1;
    off += (size_t)w;
    if (off >= out_len) return -1;
  }
  return 0;
}

static int mhx_parse_u8_list(const char *s, int n, uint8_t *out) {
  const char *p = s;
  for (int i = 0; i < n; i++) {
    while (*p == ' ' || *p == '\t') p++;
    char *end = NULL;
    long v = strtol(p, &end, 10);
    if (!end || end == p || v < 0 || v > 255) return -1;
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
    if (!end || end == p || v < 0 || v > 65535) return -1;
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
      } else if (!saw_ws) {
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
    if (!end || end == p || v < 0 || v > 65535) return -1;
    out_perm[i] = (uint16_t)v;
    p = end;
  }
  return 0;
}

static void mhx_perm_to_color(int n, int d, const uint16_t *perm, uint8_t *out_color) {
  for (int i = 0; i < n; i++) out_color[i] = (uint8_t)(perm[i] / (uint16_t)d);
}

static int mhx_parse_res_header(
    const char *line,
    unsigned long long *job_id,
    char status[8],
    size_t *diff_len,
    char *color,
    size_t color_len) {
  status[0] = '\0';
  color[0] = '\0';
  if (sscanf(line, "RES %llu %7s %zu %4095s", job_id, status, diff_len, color) != 4) return -1;
  if (color_len > 0) color[color_len - 1] = '\0';
  return 0;
}

static int mhx_parse_status_header(
    const char *line,
    char verb[8],
    size_t *capacity,
    size_t *active_count,
    char *addr,
    size_t addr_len) {
  verb[0] = '\0';
  if (addr_len > 0) addr[0] = '\0';
  if (sscanf(line, "%7s %zu %zu %63s", verb, capacity, active_count, addr) == 4) {
    if (addr_len > 0) addr[addr_len - 1] = '\0';
    return 0;
  }
  if (sscanf(line, "%7s %zu %zu", verb, capacity, active_count) == 3) {
    if (addr_len > 0) snprintf(addr, addr_len, "?");
    return 0;
  }
  return -1;
}

static void mhx_log_host_roster(const struct mhx_host_set *hosts, time_t now) {
  for (size_t i = 0; i < hosts->len; i++) {
    const struct mhx_host_state *host = &hosts->items[i];
    if (host->last_seen == 0 || now - host->last_seen > MHX_WORKER_LEASE_SEC) continue;
    fprintf(stderr, "head: host %s cores=%zu\n", host->addr[0] ? host->addr : "?", host->capacity);
  }
}

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
    char *hash = strchr(line, '#');
    if (hash) *hash = '\0';
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
  return 0;
}

static int mhx_save_state_atomic(const char *path, const struct mhx_map *M) {
  char tmp[1024];
  int n = snprintf(tmp, sizeof(tmp), "%s.tmp", path);
  if (n <= 0 || (size_t)n >= sizeof(tmp)) return -1;

  FILE *f = fopen(tmp, "w");
  if (!f) return -1;
  for (size_t i = 0; i < M->len; i++) {
    for (int j = 0; j < M->n; j++) fprintf(f, "%s%u", j ? " " : "", (unsigned)M->e[i].perm.v[j]);
    fputc('\n', f);
  }
  if (fclose(f) != 0) return -1;
  if (rename(tmp, path) != 0) return -1;
  return 0;
}

static int mhx_merge_diff(int n, int d, struct mhx_map *M, const struct mhx_map *diff) {
  if (diff->len == 0) return 0;
  for (size_t i = 0; i < diff->len; i++) {
    for (size_t j = 0; j < M->len; j++) {
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

static int mhx_color_is_available(
    const uint8_t *color,
    const struct mhx_map *M,
    const struct mhx_inflight_set *inflight,
    int n) {
  return mhx_map_get(M, color) == NULL && mhx_inflight_find_by_color(inflight, color, n) < 0;
}

static int mhx_find_next_color(
    const struct mhx_colors *colors,
    const struct mhx_map *M,
    const struct mhx_inflight_set *inflight,
    size_t *cursor,
    const uint8_t **out) {
  size_t start = *cursor;
  for (size_t i = 0; i < colors->count; i++) {
    size_t idx = (start + i) % colors->count;
    const uint8_t *c = mhx_color_at(colors, idx);
    if (mhx_color_is_available(c, M, inflight, colors->n)) {
      *cursor = (idx + 1) % colors->count;
      *out = c;
      return 1;
    }
  }
  return 0;
}

static void mhx_dispatch_to_host(
    void *router,
    const void *ident,
    size_t ident_len,
    size_t capacity,
    size_t active_count,
    const struct mhx_colors *colors,
    const struct mhx_map *M,
    struct mhx_inflight_set *inflight,
    size_t *color_cursor,
    unsigned long long *next_job_id,
    int n,
    int d,
    double eps,
    time_t now,
    unsigned long long *jobs_sent,
    int verbose) {
  if (active_count >= capacity) {
    (void)mhx_send_text_to(router, ident, ident_len, "WAIT\n");
    return;
  }
  if (M->len == colors->count && inflight->len == 0) {
    (void)mhx_send_text_to(router, ident, ident_len, "DONE\n");
    return;
  }

  const uint8_t *A = NULL;
  if (!mhx_find_next_color(colors, M, inflight, color_cursor, &A)) {
    (void)mhx_send_text_to(router, ident, ident_len, "WAIT\n");
    return;
  }

  unsigned long long job_id = (*next_job_id)++;
  char *job = mhx_build_job(job_id, n, d, eps, A, M);
  if (!job) {
    (void)mhx_send_text_to(router, ident, ident_len, "WAIT\n");
    return;
  }

  if (mhx_inflight_assign(inflight, job_id, A, n, ident, ident_len, now + MHX_WORKER_LEASE_SEC) != 0) {
    free(job);
    (void)mhx_send_text_to(router, ident, ident_len, "WAIT\n");
    return;
  }

  if (mhx_send_text_to(router, ident, ident_len, job) != 0) {
    ssize_t idx = mhx_inflight_find_by_job_id(inflight, job_id);
    if (idx >= 0) mhx_inflight_remove_idx(inflight, (size_t)idx);
    free(job);
    return;
  }

  (*jobs_sent)++;
  if (verbose) fprintf(stderr, "head: sent JOB id=%llu M=%zu/%zu\n", job_id, M->len, colors->count);
  free(job);
}

int mhx_head_main(int argc, char **argv) {
  if (argc == 1) {
    mhx_usage(stderr, argv[0]);
    return 2;
  }

  int n = 0;
  int d = 0;
  double eps = 0.1;
  int port = 9001;
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
    } else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
      port = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--workers") == 0 && i + 1 < argc) {
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

  if (n <= 0 || d <= 0 || port <= 0 || port > 65535) {
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
      mhx_log(verbose, stderr, "head: failed to resume from %s\n", state_path);
      mhx_colors_free(&colors);
      mhx_map_destroy(&M);
      return 1;
    }
    mhx_log(verbose, stderr, "head: resumed %zu/%zu from %s\n", M.len, colors.count, state_path);
  }

  void *ctx = zmq_ctx_new();
  if (!ctx) mhx_die_zmq("zmq_ctx_new");

  void *router = zmq_socket(ctx, ZMQ_ROUTER);
  if (!router) mhx_die_zmq("zmq_socket(ROUTER)");
  char bind[64];
  snprintf(bind, sizeof(bind), "tcp://0.0.0.0:%d", port);
  if (zmq_bind(router, bind) != 0) mhx_die_zmq("zmq_bind");

  mhx_log(verbose,
          stderr,
          "head: n=%d d=%d eps=%.6g colors=%zu port=%d lease=%ds\n",
          n,
          d,
          eps,
          colors.count,
          port,
          MHX_WORKER_LEASE_SEC);

  pid_t local_worker_pid = -1;
  if (local_workers > 0) {
    char hostport[64];
    char workers_buf[32];
    snprintf(hostport, sizeof(hostport), "127.0.0.1:%d", port);
    snprintf(workers_buf, sizeof(workers_buf), "%d", local_workers);
    if (verbose) {
      fprintf(stderr,
              "head: spawning local worker host via %s --worker %s --workers %s\n",
              argv[0],
              hostport,
              workers_buf);
    }
    pid_t p = fork();
    if (p == 0) {
      pid_t parent_pid = getppid();
      mhx_arm_parent_deathsig(parent_pid);
      if (verbose) {
        char *worker_argv[] = {argv[0], "--worker", hostport, "--workers", workers_buf, "--verbose", NULL};
        _exit(mhx_worker_main(6, worker_argv));
      } else {
        char *worker_argv[] = {argv[0], "--worker", hostport, "--workers", workers_buf, NULL};
        _exit(mhx_worker_main(5, worker_argv));
      }
    }
    if (p > 0) local_worker_pid = p;
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
  struct mhx_host_set hosts;
  struct mhx_inflight_set inflight;
  memset(&hosts, 0, sizeof(hosts));
  memset(&inflight, 0, sizeof(inflight));

  while (!g_stop) {
    time_t now = time(NULL);
    if (now == (time_t)-1) now = 0;
    mhx_inflight_expire(&inflight, now, verbose);

    zmq_pollitem_t items[] = {{.socket = router, .fd = 0, .events = ZMQ_POLLIN, .revents = 0}};
    int prc = zmq_poll(items, 1, MHX_POLL_MS);
    if (prc < 0) {
      if (zmq_errno() == EINTR) continue;
      mhx_die_zmq("zmq_poll");
    }

    if (items[0].revents & ZMQ_POLLIN) {
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

      now = time(NULL);
      if (now == (time_t)-1) now = 0;

      if (strncmp(msg, "RES ", 4) == 0) {
        char *copy = strdup(msg);
        if (copy) {
          char *save = NULL;
          char *line = strtok_r(copy, "\n", &save);
          unsigned long long job_id = 0;
          char status[8];
          size_t diff_len = 0;
          char color_str[4096];
          if (line && mhx_parse_res_header(line, &job_id, status, &diff_len, color_str, sizeof(color_str)) == 0) {
            uint8_t *target_color = (uint8_t *)malloc((size_t)n);
            int have_target = target_color && mhx_parse_u8_list(color_str, n, target_color) == 0;
            if (!have_target) {
              free(target_color);
              target_color = NULL;
            }

            if (strcmp(status, "OK") == 0) res_ok++;
            else if (strcmp(status, "FAIL") == 0) res_fail++;
            else res_err++;

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

              if (parsed == diff_len) {
                size_t before = M.len;
                if (mhx_merge_diff(n, d, &M, &diff)) {
                  merges++;
                  if (M.len > before) mhx_log(verbose, stderr, "head: merged job=%llu M=%zu/%zu\n", job_id, M.len, colors.count);
                } else if (verbose) {
                  fprintf(stderr, "head: rejected diff for job=%llu\n", job_id);
                }
              }
              mhx_map_destroy(&diff);
            }

            ssize_t idx = mhx_inflight_find_by_job_id(&inflight, job_id);
            if (idx >= 0) {
              mhx_inflight_remove_idx(&inflight, (size_t)idx);
            } else if (have_target && mhx_map_get(&M, target_color) != NULL) {
              idx = mhx_inflight_find_by_color(&inflight, target_color, n);
              if (idx >= 0) mhx_inflight_remove_idx(&inflight, (size_t)idx);
            }
            free(target_color);
          }
          free(copy);
        }
      } else {
        char *copy = strdup(msg);
        if (copy) {
          char *save = NULL;
          char *line = strtok_r(copy, "\n", &save);
          char verb[8];
          size_t capacity = 0;
          size_t active_count = 0;
          char addr[64];
          if (line && mhx_parse_status_header(line, verb, &capacity, &active_count, addr, sizeof(addr)) == 0) {
            struct mhx_reported_jobs reported;
            memset(&reported, 0, sizeof(reported));
            unsigned long long max_reported = 0;

            if (active_count > 0) {
              reported.items = (struct mhx_reported_job *)calloc(active_count, sizeof(*reported.items));
              if (reported.items) {
                for (size_t i = 0; i < active_count; i++) {
                  line = strtok_r(NULL, "\n", &save);
                  if (!line) break;
                  unsigned long long job_id = 0;
                  char color_str[4096];
                  color_str[0] = '\0';
                  if (sscanf(line, "%llu %4095s", &job_id, color_str) != 2) break;
                  reported.items[reported.len].color = (uint8_t *)malloc((size_t)n);
                  if (!reported.items[reported.len].color) break;
                  if (mhx_parse_u8_list(color_str, n, reported.items[reported.len].color) != 0) break;
                  reported.items[reported.len].job_id = job_id;
                  if (job_id > max_reported) max_reported = job_id;
                  reported.len++;
                }
              }
            }

            struct mhx_host_state *host = mhx_host_set_upsert(&hosts, ident, ident_len);
            if (host) {
              host->last_seen = now;
              host->capacity = capacity;
              snprintf(host->addr, sizeof(host->addr), "%s", addr[0] ? addr : "?");
            }
            mhx_reconcile_host_jobs(&inflight, ident, ident_len, &reported, n, now + MHX_WORKER_LEASE_SEC);
            if (max_reported >= next_job_id) next_job_id = max_reported + 1;

            mhx_dispatch_to_host(router,
                                 ident,
                                 ident_len,
                                 capacity,
                                 reported.len,
                                 &colors,
                                 &M,
                                 &inflight,
                                 &color_cursor,
                                 &next_job_id,
                                 n,
                                 d,
                                 eps,
                                 now,
                                 &jobs_sent,
                                 verbose);
            mhx_reported_jobs_destroy(&reported);
          } else {
            (void)mhx_send_text_to(router, ident, ident_len, "WAIT\n");
          }
          free(copy);
        }
      }

      free(ident);
      free(msg);
    }

    now = time(NULL);
    if (now == (time_t)-1) now = 0;
    if (now - last_progress >= 5) {
      mhx_log(verbose,
              stderr,
              "head: progress M=%zu/%zu hosts=%zu cores=%zu inflight=%zu jobs_sent=%llu res_ok=%llu res_fail=%llu res_err=%llu applied=%llu\n",
              M.len,
              colors.count,
              mhx_host_set_live_hosts(&hosts, now),
              mhx_host_set_live_capacity(&hosts, now),
              inflight.len,
              jobs_sent,
              res_ok,
              res_fail,
              res_err,
              merges);
      mhx_log_host_roster(&hosts, now);
      last_progress = now;
    }

    if (now - last_save >= save_interval_sec) {
      if (mhx_save_state_atomic(state_path, &M) == 0) {
        if (verbose) fprintf(stderr, "head: saved %zu/%zu to %s\n", M.len, colors.count, state_path);
      } else {
        mhx_log(verbose, stderr, "head: failed to save to %s: %s\n", state_path, strerror(errno));
      }
      last_save = now;
    }

    if (M.len == colors.count && inflight.len == 0) {
      mhx_log(verbose, stderr, "head: done\n");
      break;
    }
  }

  zmq_close(router);
  zmq_ctx_term(ctx);

  if (local_worker_pid > 0) {
    (void)kill(local_worker_pid, SIGTERM);
    (void)waitpid(local_worker_pid, NULL, 0);
  }

  if (mhx_save_state_atomic(state_path, &M) == 0) {
    mhx_log(verbose, stderr, "head: saved final %zu/%zu to %s\n", M.len, colors.count, state_path);
  }

  mhx_inflight_set_destroy(&inflight);
  mhx_host_set_destroy(&hosts);
  mhx_map_destroy(&M);
  mhx_colors_free(&colors);
  while (waitpid(-1, NULL, WNOHANG) > 0) {}
  return 0;
}
