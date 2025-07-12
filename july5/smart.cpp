#include <cmath>
#include <cstdio>
#include <string>

/*
def smart(n=6):
  d = n//2

  ret = 0
  perm = []
  lows = []
  used = [False] * n

  # perm[:i] is not separated from identity
  def recur():
    nonlocal ret

    # the prefix is in our clique
    if len(lows) == d and lows[-1] == d-1:
      return

    # no symbols remain
    if len(perm) >= n:
      ret += 1
      return

    for x in range(n):
      # skip if used
      if used[x] or abs(x-len(perm)) >= d:
        continue

      if x < d:
        lows.append(len(perm))

      perm.append(x)
      used[x] = True
      recur()
      used[x] = False
      perm.pop()

      if x < d:
        lows.pop()

  recur()
  print(ret)
*/

void recur(int n, int d, long long unsigned & ret, int* perm, int* lows, bool* used, int i, int l) {
  if (i >= n) {
    ret += 1;
    return;
  }

  for (int x = std::max(0, i-d+1); x < std::min(n, i+d); x++) {
    if (used[x]) {
      continue;
    }

    int nl = l;
    if (x < d) {
      if (l == d-1 && i == d-1) {
        continue;
      }
      lows[l] = i;
      nl = l+1;
    }

    perm[i] = x;
    used[x] = true;
    recur(n, d, ret, perm, lows, used, i+1, nl);
    used[x] = false;
  }
}

void smart(int n) {
  int d = n >> 1;
  long long unsigned ret = 0;
  int* perm = new int[n];
  int* lows = new int[d];
  bool* used = new bool[n];
  recur(n, d, ret, perm, lows, used, 0, 0);
  printf("%d %llu\n", n, ret);
}

int main(int argc, char** argv) {
  int n = std::stoi(argv[1]);
  smart(n);
  // for (int n = 6; ; n+=2) {
    // smart(n);
    // }
}


