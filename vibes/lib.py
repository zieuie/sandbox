import itertools as it
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional, Iterable


@dataclass(frozen=True)
class Coset:
    """
    One ordered (3,3,2)-block coset, with base label indices at φ=0.
      A_pos, B_pos, C_pos: positions (indices 0..7)
      A_idx_at_pos[i] in {0,1,2} → value i gets 0/1/2 under φ_A=0
      B_idx_at_pos[i] in {0,1,2} → value i gets 3/4/5 under φ_B=0  (i.e., 3 + idx)
      C_idx_at_pos[i] in {0,1}   → value i gets 6/7 (fixed in Phase 2)
    Rotation by +1 means add 1 mod 3 to all A (or B) indices of this coset.
    """
    A_pos: Tuple[int, int, int]
    B_pos: Tuple[int, int, int]
    C_pos: Tuple[int, int]
    A_idx_at_pos: Dict[int, int]
    B_idx_at_pos: Dict[int, int]
    C_idx_at_pos: Optional[Dict[int, int]] = None  # optional; default filled as {C_pos[0]:0, C_pos[1]:1}

    def __post_init__(self):
        if set(self.A_pos) & set(self.B_pos) or set(self.A_pos) & set(self.C_pos) or set(self.B_pos) & set(self.C_pos):
            raise ValueError("Blocks A/B/C must be disjoint.")
        if len(self.A_pos) != 3 or len(self.B_pos) != 3 or len(self.C_pos) != 2:
            raise ValueError("Block sizes must be 3,3,2.")
        if set(self.A_idx_at_pos.keys()) != set(self.A_pos):
            raise ValueError("A_idx_at_pos must cover exactly A_pos.")
        if set(self.B_idx_at_pos.keys()) != set(self.B_pos):
            raise ValueError("B_idx_at_pos must cover exactly B_pos.")
        if sorted(self.A_idx_at_pos.values()) != [0,1,2]:
            raise ValueError("A_idx_at_pos values must be a permutation of {0,1,2}.")
        if sorted(self.B_idx_at_pos.values()) != [0,1,2]:
            raise ValueError("B_idx_at_pos values must be a permutation of {0,1,2}.")
        object.__setattr__(self, "C_idx_at_pos",
            self.C_idx_at_pos if self.C_idx_at_pos is not None
            else {self.C_pos[0]: 0, self.C_pos[1]: 1}
        )
        if set(self.C_idx_at_pos.keys()) != set(self.C_pos):
            raise ValueError("C_idx_at_pos must cover exactly C_pos.")
        if sorted(self.C_idx_at_pos.values()) != [0,1]:
            raise ValueError("C_idx_at_pos values must be a permutation of {0,1}.")

def ceildiv(n,d):
  return n//d + int(bool(n%d))


def weave_template(n,d):
  # weave template
  A = [[ceildiv(n,d)-1]*(n%d or d)]
  for x in range(ceildiv(n,d)-1):
    B = []
    for a in A:
      for ps in it.combinations(list(range(len(a)+d)), d):
        nex = []
        l = 0
        for i in range(len(a)+d):
          if i in ps:
            nex.append(x)
          else:
            nex.append(a[l])
            l += 1
        B.append(nex)
    A = B
  return A


def quick_fill(t,n,d):
  f = [iter(range(e*d, (e+1)*d)) for e in range(ceildiv(n,d))]
  return [next(f[e]) for e in t]


def make_cosets():
  n,d = 8,3
  for row in weave_template(n,d):
    pos=[[] for _ in range(ceildiv(n,d))]
    idx=[dict() for _ in range(ceildiv(n,d))]
    for k,v in enumerate(quick_fill(row,n,d)):
      pos[v//d].append(k)
      idx[v//d][k] = v%d
    yield Coset(*pos, *idx)

