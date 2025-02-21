#include <iostream>
#include <fstream>
#include <vector>
#include <sstream>
#include <cctype>

#include <iostream>
#include <vector>
#include <algorithm>
#include <random>
#include <format>
#include <filesystem>


/** My typedefs go here */
using namespace std;
typedef int num_t;
typedef vector<num_t> perm_t;
typedef vector<perm_t> pa_t;


/** Python library functions rewritten by ChatGPT go here. Forgive me */

template <typename T>
std::vector<std::vector<T>> combinations(const std::vector<T>& elements, int r) {
    std::vector<std::vector<T>> result;

    int n = elements.size();
    if (r > n) return result;  // Edge case: r cannot be greater than n

    // Binary selection mask: first r elements are 1 (selected), rest are 0
    std::vector<bool> mask(n, false);
    std::fill(mask.begin(), mask.begin() + r, true);

    do {
        std::vector<T> combination;
        for (int i = 0; i < n; i++) {
            if (mask[i]) {
                combination.push_back(elements[i]);
            }
        }
        result.push_back(combination);
    } while (std::prev_permutation(mask.begin(), mask.end()));

    return result;
}


perm_t listRange(const num_t start, const num_t end) {
  perm_t ret;
  for (num_t x = start; x < end; x++) {
    ret.push_back(x);
  }
  return std::move(ret);
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
void randomShuffle(std::vector<T>& data) {
    std::random_device rd;  // Seed source
    std::mt19937 g(rd());   // Random number generator
    std::shuffle(data.begin(), data.end(), g);
}


/** Helper functions */


pa_t enweave(pa_t const& A, const num_t n, const num_t d) {
  pa_t ret;
  perm_t highs = listRange(n-d, n);

  for (perm_t row : A) {
    for (vector<int> ps : combinations(listRange(n), d)) {
      randomShuffle(highs);
      int l = 0;
      int h = 0;
      perm_t t;
      for (int i = 0; i < n; i++) {
        if (std::find(ps.begin(), ps.end(), i) != ps.end()) {
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
  
  filename = format("pa_{}_choose_{}_verified.txt", n-d, d);
  printf("Loaded from smaller file\n");
  return enweave(loadPa(filename), n, d);
}




int main(int argc, char* argv[]) {
  if (argc != 2) {
    cerr << "Usage: " << argv[0] << " <filename>\n";
    return 1;
  }

  // auto data = readNumbersFromFile(argv[1]);
  // cout << "Extracted Numbers:\n";
  auto data = loadPa2(9, 3);
  printArray(data);

  return 0;
}
