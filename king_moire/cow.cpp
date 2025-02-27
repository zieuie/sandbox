#include <iostream>
#include <fstream>
#include <vector>
#include <sstream>
#include <cctype>

#include <algorithm>
#include <random>
#include <format>
#include <filesystem>
#include <chrono>
#include <atomic>
#include <csignal>
#include <thread>
#include <string.h>

//////////////////////////////////////////////////////////////////////////////
// Helper stuff
//////////////////////////////////////////////////////////////////////////////

/** My typedefs go here */
using namespace std;
typedef signed char num_t;
typedef vector<num_t> perm_t;
typedef vector<perm_t> pa_t;
typedef vector<vector<bool>> sep_t;
typedef vector<ssize_t> vec_ssize_t;
typedef vector<vector<vector<num_t>>> yoink_t;


/** Forward declaration */
void driver(int n, int d);


/** Initialization of RNG */
random_device rd;
mt19937 rng(rd());


/** Special exit logic */
std::atomic<bool> ctrl_c_pressed{false};  // Global flag

void signal_handler(int signum) {
  if (ctrl_c_pressed) {
    std::cout << "\nCtrl+C detected twice! Exiting NOW!\n";
    exit(1);
  }
  std::cout << "\nCtrl+C detected! Saving and exiting.\n";
  ctrl_c_pressed = true;  // Set the flag
}


/** Python library functions rewritten by ChatGPT go here. Forgive me */

template <typename T>
vector<vector<T>> combinations(const vector<T>& elements, int r) {
  vector<vector<T>> result;

  int n = elements.size();
  if (r > n)
    return result; // Edge case: r cannot be greater than n

  // Binary selection mask: first r elements are 1 (selected), rest are 0
  vector<bool> mask(n, false);
  fill(mask.begin(), mask.begin() + r, true);

  do {
    vector<T> combination;
    for (int i = 0; i < n; i++) {
      if (mask[i]) {
        combination.push_back(elements[i]);
      }
    }
    result.push_back(combination);
  } while (prev_permutation(mask.begin(), mask.end()));

  return result;
}


perm_t list_range(const num_t start, const num_t end) {
  perm_t ret;
  for (num_t x = start; x < end; x++) {
    ret.push_back(x);
  }
  return move(ret);
}


perm_t list_range(const num_t end) {
  return list_range(0, end);
}


void print_array(const pa_t& data) {
  for (const auto& row : data) {
    for (int num : row) {
      cout << num << " ";
    }
    cout << "\n";
  }
}

void print_yoink(const yoink_t& P) {
  if (P.size() < 1) {
    return;
  }

  for (ssize_t x = 0; x < P[0].size(); x++) {
    for (auto block : P) {
      printf("[ ");
      for (auto e : block[x]) {
        printf("%d ", e);
      }
      printf("] ");
    }
    cout << endl;
  }
}


template <typename T>
void random_shuffle(vector<T>& data) {
  shuffle(data.begin(), data.end(), rng);
}


string datetime_now() {
  auto end = chrono::system_clock::now();
  time_t end_time = chrono::system_clock::to_time_t(end);
  string ret = ctime(&end_time);
  ret.resize(ret.size() - 1);
  return ret;
}

int randrange(int stop) {
  uniform_int_distribution<int> dist(0, stop - 1);
  return dist(rng);
}


/** Helper functions for loading files */

pa_t load_pa(const string& filename) {
  ifstream file(filename);
  if (!file) {
    cerr << "Error opening file: " << filename << endl;
    return {};
  }

  pa_t data;
  string line;

  while (getline(file, line)) {
    perm_t row;
    stringstream ss;

    // Extract numbers while ignoring non-numeric characters
    for (char ch : line) {
      if (ch == '#') {
        break;
      } else if (isdigit(ch) || ch == '-' || ch == ' ') {
        ss << ch;
      } else {
        ss << ' '; // Replace garbage with space to separate numbers
      }
    }

    ssize_t num;
    while (ss >> num) {
      row.push_back(num);
    }

    if (!row.empty()) {
      data.push_back(row);
    }
  }

  return data;
}


void dump_pa(const pa_t& pa, const string& filename) {
  ofstream f(filename);
  for (auto row : pa) {
    for (int num : row) {
      f << num << " ";
    }
    f << "\n";
  }
}


/** Helper functions for manipulating PAs */

inline bool separated(perm_t u, perm_t v, num_t d) {
  for (ssize_t i = 0; i < u.size(); i++) {
    if (abs(u[i] - v[i]) >= d) {
      return true;
    }
  }
  return false;
}


void init_separations(const pa_t& A, int d, sep_t& s) {
  for (size_t vx = 0; vx < A.size(); vx++) {
    for (size_t ux = 0; ux < vx; ux++) {
      if (ux != vx && separated(A[ux], A[vx], d)) {
        s[ux][vx] = 1;
        s[vx][ux] = 1;
      }
    }
  }
}


perm_t apply_permutation(const perm_t& perm, const perm_t& src, const perm_t& dst) {
  perm_t ret = perm;

  for (size_t i = 0; i < src.size(); i++) {
    ret[src[i]] = perm[dst[i]];
  }

  return ret;
}


void eval_permutation(
  const pa_t& A,
  const perm_t& src,
  const perm_t& dst,
  vec_ssize_t& adders, vec_ssize_t& subers,
  sep_t& s, int i, int d) {

  adders.clear();
  subers.clear();

  perm_t pot = apply_permutation(A[i], src, dst);

  for (ssize_t x = 0; x < A.size(); x++) {
    if (x == i) {
      continue;
    }

    if (separated(pot, A[x], d)) {
      if (!s[x][i]) {
        adders.push_back(x);
      }
    } else if (s[x][i]) {
      subers.push_back(x);
    }
  }
}

void report_s(const pa_t& A, const sep_t& s) {
  for (ssize_t x = 0; x < A.size(); x++) {
    printf("%4li | ", x);
    for (auto e : A[x]) {
      printf("%d ", e);
    }

    printf("| ");
    for (ssize_t y = 0; y < A.size(); y++) {
      if (x != y && !s[x][y]) {
        printf("%li ", y);
      }
    }
    printf("\n");
  }
}

void hill_climb(pa_t& A, yoink_t& P, num_t n, num_t d) {
  // height of the permutation array
  ssize_t N = A.size();

  // number of pairs in permutation array
  ssize_t W = N * (N - 1);

  // separation lookup table
  sep_t s;
  s.resize(N);
  for (auto& row : s) {
    row.resize(N);
  }
  init_separations(A, d, s);
  // report_s(A, s);

  // set difference holders...
  vec_ssize_t adders, subers;
  perm_t src, dst;

  // loop state
  ssize_t best_score = W;
  ssize_t last_tweak = 0;
  ssize_t score = W;
  for (auto row : s) {
    for (auto e : row) {
      score -= e;
    }
  }

  // climb that hill!
  for (ssize_t it_count = 0;; it_count++) {
    // coverage
    ssize_t coverage = 0;
    for (auto row : s) {
      int count = 0;
      for (auto e : row) {
        count += e;
      }
      if (count == N - 1) {
        coverage++;
      }
    }

    // is it time to print?
    bool should_print = it_count % 1000 == 0;
    // bool should_print = true;
    if (score < best_score) {
      best_score = score;
      should_print = true;
      // todo backup pa
    }

    // i'm gonna call a hundred times
    if (should_print) {
      // report_s(A, s);
      printf("[%s] P(%d,%d) Iteration: %li Score: %li Best: %li Coverage: %li of %li Last tweak: %li\n", datetime_now().c_str(), n, d, it_count, score, best_score, coverage, N, last_tweak);
      // printf("%s Iteration: %li Score: %li Best: %li Coverage %li of %li Last tweak: %li\n", datetime_now().c_str(), it_count, score, best_score, coverage, N, last_tweak);
      // string usr;
      // cin >> usr;
    }

    // are we really over now?
    if (score == 0 || ctrl_c_pressed) {
      report_s(A, s);
      return;
    }

    // maybe i can change your mind
    bool force = false;
    if ((score == best_score && it_count - last_tweak > 10000) ||
        (score > best_score && it_count - last_tweak > 50000)) {
      force = true;
    }

    // live a lie you like
    ssize_t i;
    for (ssize_t tries = 0; ; tries++) {
      // pick a random row to improve
      i = randrange(N);

      // pick a random group of digits to terrorize
      src = P[randrange(P.size())][i];

      // the world shall taste my eggs
      dst = src;
      random_shuffle(dst);

      // would you still love me if i were a worm?
      eval_permutation(A, src, dst, adders, subers, s, i, d);
      if (adders.size() > subers.size()) {
        // improvement, great!
        // printf("tries %li\n", tries);
        last_tweak = it_count;
        break;
      } else if (adders.size() == subers.size() && tries > 100 && !force) {
        // wandering, sure!
        break;
      } else if (tries > 100000) {
        printf("backtracking...\n");
        last_tweak = it_count;
        break;
      } else if (force) {
        // backtracking, maybe.
        printf("backtracking forced... %li %li\n", it_count, last_tweak);
        last_tweak = it_count;
        break;
      }

    }

    perm_t row = apply_permutation(A[i], src, dst);
    A[i] = row;
    score -= 2*(adders.size() - subers.size());
    for (ssize_t x : adders) {
      s[x][i] = 1;
      s[i][x] = 1;
    }
    for (ssize_t x : subers) {
      s[i][x] = 0;
      s[x][i] = 0;
    }

  }
}


bool verify(const pa_t& A, int d) {
  for (ssize_t vx = 0; vx < A.size(); vx++) {
    for (ssize_t ux = 0; ux < vx; ux++) {
      if (!separated(A[ux], A[vx], d)) {
        return false;
      }
    }
  }
  return true;
}


//////////////////////////////////////////////////////////////////////////////
/** Turn (n-d,d) into (n,d) by putting the top (n-d to n) symbols into the (n choose d) positions */
//////////////////////////////////////////////////////////////////////////////

pa_t enweave(pa_t const& A, const num_t n, const num_t d) {
  pa_t ret;
  perm_t highs = list_range(n - d, n);

  auto elements = list_range(n);
  // Binary selection mask: first r elements are 1 (selected), rest are 0
  vector<bool> mask(n, false);
  fill(mask.begin(), mask.begin() + d, true);

  vector<ssize_t> ps;
  do {
    ps.clear();
    for (int i = 0; i < n; i++) {
      if (mask[i]) {
        ps.push_back(elements[i]);
      }
    }
    // result push back

    for (perm_t row : A) {
      random_shuffle(highs);
      int l = 0;
      int h = 0;
      perm_t t;
      for (int i = 0; i < n; i++) {
        if (find(ps.begin(), ps.end(), i) != ps.end()) {
          t.push_back(highs[h]);
          h += 1;
        } else {
          t.push_back(row[l]);
          l += 1;
        }
      }
      ret.push_back(t);
    }

  } while (prev_permutation(mask.begin(), mask.end()));

  return ret;
}


pa_t random_pa(const num_t n, const num_t d) {
  pa_t pot;
  perm_t row = list_range(0, n-d);
  random_shuffle(row);
  pot.push_back(row);
  return enweave(pot, n, d);
}


pa_t resume_computation(const num_t n, const num_t d) {
  vector<string> pots;

  char filename[1024];
  sprintf(filename, "pa_%d_choose_%d_verified.txt", n, d);
  if (filesystem::exists(filename)) {
    printf("This PA already exists and is verified!");
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_choose_%d_unfinished.txt", n, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_choose_%d.txt", n, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_choose_%d_unfinished.txt", n-d, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_choose_%d.txt", n-d, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_choose_%d_verified.txt", n-d, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  if (0 == pots.size()) {
    if (n-d <= d) {
      printf("For n <= 2d, we generate a PA at random.\n");
      return random_pa(n, d);
    }
    printf("No existing files can support P(%d, %d). Constructing a smaller PA first.\n", n, d);
    driver(n-d, d);
    sprintf(filename, "pa_%d_choose_%d_verified.txt", n-d, d);
  } else if (1 == pots.size()) {
    strcpy(filename, pots[0].c_str());
  } else {
    printf("\n%li files could be used as the seed for computing P(%d, %d).\n", pots.size(), n, d);
    int usr = -1;
    while (! (0 < usr && usr <= pots.size())) {
      for (ssize_t x = 0; x < pots.size(); x++) {
        printf("  %3li: %s\n", x+1, pots[x].c_str());
      }
      printf("Choose a file from the list: ");

      if (scanf("%d", &usr) != 1) {
        int c;
        while ((c = getchar()) != '\n' && c != EOF);
      }
    }
    strcpy(filename, pots[usr-1].c_str());
  }

  auto a = load_pa(filename);
  printf("Loaded from %s\n", filename);
  if (a.size() && a[0].size() < n) {
    auto ret = enweave(a, n, d);
    printf("Weaved\n");
    return ret;
  } else {
    printf("No weaving necessary\n");
    return a;
  }
}


yoink_t yoink_columns(const pa_t& A, int n, int d) {
  num_t twists = n / d;
  num_t offset = n%d;
  vector<vector<vector<num_t>>> ret(twists);

  for (const auto& row : A) {
    vector<vector<num_t>> buckets(twists);

    for (size_t i = 0; i < row.size(); i++) {
      num_t e = row[i];
      buckets[max(0, e-offset) / d].push_back(i);
    }

    for (size_t j = 0; j < twists; j++) {
      ret[j].push_back(buckets[j]);
    }
  }

  return ret;
}


void save_pa(const pa_t& pa, int n, int d, bool verified) {
  char filename[1024];
  if (verified) {
    sprintf(filename, "pa_%d_choose_%d_verified.txt", n, d);
    printf("Verified %s\n", filename);
  } else {
    sprintf(filename, "pa_%d_choose_%d_unfinished.txt", n, d);
    printf("Failed to verify %s\n", filename);
  }
  dump_pa(pa, filename);
}


void driver(int n, int d) {
  auto pa = resume_computation(n, d);
  yoink_t P = yoink_columns(pa, n, d);
  hill_climb(pa, P, n, d);

  char filename[1024];
  bool verified = verify(pa, d);
  save_pa(pa, n, d, verified);
}


//////////////////////////////////////////////////////////////////////////////
/** Turn (n-2,d-1) into (n,d) by bumping everything up by one, adding a new 0 and n-1 into the (n choose 2) positions */
//////////////////////////////////////////////////////////////////////////////
// Epecially along the (2d, d), (6,3), (8,4), etc.
//   2n choose n upper.
//   12,6 done for lower. 14,7 is 2805 to 3432, 16,8 9379 to 12870

// A is an (n-2,d)-PA.
pa_t enweave2(pa_t const& A, const num_t n, const num_t d) {
  pa_t ret;

  for (ssize_t zero_idx = 0; zero_idx < n; zero_idx++) {
    for (ssize_t high_idx = zero_idx+1; high_idx < n; high_idx++) {
      for (perm_t row : A) {
        int l = 0;
        perm_t t;
        for (int i = 0; i < n; i++) {
          if (i == zero_idx) {
            t.push_back(0);
          } else if (i == high_idx) {
            t.push_back(n-1);
          } else {
            t.push_back(1+row[l]);
            l += 1;
          }
        }
        ret.push_back(t);
      }
    }
  }

  return ret;
}


pa_t resume_computation2(const num_t n, const num_t d) {
  vector<string> pots;

  char filename[1024];
  sprintf(filename, "pa_%d_%d_plus_2_verified.txt", n, d);
  if (filesystem::exists(filename)) {
    printf("This PA already exists and is verified!");
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_%d_plus_2_unfinished.txt", n, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_choose_%d_verified.txt", n-2, d-1);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_%d.txt", n-2, d-1);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  if (0 == pots.size()) {
    printf("No existing files can support P(%d, %d).\n", n, d);
    exit(1);
  } else if (1 == pots.size()) {
    strcpy(filename, pots[0].c_str());
  } else {
    printf("\n%li files could be used as the seed for computing P(%d, %d).\n", pots.size(), n, d);
    int usr = -1;
    while (! (0 < usr && usr <= pots.size())) {
      for (ssize_t x = 0; x < pots.size(); x++) {
        printf("  %3li: %s\n", x+1, pots[x].c_str());
      }
      printf("Choose a file from the list: ");

      if (scanf("%d", &usr) != 1) {
        int c;
        while ((c = getchar()) != '\n' && c != EOF);
      }
    }
    strcpy(filename, pots[usr-1].c_str());
  }

  auto a = load_pa(filename);
  printf("Loaded from %s\n", filename);
  if (a.size() && a[0].size() < n) {
    auto ret = enweave2(a, n, d);
    printf("Weaved\n");
    return ret;
  } else {
    printf("No weaving necessary\n");
    return a;
  }
}


yoink_t yoink_columns2(const pa_t& A, int n, int d) {
  num_t twists = 3;
  // num_t offset = n%d;
  vector<vector<vector<num_t>>> ret(twists);

  for (const auto& row : A) {
    vector<vector<num_t>> buckets(twists);

    for (size_t i = 0; i < row.size(); i++) {
      num_t e = row[i];
      if (e == 0 || e == n-1) {
        buckets[3].push_back(i);
      } else {
        buckets[(e-1) / (d-1)].push_back(i);
      }
    }

    for (size_t j = 0; j < twists; j++) {
      ret[j].push_back(buckets[j]);
    }
  }

  return ret;
}


void save_pa2(const pa_t& pa, int n, int d, bool verified) {
  char filename[1024];
  if (verified) {
    sprintf(filename, "pa_%d_%d_plus_2_verified.txt", n, d);
    printf("Verified %s\n", filename);
  } else {
    sprintf(filename, "pa_%d_%d_plus_2_unfinished.txt", n, d);
    printf("Failed to verify %s\n", filename);
  }
  dump_pa(pa, filename);
}


void driver2(int n, int d) {
  auto pa = resume_computation2(n, d);
  yoink_t P = yoink_columns2(pa, n, d);
  hill_climb(pa, P, n, d);

  char filename[1024];
  bool verified = verify(pa, d);
  save_pa2(pa, n, d, verified);
}


//////////////////////////////////////////////////////////////////////////////
/** Construct (2k+1, k+1) as (2k+1 choose k) settings of lower symbols */
//////////////////////////////////////////////////////////////////////////////


pa_t random_pa3(const num_t n, const num_t d) {
  pa_t ret;
  perm_t lows = list_range(0, d-1);
  perm_t highs = list_range(d, n);

  auto elements = list_range(n);
  // Binary selection mask: first r elements are 1 (selected), rest are 0
  vector<bool> mask(n, false);
  fill(mask.begin(), mask.begin() + d, true);

  vector<ssize_t> ps;
  do {
    ps.clear();
    for (int i = 0; i < n; i++) {
      if (mask[i]) {
        ps.push_back(elements[i]);
      }
    }

    random_shuffle(lows);
    random_shuffle(highs);
    int l = 0;
    int h = 0;
    // ssize_t m = ret.size() % d;
    perm_t t;
    for (int i = 0; i < n; i++) {
      if (find(ps.begin(), ps.end(), i) != ps.end()) {
        t.push_back(h ? highs[h-1] : d-1);
        h += 1;
      } else {
        // t.push_back(0);
        t.push_back(lows[l]);
        l += 1;
      }
    }
    ret.push_back(t);

  } while (prev_permutation(mask.begin(), mask.end()));

  return ret;
}


pa_t resume_computation3(const num_t n, const num_t d) {
  vector<string> pots;
  pots.push_back("randomly generated");

  char filename[1024];
  sprintf(filename, "pa_%d_%d_experimental_verified.txt", n, d);
  if (filesystem::exists(filename)) {
    printf("This PA already exists and is verified!");
    pots.push_back(filename);
  }

  sprintf(filename, "pa_%d_%d_experimental_unfinished.txt", n, d);
  if (filesystem::exists(filename)) {
    pots.push_back(filename);
  }

  int usr = -1;
  if (1 == pots.size()) {
    usr = 1;
  } else {
    printf("\n%li files could be used as the seed for computing P(%d, %d).\n", pots.size(), n, d);
    while (! (0 < usr && usr <= pots.size())) {
      for (ssize_t x = 0; x < pots.size(); x++) {
        printf("  %3li: %s\n", x+1, pots[x].c_str());
      }
      printf("Choose a file from the list: ");

      if (scanf("%d", &usr) != 1) {
        int c;
        while ((c = getchar()) != '\n' && c != EOF);
      }
    }
    strcpy(filename, pots[usr-1].c_str());
  }

  printf("Loading from %s\n", filename);
  if (usr != 1) {
    return load_pa(filename);
  } else {
    return random_pa3(n, d);
  }
}


yoink_t yoink_columns3(const pa_t& A, int n, int d) {
  num_t twists = 2;
  // num_t offset = n%d;
  vector<vector<vector<num_t>>> ret(twists);

  for (const auto& row : A) {
    vector<vector<num_t>> buckets(twists);

    for (size_t i = 0; i+1 < row.size(); i++) {
      num_t e = row[i];
      if (e < d-1) {
      // if (e < d) {
        buckets[0].push_back(i);
      } else if (e >= d) {
        buckets[1].push_back(i);
      }
      // That's right! We don't use e=d
    }

    for (size_t j = 0; j < twists; j++) {
      ret[j].push_back(buckets[j]);
    }
  }

  return ret;
}


void save_pa3(const pa_t& pa, int n, int d, bool verified) {
  char filename[1024];
  if (verified) {
    sprintf(filename, "pa_%d_%d_experimental_verified.txt", n, d);
    printf("Verified %s\n", filename);
  } else {
    sprintf(filename, "pa_%d_%d_experimental_unfinished.txt", n, d);
    printf("Failed to verify %s\n", filename);
  }
  dump_pa(pa, filename);
}


void driver3(int n, int d) {
  auto pa = resume_computation3(n, d);
  yoink_t P = yoink_columns3(pa, n, d);
  // print_array(pa);
  // exit(1);
  hill_climb(pa, P, n, d);

  char filename[1024];
  bool verified = verify(pa, d);
  save_pa3(pa, n, d, verified);
}


//////////////////////////////////////////////////////////////////////////////
/** Main */
//////////////////////////////////////////////////////////////////////////////

int main(int argc, char* argv[]) {
  // Register SIGINT handler
  signal(SIGINT, signal_handler);

  // parse args
  int n = std::stoi(argv[1]);
  int d = std::stoi(argv[2]);

  // options go here
  vector<string> pots;
  pots.push_back("P(n,d) >= (n choose d) * P(n-d, d)");
  pots.push_back("P(n,d) >= (n choose 2) * P(n-2, d-1)");
  pots.push_back("P(9,5) >= (9 choose 5)");

  // make the user pick an option
  int usr = -1;
  while (! (0 < usr && usr <= pots.size())) {
    for (ssize_t x = 0; x < pots.size(); x++) {
      printf("  %3li: %s\n", x+1, pots[x].c_str());
    }
    printf("Choose an option from the list: ");

    if (scanf("%d", &usr) != 1) {
      int c;
      while ((c = getchar()) != '\n' && c != EOF);
    }
  }

  printf("You chose %s\n", pots[usr-1].c_str());
  // you don't need a license to drive a sandwich
  switch(usr) {
    case 1:
      driver(n, d);
      break;
    case 2:
      if (2*d != n) {
        printf("FATAL: You must have n = 2d.\n");
        exit(1);
      }
      driver2(n, d);
      break;
    case 3:
    if (2*d-1 != n) {
      printf("FATAL: You must have n = 2d-1.\n");
      exit(1);
    }
    driver3(n, d);
      break;
  }

  return 0;
}
