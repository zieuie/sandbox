#include <iostream>
#include <fstream>
#include <vector>
#include <sstream>
#include <cctype>

#include <algorithm>
#include <random>
#include <format>
#include <filesystem>
#include <unordered_set>
#include <set>

/** My typedefs go here */
using namespace std;
typedef int num_t;
typedef vector<num_t> perm_t;
typedef vector<perm_t> pa_t;

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

void printArray(const vector<vector<int>>& data) {
  for (const auto& row : data) {
    for (int num : row) {
      cout << num << " ";
    }
    cout << "\n";
  }
}

template <typename T>
void randomShuffle(vector<T>& data) {
  random_device rd; // Seed source
  mt19937 g(rd());  // Random number generator
  shuffle(data.begin(), data.end(), g);
}

/** Helper functions for loading files*/

pa_t enweave(pa_t const& A, const num_t n, const num_t d) {
  pa_t ret;
  perm_t highs = listRange(n - d, n);

  for (perm_t row : A) {
    for (vector<int> ps : combinations(listRange(n), d)) {
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
  }
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

    num_t num;
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
  string filename = format("pa_{}_choose_{}_unfinished.txt", n, d);
  if (filesystem::exists(filename)) {
    printf("Loaded from unfinished file\n");
    return loadPa(filename);
  }

  filename = format("pa_{}_choose_{}_verified.txt", n - d, d);
  printf("Loaded from smaller file\n");
  return enweave(loadPa(filename), n, d);
}

/** Helper functions for manipulating PAs */

vector<vector<vector<int>>> yoink_columns(const pa_t& A, int n, int d) {
  int twists = n / d;
  vector<vector<vector<int>>> ret(twists);

  for (const auto& row : A) {
    vector<vector<int>> buckets(twists);

    for (size_t i = 0; i < row.size(); i++) {
      int e = row[i];
      buckets[e / d].push_back(i);
    }

    for (size_t j = 0; j < twists; j++) {
      ret[j].push_back(buckets[j]);
    }
  }

  return ret;
}

inline bool separated(perm_t u, perm_t v, num_t d) {
  num_t dd = d * d;
  for (ssize_t i = 0; i < u.size(); i++) {
    if (pow(u[i] - v[i], 2) >= dd) {
      return true;
    }
  }
  return false;
}

std::vector<std::unordered_set<int>> init_separations(const pa_t& A, int d) {
  std::vector<std::unordered_set<int>> s(A.size());

  for (size_t vx = 0; vx < A.size(); vx++) {
    for (size_t ux = 0; ux < vx; ux++) {
      if (ux != vx && separated(A[ux], A[vx], d)) {
        s[ux].insert(vx);
        s[vx].insert(ux);
      }
    }
  }

  return s;
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
  perm_t news, perm_t adders, perm_t subers,
  std::vector<std::unordered_set<int>>& s,
  int i, int d) {

    news.clear();
  adders.clear();
  subers.clear();

  perm_t pot = apply_permutation(A[i], src, dst);

  for (size_t x = 0; x < A.size(); x++) {
    if (x == i)
      continue;

    if (separated(pot, A[x], d)) {
      if (s[i].find(x) == s[i].end()) {
        news.push_back(x);
      }
      if (s[x].find(i) == s[x].end()) {
        adders.push_back(x);
      }
    } else if (s[i].find(x) != s[i].end()) {
      subers.push_back(x);
    }
  }
}

int main(int argc, char* argv[]) {
  // auto data = readNumbersFromFile(argv[1]);
  // cout << "Extracted Numbers:\n";
  auto data = loadPa2(9, 3);
  printArray(data);

  return 0;
}
