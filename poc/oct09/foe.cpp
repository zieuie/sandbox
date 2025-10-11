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
#include <string>
#include <mutex>
#include <limits.h>


//////////////////////////////////////////////////////////////////////////////
// Helper stuff
//////////////////////////////////////////////////////////////////////////////

/** My typedefs go here */
namespace fs = std::filesystem;
using namespace std;
typedef signed char num_t;
typedef vector<num_t> perm_t;
typedef vector<perm_t> pa_t;
typedef vector<vector<bool>> sep_t;
typedef vector<ssize_t> vec_ssize_t;
typedef vector<vector<vector<num_t>>> yeet_t;

std::mutex goose_mutex;
pa_t goose_pa;
ssize_t goose_score = -1;


/** Forward declaration */
void driver(int n, int d);
bool verify(const pa_t& A, int d);
void watch_file(const std::string& filename, std::atomic<bool>& running);


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

int ceildiv(int n, int d) {
  return n/d + !!(n%d);
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

void load_pa(const string& filename, pa_t& data, ssize_t& score) {
  score = -1;
  ifstream file(filename);
  if (!file) {
    cerr << "Error opening file: " << filename << endl;
    return;
  }

  data.clear();
  string line;

  while (getline(file, line)) {
    perm_t row;
    stringstream ss;

    // Extract numbers while ignoring non-numeric characters
    for (char ch : line) {
      if (ch == '#') {
        ssize_t tmp;
        if (std::sscanf(line.c_str(), "# score: %li", &tmp) == 1) {
          score = tmp;
        }
        break;
      } else if (ch == '%') {
        return;
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
}

string make_tmpfile() {
    // Get the current time point from the system clock
  auto now = std::chrono::system_clock::now();

  // Get the duration since the epoch with the clock's resolution
  auto duration = now.time_since_epoch();

  // Cast the duration to milliseconds and get the count
  auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(duration).count();

  stringstream ss;
  ss << ".temp_" << millis << "_" << rng() << ".txt";
  return ss.str();
}

void dump_pa(const pa_t& pa, const string& filename, ssize_t score) {
  string tmpfile = make_tmpfile();
  ofstream f(tmpfile);
  // ofstream f(filename);
  f << "# score: " << score << "\n";
  for (auto row : pa) {
    for (int num : row) {
      f << num << " ";
    }
    f << "\n";
  }

  // fs::copy(tmpfile, filename, fs::copy_options::overwrite_existing);
  char cmd[1024];
  sprintf(cmd, "mv %s %s", tmpfile.c_str(), filename.c_str());
  if (std::system(cmd)) {
    printf("Failed to move %s to %s\n", tmpfile.c_str(), filename.c_str());
  }
}


void dump_lut(const sep_t& lut, const string& filename) {
  string tmpfile = make_tmpfile();
  ofstream f(tmpfile);
  // ofstream f(filename);
  for (auto row : lut) {
    for (bool b : row) {
      f << (b?"1":"0");
    }
    f << "\n";
  }
  
  // fs::copy(tmpfile, filename, fs::copy_options::overwrite_existing);
  char cmd[1024];
  sprintf(cmd, "mv %s %s", tmpfile.c_str(), filename.c_str());
  if (std::system(cmd)) {
    printf("Failed to move %s to %s\n", tmpfile.c_str(), filename.c_str());
  }
}


void load_lut(const string& filename, sep_t& data) {
  ifstream file(filename);
  if (!file) {
    cerr << "Error opening file: " << filename << endl;
    return;
  }

  data.clear();
  string line;

  while (getline(file, line)) {
    vector<bool> row;

    // Extract numbers while ignoring non-numeric characters
    for (char ch : line) {
      if (ch == '1') {
        row.push_back(true);
      } else if (ch == '0') {
        row.push_back(false);
      }
    }

    if (!row.empty()) {
      data.push_back(row);
    }
  }
  printf("Loaded lut %s of size %li\n", filename.c_str(), data.size());
}


void maybe_dump(const pa_t& pa, char* filename, ssize_t score, const sep_t& foes, const sep_t& problems) {
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
  dump_lut(foes, string(".lutf_") + filename);
  dump_lut(problems, string(".lutp_") + filename);
}


/** Helper functions for manipulating PAs */

inline vector<num_t> pull_group(const perm_t& u, int n, int d, int x) {
  vector<num_t> ret;
  for (size_t i = 0; i < u.size(); i++) {
    if (u[i] / d == x) {
      ret.push_back(i);
    }
  }
  return ret;
}

inline bool separated(const perm_t& u, const perm_t& v, num_t d) {
  for (ssize_t i = 0; i < u.size(); i++) {
    if (abs(u[i] - v[i]) >= d) {
      return true;
    }
  }
  return false;
}


void init_foes(const pa_t& A, int n, int d, sep_t& foes) {
  for(int vx = 0; vx < A.size(); vx++) {
    for (int ux = 0; ux < vx; ux++) {
      bool sep = false;
      for (int z = 0; z < n; z++) {
        if (abs(A[ux][z]/d - A[vx][z]/d) > 1) {
          sep = true;
          break;
        }
      }
      if (!sep) {
        foes[ux][vx] = 1;
        foes[vx][ux] = 1;
      }
    }
  }
}


void init_problems(const pa_t& A, int d, const sep_t& foes, sep_t& problems) {
  for (size_t vx = 0; vx < A.size(); vx++) {
    for (size_t ux = 0; ux < vx; ux++) {
      if (ux != vx && foes[ux][vx] && !separated(A[ux], A[vx], d)) {
        problems[ux][vx] = 1;
        problems[vx][ux] = 1;
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
  sep_t& foes, sep_t& problems, int i, int d) {

  adders.clear();
  subers.clear();

  perm_t pot = apply_permutation(A[i], src, dst);

  for (ssize_t x = 0; x < A.size(); x++) {
    if (x == i || !foes[i][x]) {
      continue;
    }

    if (separated(pot, A[x], d)) {
      if (problems[x][i]) {
        adders.push_back(x);
      }
    } else if (!problems[x][i]) {
      subers.push_back(x);
    }
  }
}

void report_problems(const pa_t& A, const sep_t& problems) {
  for (ssize_t x = 0; x < A.size(); x++) {
    printf("%4li | ", x);
    for (auto e : A[x]) {
      printf("%d ", e);
    }

    printf("| ");
    for (ssize_t y = 0; y < A.size(); y++) {
      if (x != y && problems[x][y]) {
        printf("%li ", y);
      }
    }
    printf("\n");
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
  perm_t lows = list_range(d);

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
      random_shuffle(lows);
      int l = 0;
      int r = 0;
      perm_t t;
      for (int i = 0; i < n; i++) {
        if (find(ps.begin(), ps.end(), i) != ps.end()) {
          t.push_back(lows[l]);
          l += 1;
        } else {
          t.push_back(row[r]+d);
          r += 1;
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


void resume_computation(const num_t n, const num_t d, pa_t& pa, ssize_t& score) {
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
      pa = random_pa(n, d);
      score = -1;
      return;
    }
    printf("No existing files can support P(%d, %d). You must construct a smaller P(%d, %d) first.\n", n, d, n-d, d);
    exit(1);
    // driver(n-d, d);
    // sprintf(filename, "pa_%d_choose_%d_verified.txt", n-d, d);
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

  pa_t a;
  load_pa(filename, a, score);
  printf("Loaded from %s with score %li\n", filename, score);
  if (a.size() && a[0].size() < n) {
    auto ret = enweave(a, n, d);
    printf("Weaved\n");
    std::swap(pa, ret);
    score = -1;
    return;
  } else {
    std::swap(pa, a);
    printf("No weaving necessary\n");
  }
}


void driver(int n, int d) {
  char mid_filename[1024];
  sprintf(mid_filename, "pa_%d_choose_%d_unfinished.txt", n, d);
  char final_filename[1024];
  sprintf(final_filename, "pa_%d_choose_%d_verified.txt", n, d);

  pa_t A;
  ssize_t score;
  resume_computation(n, d, A, score);

  sep_t foes;
  sep_t problems;
  ssize_t N = A.size();
  ssize_t best_score = -1;
  ssize_t last_tweak = 0;
  if (score > 0) {
    load_lut(string(".lutf_") + mid_filename, foes);
    load_lut(string(".lutp_") + mid_filename, problems);
    best_score = score;
  } else {
    // height of the permutation array

    foes.resize(N);
    for (auto& row : foes) {
      row.resize(N);
    }
    init_foes(A, n, d, foes);

    problems.resize(N);
    for (auto& row : problems) {
      row.resize(N);
    }
    init_problems(A, d, foes, problems);

    // loop state
    score = 0;
    for (auto row : problems) {
      for (auto e : row) {
        score += e;
      }
    }
  }

  // set difference holders...
  vec_ssize_t adders, subers;
  perm_t src, dst;

  // start watching the file
  std::atomic<bool> running(true);
  std::thread watcher(watch_file, mid_filename, std::ref(running));

  // climb that hill!
  for (ssize_t it_count = 0;; it_count++) {
    // read the file maybe
    if (it_count % 1000) {
      std::lock_guard<std::mutex> lock(goose_mutex);
      if (goose_score > -1 && goose_score < score) {
        printf("Loading %li which is better than %li\n", goose_score, score);
        load_pa(mid_filename, A, score);
        load_lut(string(".lutf_") + mid_filename, foes);
        load_lut(string(".lutp_") + mid_filename, problems);
      }
    }

    // coverage
    ssize_t coverage = 0;
    for (auto row : problems) {
      int count = 0;
      for (auto e : row) {
        count += e;
      }
      if (count == 0) {
        coverage++;
      }
    }

    // is it time to print?
    bool should_print = it_count % 1000 == 0;
    // bool should_print = true;
    if (best_score < 0 || score < best_score) {
      best_score = score;
      should_print = true;
      maybe_dump(A, mid_filename, score, foes, problems);
    }

    // i'm gonna call a hundred times
    if (should_print) {
      printf("[%s] P(%d,%d) Iteration: %li Score: %li Best: %li Coverage: %li of %li Last tweak: %li\n", datetime_now().c_str(), n, d, it_count, score, best_score, coverage, N, last_tweak);
    }

    // are we really over now?
    if (score == 0 || ctrl_c_pressed) {
      report_problems(A, problems);
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
      // src = P[randrange(P.size())][i];
      src = pull_group(A[i], n, d, randrange(ceildiv(n,d)));

      // the world shall taste my eggs
      dst = src;
      random_shuffle(dst);

      // would you still love me if i were a worm?
      eval_permutation(A, src, dst, adders, subers, foes, problems, i, d);
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
    for (ssize_t x : subers) {
      problems[x][i] = 1;
      problems[i][x] = 1;
    }
    for (ssize_t x : adders) {
      problems[i][x] = 0;
      problems[x][i] = 0;
    }
  }

  running = false;
  watcher.join();

  if (score == 0 && verify(A, d)) {
    printf("Verified\n");
    dump_pa(A, final_filename, score);
  } else {
    printf("Unfinished with score %li\n", score);
    maybe_dump(A, mid_filename, score, foes, problems);
  }

  return;
}


//////////////////////////////////////////////////////////////////////////////
/** Main */
//////////////////////////////////////////////////////////////////////////////

void watch_file(const std::string& filename, std::atomic<bool>& running) {
    std::error_code ec;
    auto last_write = fs::file_time_type::min();

    while (running) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        auto current_write = fs::last_write_time(filename, ec);
        if (ec) continue;

        if (current_write != last_write) {
            last_write = current_write;
            std::lock_guard<std::mutex> lock(goose_mutex);
            load_pa(filename, goose_pa, goose_score);
            std::cout << filename << " changed, score updated to " << goose_score << "!\n";
        }
    }
}

int main(int argc, char* argv[]) {
  // Register SIGINT handler
  signal(SIGINT, signal_handler);

  // options go here
  vector<string> pots;

  // you don't need a license to drive a sandwich
  int n, d;
  char filename[1024];
  n = std::stoi(argv[1]);
  d = std::stoi(argv[2]);

  printf("[Hill Climbing] P(n,d) >= (n choose d) * P(n-d, d)\n");
  printf("[Hill Climbing] P(%d,%d) >= (%d choose %d) * P(%d, %d)\n", n, d, n, d, n-d, d);

  driver(n, d);

  return 0;
}
