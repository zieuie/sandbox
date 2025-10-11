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
typedef vector<vector<num_t>> yoink_row_t;
typedef vector<yoink_row_t> yoink_t;
typedef vector<vector<vector<num_t>>> yeet_t;


/** Forward declaration */
void driver(int n, int d);
bool verify(const pa_t& A, int d);


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
      } else if (ch == '%') {
        return data;
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


void dump_pa(const pa_t& pa, const string& filename, ssize_t score) {
  ofstream f(filename);
  f << "# score: " << score << "\n";
  for (auto row : pa) {
    for (int num : row) {
      f << num << " ";
    }
    f << "\n";
  }
  // printf("Saved to %s\n", filename.c_str());
}


void maybe_dump(const pa_t& pa, char* filename, ssize_t score) {
  if (filename == NULL) {
    return;
  }
  ifstream infile(filename);

  ssize_t old_score = 0;
  string sLine;
  infile.good() && getline(infile, sLine) && sscanf(sLine.c_str(), "# score: %li", &old_score);

  // don't dump
  if (0 < old_score && old_score < score) {
    return;
  }

  // do dump
  dump_pa(pa, filename, score);
}


/** Helper functions for manipulating PAs */

inline bool separated(const perm_t& u, const perm_t& v, num_t d) {
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

void hill_climb(pa_t& A, yoink_t& P, num_t n, num_t d, char* mid_filename, char* final_filename) {
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
      maybe_dump(A, mid_filename, score);
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
      break;
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


  if (score == 0 && verify(A, d)) {
    printf("Verified\n");
    dump_pa(A, final_filename, score);
  } else {
    printf("Unfinished with score %li\n", score);
    maybe_dump(A, mid_filename, score);
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
/**  */
//////////////////////////////////////////////////////////////////////////////

// pa_t random_pa(const num_t n, const num_t d) {
//   pa_t pot;
//   perm_t row = list_range(0, n-d);
//   random_shuffle(row);
//   pot.push_back(row);
//   return enweave(pot, n, d);
// }


pa_t random_pa(const num_t n, const num_t d) {
  pa_t pot;
  perm_t row = list_range(0, n-d);
  random_shuffle(row);
  pot.push_back(row);
  return enweave(pot, n, d);
}


pa_t resume_computation(const num_t n, const num_t d, const num_t r) {
  vector<string> pots;

  char filename[1024];
  sprintf(filename, "pa_%d_%d_group_by_%d_unfinished.txt", n, d, r);
  if (filesystem::exists(filename)) {
    printf("Loading %s\n", filename);
    return load_pa(filename);
  }

  printf("Generating %d %d group by %d at random.\n", n, d, r);
  return random_pa(n, d, r);
}


yoink_t yoink_columns(const pa_t& A, int n, int d) {
  num_t twists = n / d;
  num_t offset = (d - (n%d)) % d;
  if (offset) {
    twists += 1;
  }
  vector<vector<vector<num_t>>> ret(twists);

  for (const auto& row : A) {
    vector<vector<num_t>> buckets(twists);

    for (size_t i = 0; i < row.size(); i++) {
      num_t e = row[i];
      buckets[(e+offset) / d].push_back(i);
    }

    for (size_t j = 0; j < twists; j++) {
      ret[j].push_back(buckets[j]);
    }
  }

  return ret;
}


void driver(int n, int d, int r) {
  char mid_filename[1024];
  sprintf(mid_filename, "pa_%d_%d_group_by_%d_unfinished.txt", n, d, r);
  char final_filename[1024];
  sprintf(final_filename, "pa_%d_%d_group_by_%d_verified.txt", n, d, r);

  auto pa = resume_computation(n, d, r);
  yoink_t P = yoink_columns(pa, n, d);

  hill_climb(pa, P, n, d, mid_filename, final_filename);
}


//////////////////////////////////////////////////////////////////////////////
/** Main */
//////////////////////////////////////////////////////////////////////////////

int main(int argc, char* argv[]) {
  // Register SIGINT handler
  signal(SIGINT, signal_handler);

  if (argc < 4) {
    printf("Usage: ./a.out N D R\n");
  }

  // you don't need a license to drive a sandwich
  int n, d;
  n = std::stoi(argv[1]);
  d = std::stoi(argv[2]);
  r = std::stoi(argv[3]);
  driver(n, d, r);
  return 0;
}
