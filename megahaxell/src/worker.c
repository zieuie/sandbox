#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <zmq.h>

#include "megahaxell/math/haxell.h"

#include <errno.h>
#include <ifaddrs.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;

enum {
  MHX_HEARTBEAT_SEC = 2,
  MHX_LOOP_POLL_MS = 200,
};

struct mhx_job_meta {
  unsigned long long job_id;
  int n;
  int d;
  double eps;
  char color[4096];
};

struct mhx_child_job {
  pid_t pid;
  int fd;
  int active;
  int exited;
  struct mhx_job_meta meta;
  char *result;
  size_t result_len;
  size_t result_cap;
};

static void mhx_on_sigint(int sig) {
  (void)sig;
  g_stop = 1;
}

static void mhx_reset_child_signals(void) {
  signal(SIGINT, SIG_DFL);
  signal(SIGTERM, SIG_DFL);
}

static void mhx_arm_parent_deathsig(pid_t expected_parent) {
  if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) _exit(126);
  if (getppid() != expected_parent) _exit(0);
}

static void mhx_usage(FILE *f, const char *argv0) {
  fprintf(f, "usage:\n");
  fprintf(f, "  %s --worker HOST[:PORT] --workers N [--verbose]\n", argv0);
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

static int mhx_write_all(int fd, const void *buf, size_t len) {
  const unsigned char *p = (const unsigned char *)buf;
  while (len > 0) {
    ssize_t n = write(fd, p, len);
    if (n < 0) {
      if (errno == EINTR) continue;
      return -1;
    }
    p += (size_t)n;
    len -= (size_t)n;
  }
  return 0;
}

static void mhx_detect_host_addr(char *out, size_t out_len) {
  if (out_len == 0) return;
  snprintf(out, out_len, "?");

  struct ifaddrs *ifaddr = NULL;
  if (getifaddrs(&ifaddr) != 0) return;

  for (struct ifaddrs *ifa = ifaddr; ifa; ifa = ifa->ifa_next) {
    if (!ifa->ifa_addr) continue;
    if (ifa->ifa_addr->sa_family != AF_INET) continue;
    if (!(ifa->ifa_flags & IFF_UP) || (ifa->ifa_flags & IFF_LOOPBACK)) continue;
    char buf[INET_ADDRSTRLEN];
    struct sockaddr_in *sin = (struct sockaddr_in *)ifa->ifa_addr;
    if (inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof(buf))) {
      snprintf(out, out_len, "%s", buf);
      freeifaddrs(ifaddr);
      return;
    }
  }

  freeifaddrs(ifaddr);
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

static void mhx_child_job_reset(struct mhx_child_job *job) {
  if (!job) return;
  if (job->fd >= 0) close(job->fd);
  free(job->result);
  memset(job, 0, sizeof(*job));
  job->fd = -1;
}

static int mhx_parse_job_meta(const char *msg, struct mhx_job_meta *meta) {
  memset(meta, 0, sizeof(*meta));
  if (sscanf(msg, "JOB %llu %d %d %lf %4095s", &meta->job_id, &meta->n, &meta->d, &meta->eps, meta->color) != 5) {
    return -1;
  }
  return (meta->n > 0 && meta->d > 0) ? 0 : -1;
}

static void mhx_try_raise_priority(int verbose) {
  struct sched_param sp;
  memset(&sp, 0, sizeof(sp));
  sp.sched_priority = 1;
  if (sched_setscheduler(0, SCHED_RR, &sp) == 0) {
    if (verbose) fprintf(stderr, "worker-supervisor[%ld]: using SCHED_RR priority=1\n", (long)getpid());
    return;
  }
  if (setpriority(PRIO_PROCESS, 0, -10) == 0) {
    if (verbose) fprintf(stderr, "worker-supervisor[%ld]: raised nice priority\n", (long)getpid());
    return;
  }
  if (verbose) {
    fprintf(stderr, "worker-supervisor[%ld]: priority boost unavailable: %s\n", (long)getpid(), strerror(errno));
  }
}

static void mhx_try_raise_math_priority(int verbose) {
  if (setpriority(PRIO_PROCESS, 0, -5) == 0) {
    if (verbose) fprintf(stderr, "worker-math[%ld]: raised math priority\n", (long)getpid());
    return;
  }
  if (verbose) {
    fprintf(stderr, "worker-math[%ld]: math priority boost unavailable: %s\n", (long)getpid(), strerror(errno));
  }
}

static int mhx_child_job_drain(struct mhx_child_job *job) {
  if (!job->active || job->fd < 0) return 0;
  for (;;) {
    char buf[4096];
    ssize_t n = read(job->fd, buf, sizeof(buf));
    if (n > 0) {
      size_t need = job->result_len + (size_t)n + 1;
      if (need > job->result_cap) {
        size_t new_cap = job->result_cap ? job->result_cap * 2 : 8192;
        while (new_cap < need) new_cap *= 2;
        char *p = (char *)realloc(job->result, new_cap);
        if (!p) return -1;
        job->result = p;
        job->result_cap = new_cap;
      }
      memcpy(job->result + job->result_len, buf, (size_t)n);
      job->result_len += (size_t)n;
      job->result[job->result_len] = '\0';
      continue;
    }
    if (n == 0) {
      close(job->fd);
      job->fd = -1;
      return 0;
    }
    if (errno == EINTR) continue;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
    return -1;
  }
}

static int mhx_worker_compute_job(const char *msg, int verbose, char **out) {
  *out = NULL;
  char *copy = strdup(msg);
  if (!copy) return -1;

  char *save = NULL;
  char *line = strtok_r(copy, "\n", &save);
  if (!line) {
    free(copy);
    return -1;
  }

  struct mhx_job_meta meta;
  if (mhx_parse_job_meta(line, &meta) != 0) {
    free(copy);
    return -1;
  }

  uint8_t *A = (uint8_t *)malloc((size_t)meta.n);
  if (!A) {
    free(copy);
    return -1;
  }
  if (mhx_parse_u8_list(meta.color, meta.n, A) != 0) {
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

  struct mhx_map M = mhx_map_create(meta.n, meta.d);
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

    uint8_t *color = (uint8_t *)malloc((size_t)meta.n);
    uint16_t *perm = (uint16_t *)malloc((size_t)meta.n * sizeof(uint16_t));
    if (!color || !perm) {
      free(color);
      free(perm);
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }
    if (mhx_parse_u8_list(cstr, meta.n, color) != 0 || mhx_parse_u16_list(pstr, meta.n, perm) != 0) {
      free(color);
      free(perm);
      mhx_map_destroy(&M);
      free(A);
      free(copy);
      return -1;
    }

    struct mhx_perm p = {.n = meta.n, .v = perm};
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

  struct mhx_haxell *h = mhx_haxell_create(meta.n, meta.d, meta.eps);
  if (!h) {
    mhx_map_destroy(&M);
    free(A);
    free(copy);
    return -1;
  }

  struct mhx_map diff = mhx_map_create(meta.n, meta.d);
  int grow_rc = mhx_grow_transversal(h, &M, A, &diff);
  free(A);

  if (verbose) fprintf(stderr, "worker-math[%ld]: job=%llu grow_rc=%d diff_len=%zu\n", (long)getpid(), meta.job_id, grow_rc, diff.len);

  const char *status = (grow_rc == 1) ? "OK" : (grow_rc == 0 ? "FAIL" : "ERR");
  char *body = NULL;
  size_t body_cap = 0;
  size_t body_off = 0;
  size_t written = 0;

  if (grow_rc == 1) {
    for (size_t i = 0; i < diff.len; i++) {
      char cbuf[4096];
      char pbuf[8192];
      if (mhx_color_to_string(diff.e[i].color, meta.n, cbuf, sizeof(cbuf)) != 0) continue;
      if (mhx_perm_to_string(diff.e[i].perm.v, meta.n, pbuf, sizeof(pbuf)) != 0) continue;
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
          mhx_haxell_destroy(h);
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

  char header[4224];
  int hw = snprintf(header, sizeof(header), "RES %llu %s %zu %s\n", meta.job_id, status, written, meta.color);
  if (hw <= 0) hw = 0;

  size_t out_len = (size_t)hw + body_off + 4;
  char *result = (char *)malloc(out_len + 1);
  if (!result) {
    free(body);
    mhx_map_destroy(&diff);
    mhx_map_destroy(&M);
    mhx_haxell_destroy(h);
    free(copy);
    return -1;
  }
  size_t off = 0;
  memcpy(result + off, header, (size_t)hw);
  off += (size_t)hw;
  if (body_off) {
    memcpy(result + off, body, body_off);
    off += body_off;
  }
  memcpy(result + off, "END\n", 4);
  off += 4;
  result[off] = '\0';

  free(body);
  mhx_map_destroy(&diff);
  mhx_map_destroy(&M);
  mhx_haxell_destroy(h);
  free(copy);
  *out = result;
  return 0;
}

static int mhx_spawn_job(const char *msg, const struct mhx_job_meta *meta, struct mhx_child_job *slot, int verbose) {
  int pipefd[2];
  if (pipe(pipefd) != 0) return -1;

  pid_t pid = fork();
  if (pid < 0) {
    close(pipefd[0]);
    close(pipefd[1]);
    return -1;
  }
  if (pid == 0) {
    pid_t parent_pid = getppid();
    close(pipefd[0]);
    mhx_reset_child_signals();
    mhx_arm_parent_deathsig(parent_pid);
    mhx_try_raise_math_priority(verbose);
    char *result = NULL;
    int rc = mhx_worker_compute_job(msg, verbose, &result);
    if (rc != 0 && !result) {
      char fallback[4224];
      int n = snprintf(fallback, sizeof(fallback), "RES %llu ERR 0 %s\nEND\n", meta->job_id, meta->color);
      if (n > 0) (void)mhx_write_all(pipefd[1], fallback, (size_t)n);
      close(pipefd[1]);
      _exit(1);
    }
    if (result) {
      (void)mhx_write_all(pipefd[1], result, strlen(result));
      free(result);
    }
    close(pipefd[1]);
    _exit(rc == 0 ? 0 : 1);
  }

  close(pipefd[1]);
  mhx_child_job_reset(slot);
  slot->pid = pid;
  slot->fd = pipefd[0];
  slot->active = 1;
  slot->meta = *meta;
  return 0;
}

static size_t mhx_active_slots(const struct mhx_child_job *slots, size_t capacity) {
  size_t active = 0;
  for (size_t i = 0; i < capacity; i++) {
    if (slots[i].active) active++;
  }
  return active;
}

static ssize_t mhx_find_free_slot(struct mhx_child_job *slots, size_t capacity) {
  for (size_t i = 0; i < capacity; i++) {
    if (!slots[i].active) return (ssize_t)i;
  }
  return -1;
}

static char *mhx_build_status(const char *verb, const struct mhx_child_job *slots, size_t capacity, const char *addr) {
  size_t active = mhx_active_slots(slots, capacity);
  size_t cap = 256 + active * 512;
  char *buf = (char *)malloc(cap);
  if (!buf) return NULL;
  size_t off = 0;

  int w = snprintf(buf + off, cap - off, "%s %zu %zu %s\n", verb, capacity, active, addr);
  if (w <= 0) w = 0;
  off += (size_t)w;

  for (size_t i = 0; i < capacity; i++) {
    if (!slots[i].active) continue;
    w = snprintf(buf + off, cap - off, "%llu %s\n", slots[i].meta.job_id, slots[i].meta.color);
    if (w <= 0) w = 0;
    off += (size_t)w;
    if (off + 16 >= cap) break;
  }

  snprintf(buf + off, cap - off, "END\n");
  return buf;
}

static int mhx_send_status(void *sock, const char *verb, const struct mhx_child_job *slots, size_t capacity, const char *addr) {
  char *msg = mhx_build_status(verb, slots, capacity, addr);
  if (!msg) return -1;
  int rc = mhx_send_text(sock, msg);
  free(msg);
  return rc;
}

static int mhx_send_ready_pull(void *sock, const struct mhx_child_job *slots, size_t capacity, int initial, const char *addr) {
  return mhx_send_status(sock, initial ? "HELLO" : "PULL", slots, capacity, addr);
}

static int mhx_send_ping(void *sock, const struct mhx_child_job *slots, size_t capacity, const char *addr) {
  return mhx_send_status(sock, "PING", slots, capacity, addr);
}

static void mhx_worker_supervisor_loop(const char *head, size_t capacity, int verbose) {
  void *ctx = zmq_ctx_new();
  if (!ctx) mhx_die_zmq("zmq_ctx_new");

  void *sock = zmq_socket(ctx, ZMQ_DEALER);
  if (!sock) mhx_die_zmq("zmq_socket(DEALER)");
  int linger = 0;
  int rcvtimeo = MHX_LOOP_POLL_MS;
  int sndtimeo = 1000;
  (void)zmq_setsockopt(sock, ZMQ_LINGER, &linger, sizeof(linger));
  (void)zmq_setsockopt(sock, ZMQ_RCVTIMEO, &rcvtimeo, sizeof(rcvtimeo));
  (void)zmq_setsockopt(sock, ZMQ_SNDTIMEO, &sndtimeo, sizeof(sndtimeo));

  unsigned long long nonce = (unsigned long long)time(NULL) ^ (unsigned long long)getpid();
  char identity[128];
  char host_addr[64];
  snprintf(identity, sizeof(identity), "mhx-host-%ld-%llu", (long)getpid(), nonce);
  mhx_detect_host_addr(host_addr, sizeof(host_addr));
  (void)zmq_setsockopt(sock, ZMQ_IDENTITY, identity, strlen(identity));

  if (zmq_connect(sock, head) != 0) mhx_die_zmq("zmq_connect");

  mhx_try_raise_priority(verbose);
  mhx_log(verbose, stderr, "worker-supervisor[%ld]: connected to %s capacity=%zu as %s\n", (long)getpid(), head, capacity, identity);

  struct mhx_child_job *slots = (struct mhx_child_job *)calloc(capacity, sizeof(*slots));
  if (!slots) exit(1);
  for (size_t i = 0; i < capacity; i++) slots[i].fd = -1;

  time_t last_ping = 0;
  int want_pull = 1;
  int initial_hello = 1;

  while (!g_stop) {
    for (size_t i = 0; i < capacity; i++) {
      if (!slots[i].active) continue;
      if (mhx_child_job_drain(&slots[i]) != 0) {
        mhx_log(verbose, stderr, "worker-supervisor[%ld]: child pipe error on slot=%zu\n", (long)getpid(), i);
      }
      if (!slots[i].exited) {
        int status = 0;
        pid_t rc = waitpid(slots[i].pid, &status, WNOHANG);
        if (rc == slots[i].pid) slots[i].exited = 1;
      }
      if (slots[i].active && slots[i].exited && slots[i].fd < 0 && slots[i].result) {
        if (mhx_send_text(sock, slots[i].result) != 0) {
          mhx_log(verbose, stderr, "worker-supervisor[%ld]: send RES failed: %s\n", (long)getpid(), zmq_strerror(zmq_errno()));
          goto cleanup;
        }
        mhx_child_job_reset(&slots[i]);
        want_pull = 1;
      }
    }

    size_t active = mhx_active_slots(slots, capacity);
    if (want_pull && active < capacity) {
      if (mhx_send_ready_pull(sock, slots, capacity, initial_hello, host_addr) == 0) {
        want_pull = 0;
        initial_hello = 0;
      }
    }

    time_t now = time(NULL);
    if (now == (time_t)-1) now = 0;
    if (last_ping == 0 || now - last_ping >= MHX_HEARTBEAT_SEC) {
      if (mhx_send_ping(sock, slots, capacity, host_addr) == 0) last_ping = now;
    }

    char *msg = NULL;
    int n = mhx_recv_text(sock, &msg);
    if (n < 0) {
      int e = zmq_errno();
      if (e == EINTR || e == EAGAIN) continue;
      mhx_log(verbose, stderr, "worker-supervisor[%ld]: recv error %s (%d)\n", (long)getpid(), zmq_strerror(e), e);
      break;
    }
    if (!msg) continue;

    if (strncmp(msg, "JOB ", 4) == 0) {
      struct mhx_job_meta meta;
      if (mhx_parse_job_meta(msg, &meta) == 0) {
        ssize_t idx = mhx_find_free_slot(slots, capacity);
        if (idx >= 0) {
          if (mhx_spawn_job(msg, &meta, &slots[idx], verbose) == 0) {
            if (mhx_active_slots(slots, capacity) < capacity) want_pull = 1;
          } else {
            char err_msg[4224];
            int m = snprintf(err_msg, sizeof(err_msg), "RES %llu ERR 0 %s\nEND\n", meta.job_id, meta.color);
            if (m > 0) (void)mhx_send_text(sock, err_msg);
            want_pull = 1;
          }
        }
      }
    } else if (strncmp(msg, "WAIT", 4) == 0) {
      /* heartbeat will ask again soon; completed jobs also trigger pulls */
    } else if (strncmp(msg, "DONE", 4) == 0) {
      if (mhx_active_slots(slots, capacity) == 0) {
        free(msg);
        break;
      }
    }

    free(msg);
  }

cleanup:
  for (size_t i = 0; i < capacity; i++) {
    if (slots[i].active && !slots[i].exited) {
      (void)kill(slots[i].pid, SIGTERM);
      (void)waitpid(slots[i].pid, NULL, 0);
    }
    mhx_child_job_reset(&slots[i]);
  }
  free(slots);
  zmq_close(sock);
  zmq_ctx_term(ctx);
}

int mhx_worker_main(int argc, char **argv) {
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
    } else if (strcmp(argv[i], "--worker") == 0 && i + 1 < argc) {
      head_hostport = argv[++i];
    } else if (strcmp(argv[i], "--workers") == 0 && i + 1 < argc) {
      workers = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--verbose") == 0) {
      verbose = 1;
    } else {
      mhx_usage(stderr, argv[0]);
      return 2;
    }
  }

  if (!head_hostport || workers <= 0) {
    mhx_usage(stderr, argv[0]);
    return 2;
  }

  signal(SIGINT, mhx_on_sigint);
  signal(SIGTERM, mhx_on_sigint);

  char endpoint[256];
  if (strstr(head_hostport, "://")) {
    snprintf(endpoint, sizeof(endpoint), "%s", head_hostport);
  } else if (strchr(head_hostport, ':')) {
    snprintf(endpoint, sizeof(endpoint), "tcp://%s", head_hostport);
  } else {
    snprintf(endpoint, sizeof(endpoint), "tcp://%s:9001", head_hostport);
  }

  mhx_worker_supervisor_loop(endpoint, (size_t)workers, verbose);
  return 0;
}
