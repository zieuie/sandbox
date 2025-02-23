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

/** My typedefs go here */
using namespace std;
typedef signed char num_t;
typedef vector<num_t> perm_t;
typedef vector<perm_t> pa_t;
typedef vector<vector<bool>> sep_t;
typedef vector<ssize_t> vec_ssize_t;

random_device rd; // Seed source
mt19937 rng(rd());  // Random number generator

/** Special exit logic */
std::atomic<bool> ctrlCPressed{false};  // Global flag

void signalHandler(int signum) {
  if (ctrlCPressed) {
    exit(1);
  }
  std::cout << "\nCtrl+C detected! Saving and exiting.\n";
  ctrlCPressed = true;  // Set the flag
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

perm_t listRange(const num_t start, const num_t end) {
  perm_t ret;
  for (num_t x = start; x < end; x++) {
    ret.push_back(x);
  }
  return move(ret);
}

perm_t listRange(const num_t end) {
  return listRange(0, end);
}

void printArray(const pa_t& data) {
  for (const auto& row : data) {
    for (int num : row) {
      cout << num << " ";
    }
    cout << "\n";
  }
}

template <typename T>
void randomShuffle(vector<T>& data) {
  shuffle(data.begin(), data.end(), rng);
}


string datetimeNow() {
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

/** Helper functions for loading files*/

pa_t enweave(pa_t const& A, const num_t n, const num_t d) {
  pa_t ret;
  perm_t highs = listRange(n - d, n);

  auto elements = listRange(n);
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
      randomShuffle(highs);
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

pa_t loadPa(const string& filename) {
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

pa_t loadPa2(const num_t n, const num_t d) {
  char filename[1024];
  sprintf(filename, "pa_%d_choose_%d_unfinished.txt", n, d);
  if (filesystem::exists(filename)) {
    printf("Resuming unfinished file\n");
    return loadPa(filename);
  }

  sprintf(filename, "pa_%d_choose_%d.txt", n, d);
  if (filesystem::exists(filename)) {
    printf("Resuming unfinished file\n");
    return loadPa(filename);
  }

  sprintf(filename, "pa_%d_choose_%d_verified.txt", n - d, d);
  printf("Loading from smaller file\n");
  auto a = loadPa(filename);
  printf("Loaded\n");
  auto ret = enweave(a, n, d);
  printf("Weaved\n");
  return ret;
}


/** Helper functions for manipulating PAs */

vector<vector<vector<num_t>>> yoink_columns(const pa_t& A, int n, int d) {
  num_t twists = n / d;
  vector<vector<vector<num_t>>> ret(twists);

  for (const auto& row : A) {
    vector<vector<num_t>> buckets(twists);

    for (size_t i = 0; i < row.size(); i++) {
      num_t e = row[i];
      buckets[e / d].push_back(i);
    }

    for (size_t j = 0; j < twists; j++) {
      ret[j].push_back(buckets[j]);
    }
  }

  return ret;
}

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

void hill_climb(pa_t& A, num_t n, num_t d) {
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

  // weird setup of course
  auto P = yoink_columns(A, n, d);

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
    // // coverage
    // ssize_t coverage = 0;
    // for (auto row : s) {
    //   int count = 0;
    //   for (auto e : row) {
    //     count += e;
    //   }
    //   if (count == N - 1) {
    //     coverage++;
    //   }
    // }

    // is it time to print?
    bool should_print = it_count % 100 == 0;
    // bool should_print = true;
    if (score < best_score) {
      best_score = score;
      should_print = true;
      // todo backup pa
    }

    // i'm gonna call a hundred times
    if (should_print) {
      printf("%s Iteration: %li Score: %li Best: %li Last tweak: %li\n", datetimeNow().c_str(), it_count, score, best_score, last_tweak);
      // printf("%s Iteration: %li Score: %li Best: %li Coverage %li of %li Last tweak: %li\n", datetimeNow().c_str(), it_count, score, best_score, coverage, N, last_tweak);
    }

    // are we really over now?
    if (score == 0 || ctrlCPressed) {
      return;
    }

    // maybe i can change your mind
    bool force = false;
    if (score >= best_score and it_count - last_tweak > 10000) {
      force = true;
    }

    // live a lie you like
    ssize_t i;
    for (ssize_t tries = 0; ; tries++) {
      // pick a random row to improve
      i = randrange(N);

      // pick a random group of digits to terrorize
      src = P[randrange(P.size())][i];

      // scramble the eggs
      dst = src;
      randomShuffle(dst);

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
      } else if (tries > 100000 or force) {
        // backtracking, maybe.
        printf("backtracking...\n");
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
      s[i][x] = 1;
      s[x][i] = 1;
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


int main(int argc, char* argv[]) {
  signal(SIGINT, signalHandler);  // Register SIGINT handler

  int n = std::stoi(argv[1]);
  int d = std::stoi(argv[2]);
  auto pa = loadPa2(n, d);
  hill_climb(pa, n, d);

  char filename[1024];
  if (verify(pa, d)) {
    sprintf(filename, "pa_%d_choose_%d_verified.txt", n, d);
    printf("Verified %s\n", filename);
  } else {
    sprintf(filename, "pa_%d_choose_%d_unfinished.txt", n, d);
    printf("Failed to verify %s\n", filename);
  }

  ofstream f(filename);
  for (auto row : pa) {
    for (int num : row) {
      f << num << " ";
    }
    f << "\n";
  }

  return 0;
}
