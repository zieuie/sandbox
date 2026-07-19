#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PARAM_LIMIT 256
#define BIG_BASE 1000000000U

typedef struct {
  uint32_t *limbs;
  size_t len;
  size_t cap;
} Big;

typedef struct {
  uint8_t a;
  uint8_t b;
  uint8_t t;
} Step;

typedef struct {
  Step *items;
  size_t count;
  size_t cap;
} StepList;

static void die(const char *message) {
  fprintf(stderr, "error: %s\n", message);
  exit(1);
}

static void *xcalloc(size_t count, size_t size) {
  void *ptr = calloc(count ? count : 1, size ? size : 1);
  if (!ptr) die("out of memory");
  return ptr;
}

static void *xrealloc(void *ptr, size_t size) {
  void *next = realloc(ptr, size ? size : 1);
  if (!next) die("out of memory");
  return next;
}

static void step_push(StepList *list, Step step) {
  if (list->count == list->cap) {
    list->cap = list->cap ? list->cap * 2 : 16;
    list->items = xrealloc(list->items, list->cap * sizeof(Step));
  }
  list->items[list->count++] = step;
}

static bool is_prime(uint32_t n) {
  if (n < 2) return false;
  if (n % 2 == 0) return n == 2;
  for (uint32_t d = 3; d * d <= n; d += 2) {
    if (n % d == 0) return false;
  }
  return true;
}

static bool checked_mul_u64(uint64_t a, uint64_t b, uint64_t *out) {
  if (a != 0 && b > UINT64_MAX / a) return false;
  *out = a * b;
  return true;
}

static bool pow_u64(uint32_t base, uint32_t exp, uint64_t *out) {
  uint64_t result = 1;
  for (uint32_t i = 0; i < exp; i++) {
    if (!checked_mul_u64(result, base, &result)) return false;
  }
  *out = result;
  return true;
}

static void big_reserve(Big *big, size_t cap) {
  if (big->cap >= cap) return;
  big->cap = cap;
  big->limbs = xrealloc(big->limbs, big->cap * sizeof(uint32_t));
}

static Big big_from_u64(uint64_t value) {
  Big big = {NULL, 0, 0};
  big_reserve(&big, 3);
  if (value == 0) {
    big.limbs[0] = 0;
    big.len = 1;
    return big;
  }
  while (value) {
    big.limbs[big.len++] = (uint32_t)(value % BIG_BASE);
    value /= BIG_BASE;
  }
  return big;
}

static void big_mul_small(Big *big, uint64_t factor) {
  if (factor == 0) {
    big->len = 1;
    big->limbs[0] = 0;
    return;
  }
  uint64_t carry = 0;
  for (size_t i = 0; i < big->len; i++) {
    uint64_t value = (uint64_t)big->limbs[i] * factor + carry;
    big->limbs[i] = (uint32_t)(value % BIG_BASE);
    carry = value / BIG_BASE;
  }
  while (carry) {
    big_reserve(big, big->len + 1);
    big->limbs[big->len++] = (uint32_t)(carry % BIG_BASE);
    carry /= BIG_BASE;
  }
}

static Big big_pow_small(uint32_t base, uint32_t exp) {
  Big big = big_from_u64(1);
  for (uint32_t i = 0; i < exp; i++) big_mul_small(&big, base);
  return big;
}

static void big_add(Big *left, const Big *right) {
  size_t max_len = left->len > right->len ? left->len : right->len;
  big_reserve(left, max_len + 1);
  uint64_t carry = 0;
  for (size_t i = 0; i < max_len; i++) {
    uint64_t a = i < left->len ? left->limbs[i] : 0;
    uint64_t b = i < right->len ? right->limbs[i] : 0;
    uint64_t value = a + b + carry;
    left->limbs[i] = (uint32_t)(value % BIG_BASE);
    carry = value / BIG_BASE;
  }
  left->len = max_len;
  if (carry) left->limbs[left->len++] = (uint32_t)carry;
}

static void big_print(const Big *big) {
  printf("%u", big->limbs[big->len - 1]);
  for (size_t i = big->len - 1; i > 0; i--) printf("%09u", big->limbs[i - 1]);
}

static void big_free(Big *big) {
  free(big->limbs);
  big->limbs = NULL;
  big->len = 0;
  big->cap = 0;
}

static int positive_mod(int value, int modulus) {
  int ret = value % modulus;
  return ret < 0 ? ret + modulus : ret;
}

static uint8_t overlap_value(uint8_t p, uint8_t a, uint8_t b, uint8_t t) {
  uint64_t seen[4] = {0, 0, 0, 0};
  uint8_t count = 0;
  for (uint8_t g_big = 0; g_big < a; g_big++) {
    for (uint8_t g_small = 0; g_small < b; g_small++) {
      uint8_t value = (uint8_t)positive_mod((int)g_small * t - g_big, p);
      uint64_t bit = UINT64_C(1) << (value & 63);
      uint8_t word = value >> 6;
      if ((seen[word] & bit) == 0) {
        seen[word] |= bit;
        count++;
      }
    }
  }
  return count;
}

static uint64_t parse_mb(const char *text) {
  char *end = NULL;
  errno = 0;
  unsigned long long value = strtoull(text, &end, 10);
  if (errno || *end) die("bad --max-mb value");
  return (uint64_t)value;
}

static bool parse_odd_power(const char *text, uint32_t *p_out, uint32_t *k_out) {
  const char *caret = strchr(text, '^');
  if (!caret) return false;
  char left[32];
  char right[32];
  size_t left_len = (size_t)(caret - text);
  if (left_len == 0 || left_len >= sizeof(left)) return false;
  memcpy(left, text, left_len);
  left[left_len] = '\0';
  snprintf(right, sizeof(right), "%s", caret + 1);

  char *end = NULL;
  unsigned long p = strtoul(left, &end, 10);
  if (*end || p >= PARAM_LIMIT || !is_prime((uint32_t)p)) return false;
  unsigned long r = strtoul(right, &end, 10);
  if (*end || r < 3 || r >= 2 * PARAM_LIMIT || (r % 2) == 0) return false;
  *p_out = (uint32_t)p;
  *k_out = (uint32_t)((r - 1) / 2);
  return true;
}

static bool parse_pk(const char *p_text, const char *k_text, uint32_t *p_out, uint32_t *k_out) {
  char *end = NULL;
  unsigned long p = strtoul(p_text, &end, 10);
  if (*end || p >= PARAM_LIMIT || !is_prime((uint32_t)p)) return false;
  unsigned long k = strtoul(k_text, &end, 10);
  if (*end || k >= PARAM_LIMIT) return false;
  *p_out = (uint32_t)p;
  *k_out = (uint32_t)k;
  return true;
}

static uint64_t estimate_bytes(uint32_t p, uint64_t axis, bool use_u32) {
  uint64_t max_back = (uint64_t)p * p;
  uint64_t rows = axis < max_back ? axis + 1 : max_back + 1;
  uint64_t cols = axis + 1;
  uint64_t cell_size = use_u32 ? sizeof(uint32_t) : sizeof(uint64_t);
  if (rows != 0 && cols > UINT64_MAX / rows) return UINT64_MAX;
  uint64_t cells = rows * cols;
  if (cells != 0 && cell_size > UINT64_MAX / cells) return UINT64_MAX;
  return cells * cell_size;
}

static uint64_t dp_bound_u32(uint8_t p, uint64_t axis) {
  uint64_t max_back = (uint64_t)p * p;
  uint64_t rows = axis < max_back ? axis + 1 : max_back + 1;
  uint32_t *dp = xcalloc((size_t)(rows * (axis + 1)), sizeof(uint32_t));

  for (uint64_t p_budget = 1; p_budget <= axis; p_budget++) {
    uint32_t *row = dp + (size_t)(p_budget % rows) * (axis + 1);
    memset(row, 0, (size_t)(axis + 1) * sizeof(uint32_t));
    for (uint64_t q_budget = 1; q_budget <= axis; q_budget++) {
      uint32_t best = 0;
      uint8_t max_a = p_budget < p ? (uint8_t)p_budget : p;
      uint8_t max_b = q_budget < p ? (uint8_t)q_budget : p;
      for (uint8_t a = 1; a <= max_a; a++) {
        for (uint8_t b = 1; b <= max_b; b++) {
          uint8_t limit = p;
          if (p_budget / a < limit) limit = (uint8_t)(p_budget / a);
          if (q_budget / b < limit) limit = (uint8_t)(q_budget / b);
          for (uint8_t t = 1; t <= limit; t++) {
            uint64_t prev_row_ix = (p_budget - (uint64_t)t * a) % rows;
            uint32_t prev = dp[(size_t)prev_row_ix * (axis + 1) + q_budget - (uint64_t)t * b];
            uint32_t gain = (uint32_t)(t * overlap_value(p, a, b, t));
            uint32_t candidate = prev + gain;
            if (candidate > best) best = candidate;
          }
        }
      }
      row[q_budget] = best;
    }
  }

  uint64_t result = dp[(size_t)(axis % rows) * (axis + 1) + axis];
  free(dp);
  return result;
}

static uint64_t dp_bound_u64(uint8_t p, uint64_t axis) {
  uint64_t max_back = (uint64_t)p * p;
  uint64_t rows = axis < max_back ? axis + 1 : max_back + 1;
  uint64_t *dp = xcalloc((size_t)(rows * (axis + 1)), sizeof(uint64_t));

  for (uint64_t p_budget = 1; p_budget <= axis; p_budget++) {
    uint64_t *row = dp + (size_t)(p_budget % rows) * (axis + 1);
    memset(row, 0, (size_t)(axis + 1) * sizeof(uint64_t));
    for (uint64_t q_budget = 1; q_budget <= axis; q_budget++) {
      uint64_t best = 0;
      uint8_t max_a = p_budget < p ? (uint8_t)p_budget : p;
      uint8_t max_b = q_budget < p ? (uint8_t)q_budget : p;
      for (uint8_t a = 1; a <= max_a; a++) {
        for (uint8_t b = 1; b <= max_b; b++) {
          uint8_t limit = p;
          if (p_budget / a < limit) limit = (uint8_t)(p_budget / a);
          if (q_budget / b < limit) limit = (uint8_t)(q_budget / b);
          for (uint8_t t = 1; t <= limit; t++) {
            uint64_t prev_row_ix = (p_budget - (uint64_t)t * a) % rows;
            uint64_t prev = dp[(size_t)prev_row_ix * (axis + 1) + q_budget - (uint64_t)t * b];
            uint64_t gain = (uint64_t)t * overlap_value(p, a, b, t);
            uint64_t candidate = prev + gain;
            if (candidate > best) best = candidate;
          }
        }
      }
      row[q_budget] = best;
    }
  }

  uint64_t result = dp[(size_t)(axis % rows) * (axis + 1) + axis];
  free(dp);
  return result;
}

static uint64_t dp_pattern(uint8_t p, uint64_t axis, uint64_t max_mb, bool stats, StepList *split) {
  uint64_t cells = (axis + 1) * (axis + 1);
  uint64_t bytes = cells * (sizeof(uint64_t) + sizeof(Step));
  uint64_t max_bytes = max_mb * 1024ULL * 1024ULL;
  if (stats) {
    fprintf(
      stderr,
      "# p=%u axis=%llu pattern_ram=%llu bytes\n",
      p,
      (unsigned long long)axis,
      (unsigned long long)bytes
    );
  }
  if (axis > (uint64_t)SIZE_MAX - 1 || cells > (uint64_t)SIZE_MAX / sizeof(uint64_t)) {
    die("pattern DP is too large for this machine");
  }
  if (bytes > max_bytes) die("pattern DP exceeds --max-mb; raise the cap or use a smaller k");

  uint64_t *dp = xcalloc((size_t)cells, sizeof(uint64_t));
  Step *steps = xcalloc((size_t)cells, sizeof(Step));

  for (uint64_t p_budget = 1; p_budget <= axis; p_budget++) {
    for (uint64_t q_budget = 1; q_budget <= axis; q_budget++) {
      uint64_t idx = p_budget * (axis + 1) + q_budget;
      uint64_t best = 0;
      Step best_step = {0, 0, 0};
      uint8_t max_a = p_budget < p ? (uint8_t)p_budget : p;
      uint8_t max_b = q_budget < p ? (uint8_t)q_budget : p;
      for (uint8_t a = 1; a <= max_a; a++) {
        for (uint8_t b = 1; b <= max_b; b++) {
          uint8_t limit = p;
          if (p_budget / a < limit) limit = (uint8_t)(p_budget / a);
          if (q_budget / b < limit) limit = (uint8_t)(q_budget / b);
          for (uint8_t t = 1; t <= limit; t++) {
            uint64_t prev_idx = (p_budget - (uint64_t)t * a) * (axis + 1) + q_budget - (uint64_t)t * b;
            uint64_t gain = (uint64_t)t * overlap_value(p, a, b, t);
            uint64_t candidate = dp[prev_idx] + gain;
            if (candidate > best) {
              best = candidate;
              best_step = (Step){a, b, t};
            }
          }
        }
      }
      dp[idx] = best;
      steps[idx] = best_step;
    }
  }

  uint64_t normalized = dp[axis * (axis + 1) + axis];
  uint64_t p_budget = axis;
  uint64_t q_budget = axis;
  while (p_budget > 0 && q_budget > 0) {
    Step step = steps[p_budget * (axis + 1) + q_budget];
    if (step.t == 0) break;
    step_push(split, step);
    p_budget -= (uint64_t)step.a * step.t;
    q_budget -= (uint64_t)step.b * step.t;
  }

  free(dp);
  free(steps);
  return normalized;
}

static Big bound_for(uint32_t p, uint32_t k, bool naive, uint64_t max_mb, bool stats) {
  uint32_t r = 2 * k + 1;
  uint64_t axis = 0;
  if (!pow_u64(p, k + 1, &axis)) die("p^(k+1) is too large for exact DP indexing");

  if (naive) {
    Big active = big_pow_small(p, 3 * k + 1);
    Big q = big_pow_small(p, r);
    big_add(&active, &q);
    big_free(&q);
    return active;
  }

  uint64_t upper = axis * (uint64_t)p;
  bool use_u32 = upper <= UINT32_MAX;
  uint64_t bytes = estimate_bytes(p, axis, use_u32);
  uint64_t max_bytes = max_mb * 1024ULL * 1024ULL;
  if (stats) {
    fprintf(
      stderr,
      "# p=%u k=%u axis=%llu cell=%s estimated_ram=%llu bytes\n",
      p,
      k,
      (unsigned long long)axis,
      use_u32 ? "u32" : "u64",
      (unsigned long long)bytes
    );
  }
  if (bytes > max_bytes) die("exact DP exceeds --max-mb; raise the cap or use a smaller k");
  if (axis > (uint64_t)SIZE_MAX - 1) die("axis is too large for this machine");

  uint64_t normalized = use_u32 ? dp_bound_u32((uint8_t)p, axis) : dp_bound_u64((uint8_t)p, axis);
  Big active = big_pow_small(p, 2 * k);
  big_mul_small(&active, normalized);
  Big q = big_pow_small(p, r);
  big_add(&active, &q);
  big_free(&q);
  return active;
}

static void fprint_big(FILE *out, const Big *big) {
  fprintf(out, "%u", big->limbs[big->len - 1]);
  for (size_t i = big->len - 1; i > 0; i--) fprintf(out, "%09u", big->limbs[i - 1]);
}

static void write_pattern(uint32_t p, uint32_t k, uint64_t max_mb, bool stats) {
  uint64_t axis = 0;
  if (!pow_u64(p, k + 1, &axis)) die("p^(k+1) is too large for exact DP indexing");
  StepList split = {NULL, 0, 0};
  uint64_t normalized = dp_pattern((uint8_t)p, axis, max_mb, stats, &split);
  Big active = big_pow_small(p, 2 * k);
  big_mul_small(&active, normalized);
  Big q = big_pow_small(p, 2 * k + 1);
  Big bound = big_pow_small(p, 2 * k);
  big_mul_small(&bound, normalized);
  big_add(&bound, &q);

  char path[256];
  sprintf(path, "pattern_%d_%d.json", p,2*k+1);
  FILE *out = fopen(path, "w");
  if (!out) {
    fprintf(stderr, "error: cannot open %s: %s\n", path, strerror(errno));
    exit(1);
  }
  fprintf(out, "{\n");
  fprintf(out, "  \"filetype\": \"odd_peek_pattern\",\n");
  fprintf(out, "  \"version\": 1,\n");
  fprintf(out, "  \"prime\": %u,\n", p);
  fprintf(out, "  \"k\": %u,\n", k);
  fprintf(out, "  \"degree\": %u,\n", 2 * k + 1);
  fprintf(out, "  \"axis\": %llu,\n", (unsigned long long)axis);
  fprintf(out, "  \"q\": \"");
  fprint_big(out, &q);
  fprintf(out, "\",\n");
  fprintf(out, "  \"normalized_value\": %llu,\n", (unsigned long long)normalized);
  fprintf(out, "  \"active_rows\": \"");
  fprint_big(out, &active);
  fprintf(out, "\",\n");
  fprintf(out, "  \"bound\": \"");
  fprint_big(out, &bound);
  fprintf(out, "\",\n");
  fprintf(out, "  \"split\": [\n");
  for (size_t i = 0; i < split.count; i++) {
    Step s = split.items[i];
    fprintf(out, "    [%u, %u, %u]%s\n", s.a, s.b, s.t, i + 1 == split.count ? "" : ",");
  }
  fprintf(out, "  ]\n");
  fprintf(out, "}\n");
  fclose(out);

  printf("Wrote DP pattern with %zu steps to %s\n", split.count, path);
  big_free(&active);
  big_free(&q);
  big_free(&bound);
  free(split.items);
}

static void print_case(uint32_t p, uint32_t k, bool naive, bool csv, uint64_t max_mb, bool stats) {
  uint32_t r = 2 * k + 1;
  Big q = big_pow_small(p, r);
  Big bound = bound_for(p, k, naive, max_mb, stats);
  if (csv) {
    big_print(&q);
    printf(",%u,%u,", p, k);
    big_print(&bound);
    printf("\n");
  } else {
    printf("M(");
    Big q_plus_one = big_pow_small(p, r);
    Big one = big_from_u64(1);
    big_add(&q_plus_one, &one);
    big_print(&q_plus_one);
    printf(", ");
    big_print(&q);
    printf(") >= ");
    big_print(&bound);
    printf("  # %u^%u, k=%u\n", p, r, k);
    big_free(&q_plus_one);
    big_free(&one);
  }
  big_free(&q);
  big_free(&bound);
}

static void usage(const char *argv0) {
  fprintf(
    stderr,
    "Usage: %s [OPTIONS] P^R [P^R ...]\n"
    "       %s [OPTIONS] --pk P K [--pk P K ...]\n"
    "\n"
    "Peek-only exact DP for odd powers R=2K+1. P and K must be < 256;\n"
    "P^(2K+1) may be huge. Exact DP indexing still needs P^(K+1).\n"
    "\n"
    "Options:\n"
    "  -n, --naive       Use the naive split bound\n"
    "      --csv         Print CSV rows: q,p,k,bound\n"
    "      --pattern F   Write final DP split pattern JSON for one case\n"
    "      --max-mb N    Refuse DP allocations above N MiB (default: 256)\n"
    "      --stats       Print axis and estimated RAM to stderr\n",
    argv0,
    argv0
  );
}

int main(int argc, char **argv) {
  bool naive = false;
  bool csv = false;
  bool stats = false;
  uint64_t max_mb = 256;
  bool printed_header = false;
  bool saw_case = false;
  bool use_pattern = false;

  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--naive") == 0) {
      naive = true;
    } else if (strcmp(argv[i], "--csv") == 0) {
      csv = true;
    } else if (strcmp(argv[i], "--stats") == 0) {
      stats = true;
    } else if (strcmp(argv[i], "--pattern") == 0) {
      use_pattern = true;
    } else if (strcmp(argv[i], "--max-mb") == 0) {
      if (++i >= argc) {
        usage(argv[0]);
        return 1;
      }
      max_mb = parse_mb(argv[i]);
    } else if (strcmp(argv[i], "--pk") == 0) {
      if (i + 2 >= argc) {
        usage(argv[0]);
        return 1;
      }
      uint32_t p = 0;
      uint32_t k = 0;
      if (!parse_pk(argv[i + 1], argv[i + 2], &p, &k)) die("--pk expects prime P < 256 and K < 256");
      if (use_pattern) {
        if (saw_case) die("--pattern accepts exactly one case");
        write_pattern(p, k, max_mb, stats);
        saw_case = true;
        i += 2;
        continue;
      }
      if (csv && !printed_header) {
        printf("q,p,k,bound\n");
        printed_header = true;
      }
      print_case(p, k, naive, csv, max_mb, stats);
      saw_case = true;
      i += 2;
    } else {
      uint32_t p = 0;
      uint32_t k = 0;
      if (!parse_odd_power(argv[i], &p, &k)) die("expected P^R with prime P < 256 and odd R=2K+1");
      if (use_pattern) {
        if (saw_case) die("--pattern accepts exactly one case");
        write_pattern(p, k, max_mb, stats);
        saw_case = true;
        continue;
      }
      if (csv && !printed_header) {
        printf("q,p,k,bound\n");
        printed_header = true;
      }
      print_case(p, k, naive, csv, max_mb, stats);
      saw_case = true;
    }
  }

  if (!saw_case) {
    usage(argv[0]);
    return 1;
  }
  return 0;
}
