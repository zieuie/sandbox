#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DEGREE 64

typedef long long i64;

typedef struct {
  int a;
  int b;
  int t;
} Step;

typedef struct {
  int count;
  int cap;
  int *items;
} IntList;

typedef struct {
  int p;
  int r;
  int l;
  int q;
  int *prim;
  int generator;
  int *label_to_basis;
  int *basis_to_label;
  int *add;
  int *mul;
  int *inv;
} Field;

typedef struct {
  int count;
  int cap;
  Step *items;
} StepList;

typedef struct {
  i64 value;
  StepList split;
} SplitResult;

typedef struct {
  Field field;
  IntList *p_sets;
  IntList *q_sets;
  int blocks;
  i64 coverage;
} Construction;

typedef struct {
  int k;
  int g;
  int h;
} LeftNode;

static void die(const char *message) {
  fprintf(stderr, "error: %s\n", message);
  exit(1);
}

static void *xmalloc(size_t size) {
  void *ptr = malloc(size ? size : 1);
  if (!ptr) die("out of memory");
  return ptr;
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

static int ipow_int(int base, int exp) {
  int result = 1;
  for (int i = 0; i < exp; i++) {
    if (base != 0 && result > INT_MAX / base) die("integer overflow");
    result *= base;
  }
  return result;
}

static void check_degree(int r) {
  if (r > MAX_DEGREE) die("degree is too large for the fixed C work buffers");
}

static int mod_int(int value, int p) {
  int ret = value % p;
  return ret < 0 ? ret + p : ret;
}

static void list_push(IntList *list, int value) {
  if (list->count == list->cap) {
    list->cap = list->cap ? list->cap * 2 : 8;
    list->items = xrealloc(list->items, (size_t)list->cap * sizeof(int));
  }
  list->items[list->count++] = value;
}

static void step_push(StepList *list, Step value) {
  if (list->count == list->cap) {
    list->cap = list->cap ? list->cap * 2 : 8;
    list->items = xrealloc(list->items, (size_t)list->cap * sizeof(Step));
  }
  list->items[list->count++] = value;
}

static int compare_ints(const void *left, const void *right) {
  int a = *(const int *)left;
  int b = *(const int *)right;
  return (a > b) - (a < b);
}

static bool is_prime(int n) {
  if (n < 2) return false;
  if (n % 2 == 0) return n == 2;
  for (int d = 3; d * d <= n; d += 2) {
    if (n % d == 0) return false;
  }
  return true;
}

static bool prime_power_from_int(int value, int *p_out, int *r_out) {
  if (value < 2) return false;
  if (is_prime(value)) {
    *p_out = value;
    *r_out = 1;
    return true;
  }
  for (int p = 2; p <= value; p++) {
    if (!is_prime(p)) continue;
    int power = p;
    int exponent = 1;
    while (power < value && power <= INT_MAX / p) {
      power *= p;
      exponent++;
    }
    if (power == value) {
      *p_out = p;
      *r_out = exponent;
      return true;
    }
  }
  return false;
}

static bool parse_pr(const char *text, int *p_out, int *r_out) {
  const char *caret = strchr(text, '^');
  if (caret) {
    char left[64];
    char right[64];
    size_t left_len = (size_t)(caret - text);
    if (left_len >= sizeof(left)) return false;
    memcpy(left, text, left_len);
    left[left_len] = '\0';
    snprintf(right, sizeof(right), "%s", caret + 1);
    char *end = NULL;
    long p = strtol(left, &end, 10);
    if (*end || p < 2 || p > INT_MAX || !is_prime((int)p)) return false;
    long r = strtol(right, &end, 10);
    if (*end || r < 1 || r > INT_MAX) return false;
    *p_out = (int)p;
    *r_out = (int)r;
    return true;
  }
  char *end = NULL;
  long q = strtol(text, &end, 10);
  if (*end || q < 2 || q > INT_MAX) return false;
  return prime_power_from_int((int)q, p_out, r_out);
}

static int digits_to_int(const int *digits, int len, int base) {
  int total = 0;
  int multiplier = 1;
  for (int i = 0; i < len; i++) {
    total += digits[i] * multiplier;
    multiplier *= base;
  }
  return total;
}

static void basis_digits(int value, int p, int r, int *digits) {
  for (int i = 0; i < r; i++) {
    digits[i] = value % p;
    value /= p;
  }
}

static int inv_mod_prime(int value, int p) {
  value = mod_int(value, p);
  for (int x = 1; x < p; x++) {
    if ((value * x) % p == 1) return x;
  }
  die("no modular inverse");
  return 0;
}

static void poly_mul_mod_arrays(const int *a, int alen, const int *b, int blen, const int *modulus, int degree, int p, int *out) {
  int prod_len = alen + blen - 1;
  if (prod_len > 2 * MAX_DEGREE + 1 || degree > MAX_DEGREE) die("degree is too large");
  int prod[2 * MAX_DEGREE + 1] = {0};
  for (int i = 0; i < alen; i++) {
    for (int j = 0; j < blen; j++) {
      prod[i + j] = (prod[i + j] + a[i] * b[j]) % p;
    }
  }
  while (prod_len > degree) {
    int lead = mod_int(prod[prod_len - 1], p);
    if (lead) {
      int offset = prod_len - (degree + 1);
      for (int i = 0; i <= degree; i++) {
        prod[offset + i] = mod_int(prod[offset + i] - lead * modulus[i], p);
      }
    }
    prod_len--;
  }
  for (int i = 0; i < degree; i++) out[i] = i < prod_len ? mod_int(prod[i], p) : 0;
}

static int basis_mul_raw(int left, int right, const int *prim, int p, int r) {
  check_degree(r);
  int a[MAX_DEGREE];
  int b[MAX_DEGREE];
  int out[MAX_DEGREE];
  basis_digits(left, p, r, a);
  basis_digits(right, p, r, b);
  poly_mul_mod_arrays(a, r, b, r, prim, r, p, out);
  int ret = digits_to_int(out, r, p);
  return ret;
}

static int basis_add_raw(int left, int right, int p, int r) {
  check_degree(r);
  int a[MAX_DEGREE];
  int b[MAX_DEGREE];
  int out[MAX_DEGREE];
  basis_digits(left, p, r, a);
  basis_digits(right, p, r, b);
  for (int i = 0; i < r; i++) out[i] = (a[i] + b[i]) % p;
  int ret = digits_to_int(out, r, p);
  return ret;
}

static int basis_pow_raw(int base, int exponent, const int *prim, int p, int r) {
  int result = 1;
  int power = base;
  while (exponent) {
    if (exponent & 1) result = basis_mul_raw(result, power, prim, p, r);
    power = basis_mul_raw(power, power, prim, p, r);
    exponent >>= 1;
  }
  return result;
}

static int polynomial_remainder_zero(const int *dividend, int dividend_len, const int *divisor, int divisor_len, int p) {
  int *rem = xmalloc((size_t)dividend_len * sizeof(int));
  memcpy(rem, dividend, (size_t)dividend_len * sizeof(int));
  int rem_len = dividend_len;
  while (rem_len > 0 && rem[rem_len - 1] == 0) rem_len--;
  int div_len = divisor_len;
  while (div_len > 0 && divisor[div_len - 1] == 0) div_len--;
  if (div_len == 0) die("polynomial division by zero");
  int inv_lead = inv_mod_prime(divisor[div_len - 1], p);
  while (rem_len >= div_len && rem_len > 0) {
    int coeff = rem[rem_len - 1] * inv_lead % p;
    int offset = rem_len - div_len;
    for (int i = 0; i < div_len; i++) {
      rem[offset + i] = mod_int(rem[offset + i] - coeff * divisor[i], p);
    }
    while (rem_len > 0 && rem[rem_len - 1] == 0) rem_len--;
  }
  bool zero = rem_len == 0;
  free(rem);
  return zero;
}

static bool is_irreducible_poly(const int *poly, int p, int degree) {
  for (int d = 1; d <= degree / 2; d++) {
    int combos = ipow_int(p, d);
    for (int index = 0; index < combos; index++) {
      int *candidate = xcalloc((size_t)(d + 1), sizeof(int));
      int n = index;
      for (int i = d - 1; i >= 0; i--) {
        candidate[i] = n % p;
        n /= p;
      }
      candidate[d] = 1;
      bool divides = polynomial_remainder_zero(poly, degree + 1, candidate, d + 1, p);
      free(candidate);
      if (divides) return false;
    }
  }
  return true;
}

static IntList proper_divisors_for_order(int n) {
  IntList prime_factors = {0, 0, NULL};
  int temp = n;
  for (int d = 2; d * d <= temp; d++) {
    if (temp % d == 0) {
      list_push(&prime_factors, d);
      while (temp % d == 0) temp /= d;
    }
  }
  if (temp > 1) list_push(&prime_factors, temp);
  IntList ret = {0, 0, NULL};
  for (int i = 0; i < prime_factors.count; i++) list_push(&ret, n / prime_factors.items[i]);
  free(prime_factors.items);
  return ret;
}

static bool is_primitive_poly(const int *poly, int p, int degree) {
  if (!is_irreducible_poly(poly, p, degree)) return false;
  int order = ipow_int(p, degree) - 1;
  IntList checks = proper_divisors_for_order(order);
  bool primitive = true;
  for (int i = 0; i < checks.count; i++) {
    if (basis_pow_raw(p, checks.items[i], poly, p, degree) == 1) {
      primitive = false;
      break;
    }
  }
  free(checks.items);
  return primitive;
}

static int *find_primitive_polynomial(int p, int degree) {
  int *coeffs = xcalloc((size_t)degree, sizeof(int));
  int *poly = xcalloc((size_t)(degree + 1), sizeof(int));
  while (true) {
    if (coeffs[0] != 0) {
      for (int i = 0; i < degree; i++) poly[i] = coeffs[i];
      poly[degree] = 1;
      if (is_primitive_poly(poly, p, degree)) {
        free(coeffs);
        return poly;
      }
    }
    int pos = degree - 1;
    while (pos >= 0) {
      coeffs[pos]++;
      if (coeffs[pos] < p) break;
      coeffs[pos] = 0;
      pos--;
    }
    if (pos < 0) break;
  }
  free(coeffs);
  free(poly);
  die("could not find a primitive polynomial");
  return NULL;
}

static int find_primitive_element(const Field *field) {
  if (field->q == 2) return 1;
  int order = field->q - 1;
  IntList checks = proper_divisors_for_order(order);
  for (int candidate = 2; candidate < field->q; candidate++) {
    bool ok = true;
    for (int i = 0; i < checks.count; i++) {
      if (basis_pow_raw(candidate, checks.items[i], field->prim, field->p, field->r) == 1) {
        ok = false;
        break;
      }
    }
    if (ok) {
      free(checks.items);
      return candidate;
    }
  }
  free(checks.items);
  die("could not find a primitive element");
  return 0;
}

static Field make_field(int p, int r, const int *prim_arg, int prim_len) {
  check_degree(r);
  Field field;
  memset(&field, 0, sizeof(field));
  field.p = p;
  field.r = r;
  field.l = r >> 1;
  field.q = ipow_int(p, r);
  if (prim_arg) {
    if (prim_len != r + 1) die("--prim must contain degree+1 coefficients");
    field.prim = xmalloc((size_t)(r + 1) * sizeof(int));
    memcpy(field.prim, prim_arg, (size_t)(r + 1) * sizeof(int));
  } else {
    field.prim = find_primitive_polynomial(p, r);
  }
  field.generator = find_primitive_element(&field);
  field.label_to_basis = xmalloc((size_t)field.q * sizeof(int));
  field.basis_to_label = xmalloc((size_t)field.q * sizeof(int));
  field.label_to_basis[0] = 0;
  field.label_to_basis[1] = 1;
  int current = 1;
  for (int i = 1; i < field.q - 1; i++) {
    current = basis_mul_raw(current, field.generator, field.prim, p, r);
    field.label_to_basis[i + 1] = current;
  }
  for (int label = 0; label < field.q; label++) field.basis_to_label[field.label_to_basis[label]] = label;

  size_t table_size = (size_t)field.q * (size_t)field.q;
  field.add = xmalloc(table_size * sizeof(int));
  field.mul = xmalloc(table_size * sizeof(int));
  for (int a = 0; a < field.q; a++) {
    for (int b = 0; b < field.q; b++) {
      int basis_sum = basis_add_raw(field.label_to_basis[a], field.label_to_basis[b], p, r);
      field.add[a * field.q + b] = field.basis_to_label[basis_sum];
      if (a == 0 || b == 0) {
        field.mul[a * field.q + b] = 0;
      } else {
        int basis_product = basis_mul_raw(field.label_to_basis[a], field.label_to_basis[b], field.prim, p, r);
        field.mul[a * field.q + b] = field.basis_to_label[basis_product];
      }
    }
  }
  field.inv = xmalloc((size_t)field.q * sizeof(int));
  field.inv[0] = -1;
  for (int value = 1; value < field.q; value++) {
    int exponent = value - 1;
    int inverse_exponent = (field.q - 1 - exponent) % (field.q - 1);
    field.inv[value] = inverse_exponent + 1;
  }
  return field;
}

static void free_field(Field *field) {
  free(field->prim);
  free(field->label_to_basis);
  free(field->basis_to_label);
  free(field->add);
  free(field->mul);
  free(field->inv);
  memset(field, 0, sizeof(*field));
}

static IntList *sud_sets(const Field *field, int *set_count_out) {
  int suffix_count = ipow_int(field->p, field->l);
  int set_count = field->p * suffix_count;
  IntList *sets = xcalloc((size_t)set_count, sizeof(IntList));
  int *digits = xmalloc((size_t)field->r * sizeof(int));
  for (int label = 0; label < field->q; label++) {
    basis_digits(field->label_to_basis[label], field->p, field->r, digits);
    int pre = 0;
    for (int i = 0; i < field->r; i++) pre = (pre + digits[i]) % field->p;
    int suf = digits_to_int(digits, field->l, field->p);
    list_push(&sets[pre * suffix_count + suf], label);
  }
  free(digits);
  *set_count_out = set_count;
  return sets;
}

static int overlap_value(int n, int a, int b, int t) {
  bool *seen = xcalloc((size_t)n, sizeof(bool));
  int count = 0;
  for (int g_big = 0; g_big < a; g_big++) {
    for (int g_small = 0; g_small < b; g_small++) {
      int value = mod_int(g_small * t - g_big, n);
      if (!seen[value]) {
        seen[value] = true;
        count++;
      }
    }
  }
  free(seen);
  return count;
}

static SplitResult dp_split(int n, int r) {
  int l = r >> 1;
  int f = ipow_int(n, l);
  int nf = n * f;
  int ff = f * f;
  size_t cells = (size_t)(nf + 1) * (size_t)(nf + 1);
  i64 *values = xcalloc(cells, sizeof(i64));
  Step *steps = xcalloc(cells, sizeof(Step));
  bool *has_step = xcalloc(cells, sizeof(bool));
  int *overlaps = xcalloc((size_t)(n + 1) * (n + 1) * (n + 1), sizeof(int));
  for (int a = 1; a <= n; a++) {
    for (int b = 1; b <= n; b++) {
      for (int t = 1; t <= n; t++) {
        overlaps[(a * (n + 1) + b) * (n + 1) + t] = overlap_value(n, a, b, t);
      }
    }
  }

  for (int p = 1; p <= nf; p++) {
    for (int q = 1; q <= nf; q++) {
      size_t idx = (size_t)p * (nf + 1) + q;
      for (int a = 1; a <= n && a <= p; a++) {
        for (int b = 1; b <= n && b <= q; b++) {
          int limit = n;
          if (p / a < limit) limit = p / a;
          if (q / b < limit) limit = q / b;
          for (int t = 1; t <= limit; t++) {
            int ov = overlaps[(a * (n + 1) + b) * (n + 1) + t];
            i64 candidate = (i64)t * ff * ov + values[(size_t)(p - t * a) * (nf + 1) + (q - t * b)];
            if (candidate > values[idx]) {
              values[idx] = candidate;
              steps[idx] = (Step){a, b, t};
              has_step[idx] = true;
            }
          }
        }
      }
    }
  }

  SplitResult result;
  result.value = values[(size_t)nf * (nf + 1) + nf];
  result.split = (StepList){0, 0, NULL};
  int p = nf;
  int q = nf;
  while (p > 0 && q > 0) {
    size_t idx = (size_t)p * (nf + 1) + q;
    if (!has_step[idx]) break;
    Step step = steps[idx];
    step_push(&result.split, step);
    p -= step.a * step.t;
    q -= step.b * step.t;
  }

  free(values);
  free(steps);
  free(has_step);
  free(overlaps);
  return result;
}

static void free_sets(IntList *sets, int count) {
  for (int i = 0; i < count; i++) free(sets[i].items);
  free(sets);
}

static IntList *mishy_q(int n, int r, const StepList *split, const IntList *sets, int *block_count_out) {
  int l = r >> 1;
  int f = ipow_int(n, l);
  int blocks = 0;
  for (int i = 0; i < split->count; i++) blocks += split->items[i].t;
  IntList *q_sets = xcalloc((size_t)blocks, sizeof(IntList));
  int j = 0;
  int block = 0;
  for (int si = 0; si < split->count; si++) {
    Step step = split->items[si];
    for (int k = 0; k < step.b * step.t; k++) {
      int q = j / n;
      int residue = j % n;
      IntList cell = sets[residue * f + q];
      int target = block + (k % step.t);
      for (int x = 0; x < cell.count; x++) list_push(&q_sets[target], cell.items[x]);
      j++;
    }
    block += step.t;
  }
  *block_count_out = blocks;
  return q_sets;
}

static LeftNode *mishy_a(int n, int r, const StepList *split, int *left_count_out) {
  int l = r >> 1;
  int f = ipow_int(n, l);
  int count = 0;
  for (int si = 0; si < split->count; si++) count += split->items[si].t * split->items[si].a * f;
  LeftNode *left = xmalloc((size_t)count * sizeof(LeftNode));
  int k = 0;
  int ix = 0;
  for (int si = 0; si < split->count; si++) {
    Step step = split->items[si];
    for (int ti = 0; ti < step.t; ti++) {
      for (int g = 0; g < step.a; g++) {
        for (int h = 0; h < f; h++) {
          left[ix++] = (LeftNode){k, g, h};
        }
      }
      k++;
    }
  }
  *left_count_out = count;
  return left;
}

static int hopcroft_karp(int left_count, int right_count, const int *offsets, const int *adj, int *match_left) {
  int *match_right = xmalloc((size_t)right_count * sizeof(int));
  int *dist = xmalloc((size_t)left_count * sizeof(int));
  int *queue = xmalloc((size_t)left_count * sizeof(int));
  for (int i = 0; i < left_count; i++) match_left[i] = -1;
  for (int i = 0; i < right_count; i++) match_right[i] = -1;
  int matching = 0;
  while (true) {
    int head = 0;
    int tail = 0;
    for (int u = 0; u < left_count; u++) {
      if (match_left[u] == -1) {
        dist[u] = 0;
        queue[tail++] = u;
      } else {
        dist[u] = -1;
      }
    }
    bool found = false;
    while (head < tail) {
      int u = queue[head++];
      for (int ei = offsets[u]; ei < offsets[u + 1]; ei++) {
        int v = adj[ei];
        int mate = match_right[v];
        if (mate == -1) {
          found = true;
        } else if (dist[mate] == -1) {
          dist[mate] = dist[u] + 1;
          queue[tail++] = mate;
        }
      }
    }
    if (!found) break;

    bool progress = false;
    int *stack_u = xmalloc((size_t)(left_count + 1) * sizeof(int));
    int *iter = xmalloc((size_t)left_count * sizeof(int));
    memcpy(iter, offsets, (size_t)left_count * sizeof(int));

    for (int start = 0; start < left_count; start++) {
      if (match_left[start] != -1) continue;
      int sp = 0;
      stack_u[sp++] = start;
      bool augmented = false;
      while (sp > 0 && !augmented) {
        int u = stack_u[sp - 1];
        bool advanced = false;
        while (iter[u] < offsets[u + 1]) {
          int v = adj[iter[u]++];
          int mate = match_right[v];
          if (mate == -1) {
            int cur_v = v;
            for (int depth = sp - 1; depth >= 0; depth--) {
              int cur_u = stack_u[depth];
              int next_v = match_left[cur_u];
              match_left[cur_u] = cur_v;
              match_right[cur_v] = cur_u;
              cur_v = next_v;
            }
            matching++;
            progress = true;
            augmented = true;
            break;
          }
          if (dist[mate] == dist[u] + 1) {
            stack_u[sp++] = mate;
            advanced = true;
            break;
          }
        }
        if (!advanced && !augmented) {
          dist[u] = -1;
          sp--;
        }
      }
    }
    free(stack_u);
    free(iter);
    if (!progress) break;
  }
  free(match_right);
  free(dist);
  free(queue);
  return matching;
}

static IntList *realize_p_sets(const Field *field, const IntList *sets, const LeftNode *left, int left_count, int blocks) {
  int f = ipow_int(field->p, field->l);
  int *offsets = xmalloc((size_t)(left_count + 1) * sizeof(int));
  int total_edges = 0;
  for (int i = 0; i < left_count; i++) {
    int set_index = left[i].h + left[i].g * f;
    offsets[i] = total_edges;
    total_edges += sets[set_index].count;
  }
  offsets[left_count] = total_edges;
  int *adj = xmalloc((size_t)total_edges * sizeof(int));
  for (int i = 0; i < left_count; i++) {
    int set_index = left[i].h + left[i].g * f;
    int inv = field->inv[left[i].k + 1];
    int out = offsets[i];
    for (int j = 0; j < sets[set_index].count; j++) {
      int x = sets[set_index].items[j];
      adj[out++] = field->mul[inv * field->q + x];
    }
  }
  int *match_left = xmalloc((size_t)left_count * sizeof(int));
  int matched = hopcroft_karp(left_count, field->q, offsets, adj, match_left);
  if (matched != left_count) die("matching failed");

  IntList *p_sets = xcalloc((size_t)blocks, sizeof(IntList));
  for (int i = 0; i < left_count; i++) list_push(&p_sets[left[i].k], match_left[i]);
  for (int i = 0; i < blocks; i++) qsort(p_sets[i].items, (size_t)p_sets[i].count, sizeof(int), compare_ints);

  free(offsets);
  free(adj);
  free(match_left);
  return p_sets;
}

static Construction theorem(int n, int r, const int *prim, int prim_len, bool verbose) {
  if (verbose) printf("Computing a partition for %d^%d = %d\n", n, r, ipow_int(n, r));
  Construction c;
  memset(&c, 0, sizeof(c));
  c.field = make_field(n, r, prim, prim_len);
  if (verbose) {
    printf("\t* Field made:");
    for (int i = 0; i <= r; i++) printf(" %d", c.field.prim[i]);
    printf("\nComputing optimal split...\n");
  }
  int set_count = 0;
  IntList *sets = sud_sets(&c.field, &set_count);
  SplitResult split = dp_split(n, r);
  c.coverage = split.value;
  if (verbose) {
    printf("\t* Split calculated:");
    for (int i = 0; i < split.split.count; i++) {
      Step s = split.split.items[i];
      printf(" (%d,%d,%d)", s.a, s.b, s.t);
    }
    printf(" %lld\n", split.value);
  }
  c.q_sets = mishy_q(n, r, &split.split, sets, &c.blocks);
  int left_count = 0;
  LeftNode *left = mishy_a(n, r, &split.split, &left_count);
  c.p_sets = realize_p_sets(&c.field, sets, left, left_count, c.blocks);
  free(left);
  free(split.split.items);
  free_sets(sets, set_count);
  return c;
}

static Construction theorem_from_split(
  int n,
  int r,
  const StepList *split,
  i64 value,
  const int *prim,
  int prim_len,
  bool verbose
) {
  if (verbose) printf("Building from stored split for %d^%d = %d\n", n, r, ipow_int(n, r));
  Construction c;
  memset(&c, 0, sizeof(c));
  c.field = make_field(n, r, prim, prim_len);
  if (verbose) {
    printf("\t* Field made:");
    for (int i = 0; i <= r; i++) printf(" %d", c.field.prim[i]);
    printf("\n\t* Stored split:");
    for (int i = 0; i < split->count; i++) {
      Step s = split->items[i];
      printf(" (%d,%d,%d)", s.a, s.b, s.t);
    }
    printf(" %lld\n", value);
  }
  int set_count = 0;
  IntList *sets = sud_sets(&c.field, &set_count);
  c.coverage = value;
  c.q_sets = mishy_q(n, r, split, sets, &c.blocks);
  int left_count = 0;
  LeftNode *left = mishy_a(n, r, split, &left_count);
  c.p_sets = realize_p_sets(&c.field, sets, left, left_count, c.blocks);
  free(left);
  free_sets(sets, set_count);
  return c;
}

static Construction naive_theorem(int n, int r, const int *prim, int prim_len, bool verbose) {
  Construction c;
  memset(&c, 0, sizeof(c));
  c.field = make_field(n, r, prim, prim_len);
  int set_count = 0;
  IntList *sets = sud_sets(&c.field, &set_count);
  int f = ipow_int(n, r >> 1);
  c.blocks = f;
  c.q_sets = xcalloc((size_t)c.blocks, sizeof(IntList));
  for (int residue = 0; residue < f; residue++) {
    for (int digit = 0; digit < n; digit++) {
      IntList cell = sets[f * digit + residue];
      for (int x = 0; x < cell.count; x++) list_push(&c.q_sets[residue], cell.items[x]);
    }
  }
  int left_count = f * f;
  LeftNode *left = xmalloc((size_t)left_count * sizeof(LeftNode));
  int ix = 0;
  for (int k = 0; k < f; k++) {
    for (int i = 0; i < f; i++) left[ix++] = (LeftNode){k, 0, i};
  }
  c.p_sets = realize_p_sets(&c.field, sets, left, left_count, c.blocks);
  c.coverage = (i64)ipow_int(n, r) * f;
  if (verbose) printf("\t* Naive construction uses %d blocks\n", c.blocks);
  free(left);
  free_sets(sets, set_count);
  return c;
}

static bool parse_prim(const char *text, int **prim_out, int *len_out) {
  char *copy = xmalloc(strlen(text) + 1);
  strcpy(copy, text);
  IntList values = {0, 0, NULL};
  char *token = strtok(copy, ",");
  while (token) {
    char *end = NULL;
    long value = strtol(token, &end, 10);
    if (*end || value < 0 || value > INT_MAX) {
      free(copy);
      free(values.items);
      return false;
    }
    list_push(&values, (int)value);
    token = strtok(NULL, ",");
  }
  free(copy);
  *prim_out = values.items;
  *len_out = values.count;
  return values.count > 1;
}

static char *read_file(const char *path) {
  FILE *file = fopen(path, "rb");
  if (!file) {
    fprintf(stderr, "error: cannot open %s: %s\n", path, strerror(errno));
    exit(1);
  }
  if (fseek(file, 0, SEEK_END) != 0) die("could not seek pattern file");
  long size = ftell(file);
  if (size < 0) die("could not size pattern file");
  rewind(file);
  char *text = xmalloc((size_t)size + 1);
  if (fread(text, 1, (size_t)size, file) != (size_t)size) die("could not read pattern file");
  text[size] = '\0';
  fclose(file);
  return text;
}

static const char *json_field(const char *text, const char *name) {
  char needle[128];
  snprintf(needle, sizeof(needle), "\"%s\"", name);
  const char *pos = strstr(text, needle);
  if (!pos) return NULL;
  pos = strchr(pos + strlen(needle), ':');
  if (!pos) return NULL;
  return pos + 1;
}

static int json_int_field(const char *text, const char *name) {
  const char *pos = json_field(text, name);
  if (!pos) die("missing integer field in pattern");
  char *end = NULL;
  long value = strtol(pos, &end, 10);
  if (pos == end || value < 0 || value > INT_MAX) die("bad integer field in pattern");
  return (int)value;
}

static i64 json_i64_field(const char *text, const char *name) {
  const char *pos = json_field(text, name);
  if (!pos) die("missing integer field in pattern");
  char *end = NULL;
  long long value = strtoll(pos, &end, 10);
  if (pos == end || value < 0) die("bad integer field in pattern");
  return value;
}

static StepList json_split_field(const char *text) {
  const char *pos = json_field(text, "split");
  if (!pos) die("missing split in pattern");
  pos = strchr(pos, '[');
  if (!pos) die("bad split in pattern");
  StepList split = {0, 0, NULL};
  pos++;
  while (*pos) {
    while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t' || *pos == ',') pos++;
    if (*pos == ']') break;
    if (*pos != '[') die("bad split step in pattern");
    pos++;
    char *end = NULL;
    long a = strtol(pos, &end, 10);
    if (end == pos || a < 1 || a > INT_MAX) die("bad a in split step");
    pos = end;
    while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t' || *pos == ',') pos++;
    long b = strtol(pos, &end, 10);
    if (end == pos || b < 1 || b > INT_MAX) die("bad b in split step");
    pos = end;
    while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t' || *pos == ',') pos++;
    long t = strtol(pos, &end, 10);
    if (end == pos || t < 1 || t > INT_MAX) die("bad t in split step");
    pos = end;
    while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t') pos++;
    if (*pos != ']') die("bad split step terminator");
    pos++;
    step_push(&split, (Step){(int)a, (int)b, (int)t});
  }
  if (split.count == 0) die("empty split in pattern");
  return split;
}

static StepList read_pattern(const char *path, int *n_out, int *r_out, i64 *value_out) {
  char *text = read_file(path);
  const char *type = strstr(text, "\"filetype\"");
  if (!type || !strstr(type, "odd_peek_pattern")) die("not an odd_peek pattern file");
  *n_out = json_int_field(text, "prime");
  *r_out = json_int_field(text, "degree");
  i64 normalized = json_i64_field(text, "normalized_value");
  *value_out = normalized * (i64)ipow_int(*n_out, *r_out - 1);
  StepList split = json_split_field(text);
  free(text);
  return split;
}

static int covered_position(const int *row, const IntList *positions, const bool *symbols) {
  for (int i = 0; i < positions->count; i++) {
    int pos = positions->items[i];
    if (symbols[row[pos]]) return pos;
  }
  return -1;
}

static int hamming_distance(const int *a, const int *b, int len) {
  int d = 0;
  for (int i = 0; i < len; i++) d += a[i] != b[i];
  return d;
}

static bool verify_rows(const int *rows, int row_count, int row_len, int required_distance) {
  for (int u = 0; u < row_count; u++) {
    for (int v = 0; v < u; v++) {
      int d = hamming_distance(rows + (size_t)u * row_len, rows + (size_t)v * row_len, row_len);
      if (d < required_distance) {
        fprintf(stderr, "Poor distance %d between %d and %d\n", d, u + 1, v + 1);
        return false;
      }
    }
  }
  return true;
}

static int write_pa(const char *path, const Construction *c, bool verify) {
  FILE *out = fopen(path, "w");
  if (!out) {
    fprintf(stderr, "error: cannot open %s: %s\n", path, strerror(errno));
    exit(1);
  }
  int q = c->field.q;
  int row_len = q + 1;
  int row_cap = verify ? 1024 : 0;
  int row_count = 0;
  int *rows = verify ? xmalloc((size_t)row_cap * row_len * sizeof(int)) : NULL;
  int *row = xmalloc((size_t)row_len * sizeof(int));
  bool *symbols = xcalloc((size_t)q, sizeof(bool));

  for (int block = 0; block < c->blocks; block++) {
    memset(symbols, 0, (size_t)q * sizeof(bool));
    for (int i = 0; i < c->q_sets[block].count; i++) symbols[c->q_sets[block].items[i]] = true;
    int multiplier = block + 1;
    for (int translate = 0; translate < q; translate++) {
      for (int x = 0; x < q; x++) row[x] = c->field.add[c->field.mul[multiplier * q + x] * q + translate];
      int pos = covered_position(row, &c->p_sets[block], symbols);
      if (pos < 0) continue;
      int displaced = row[pos];
      row[pos] = q;
      row[q] = displaced;
      for (int i = 0; i < row_len; i++) fprintf(out, "%s%d", i ? " " : "", row[i]);
      fputc('\n', out);
      if (verify) {
        if (row_count == row_cap) {
          row_cap *= 2;
          rows = xrealloc(rows, (size_t)row_cap * row_len * sizeof(int));
        }
        memcpy(rows + (size_t)row_count * row_len, row, (size_t)row_len * sizeof(int));
      }
      row_count++;
      row[pos] = displaced;
    }
  }

  int freebie_multiplier = c->blocks + 1;
  if (freebie_multiplier < q) {
    for (int translate = 0; translate < q; translate++) {
      for (int x = 0; x < q; x++) row[x] = c->field.add[c->field.mul[freebie_multiplier * q + x] * q + translate];
      row[q] = q;
      for (int i = 0; i < row_len; i++) fprintf(out, "%s%d", i ? " " : "", row[i]);
      fputc('\n', out);
      if (verify) {
        if (row_count == row_cap) {
          row_cap *= 2;
          rows = xrealloc(rows, (size_t)row_cap * row_len * sizeof(int));
        }
        memcpy(rows + (size_t)row_count * row_len, row, (size_t)row_len * sizeof(int));
      }
      row_count++;
    }
  }

  fclose(out);
  if (verify) {
    if (verify_rows(rows, row_count, row_len, q)) printf("Verified\n");
    else printf("Failed!\n");
  }
  free(rows);
  free(row);
  free(symbols);
  return row_count;
}

static void free_construction(Construction *c) {
  free_sets(c->p_sets, c->blocks);
  free_sets(c->q_sets, c->blocks);
  free_field(&c->field);
}

static void usage(const char *argv0) {
  fprintf(stderr,
    "Usage: %s Q [OPTIONS]\n"
    "\n"
    "Options:\n"
    "  -p, --peek       Only compute the bound\n"
    "  -v, --verbose    Print explanatory output\n"
    "  -n, --naive      Use the simpler full-coverage split\n"
    "  -o, --output     Choose the output file\n"
    "      --pattern F  Read an odd_peek pattern JSON instead of running DP\n"
    "      --prim LIST  Primitive polynomial coefficients, low-to-high\n"
    "      --verify     Verify pairwise Hamming distance after writing\n",
    argv0);
}

int main(int argc, char **argv) {
  const char *q_arg = NULL;
  const char *output = NULL;
  const char *prim_text = NULL;
  const char *pattern_path = NULL;
  bool peek = false;
  bool verbose = false;
  bool naive = false;
  bool verify = false;

  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--peek") == 0) {
      peek = true;
    } else if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--verbose") == 0) {
      verbose = true;
    } else if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--naive") == 0) {
      naive = true;
    } else if (strcmp(argv[i], "--verify") == 0) {
      verify = true;
    } else if (strcmp(argv[i], "--pattern") == 0) {
      if (++i >= argc) {
        usage(argv[0]);
        return 1;
      }
      pattern_path = argv[i];
    } else if (strcmp(argv[i], "-o") == 0 || strcmp(argv[i], "--output") == 0) {
      if (++i >= argc) {
        usage(argv[0]);
        return 1;
      }
      output = argv[i];
    } else if (strcmp(argv[i], "--prim") == 0) {
      if (++i >= argc) {
        usage(argv[0]);
        return 1;
      }
      prim_text = argv[i];
    } else if (!q_arg) {
      q_arg = argv[i];
    } else {
      usage(argv[0]);
      return 1;
    }
  }

  if (!q_arg && !pattern_path) {
    usage(argv[0]);
    return 1;
  }

  int n = 0;
  int r = 0;
  i64 pattern_value = 0;
  StepList pattern_split = {0, 0, NULL};
  if (pattern_path) {
    if (q_arg) die("do not pass Q when using --pattern");
    if (peek) die("--peek is not needed with --pattern");
    if (naive) die("--naive is not compatible with --pattern");
    pattern_split = read_pattern(pattern_path, &n, &r, &pattern_value);
  } else if (!parse_pr(q_arg, &n, &r)) {
    die("Q must be a prime power or P^R");
  }
  int nr = ipow_int(n, r);
  if (r % 2 == 0 || r < 3) {
    printf("%d is not an odd power of a prime\n", nr);
    return 1;
  }

  int *prim = NULL;
  int prim_len = 0;
  if (prim_text && !parse_prim(prim_text, &prim, &prim_len)) die("bad --prim list");

  if (peek) {
    i64 value;
    if (naive) {
      value = (i64)nr * ipow_int(n, r / 2);
    } else {
      SplitResult split = dp_split(n, r);
      value = split.value;
      if (verbose) {
        for (int i = 0; i < split.split.count; i++) {
          Step s = split.split.items[i];
          printf("(%d,%d,%d)%s", s.a, s.b, s.t, i + 1 == split.split.count ? "\n" : " ");
        }
      }
      free(split.split.items);
    }
    printf("M(%d, %d) >= %lld (probably)\n", nr + 1, nr, value + nr);
    free(prim);
    return 0;
  }

  Construction c;
  if (pattern_path) {
    c = theorem_from_split(n, r, &pattern_split, pattern_value, prim, prim_len, verbose);
  } else {
    c = naive ? naive_theorem(n, r, prim, prim_len, verbose) : theorem(n, r, prim, prim_len, verbose);
  }
  i64 coverage = c.coverage + nr;
  char default_output[128];
  if (!output) {
    snprintf(default_output, sizeof(default_output), "M_%d_%d_%lld.pa.txt", nr + 1, nr, coverage);
    output = default_output;
  }
  int rows = write_pa(output, &c, verify);
  printf("Used primitive polynomial:");
  for (int i = 0; i <= r; i++) printf(" %d", c.field.prim[i]);
  printf("\nComputed M(%d, %d) >= %lld.\n", nr + 1, nr, coverage);
  printf("Wrote %d rows to %s.\n", rows, output);

  free_construction(&c);
  free(prim);
  free(pattern_split.items);
  return 0;
}
