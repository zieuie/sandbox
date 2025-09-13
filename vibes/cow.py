from lib import Coset, make_cosets
from typing import Dict, List, Set, Tuple, Optional, Iterable


# ---------- Core combinatorics for H2 ----------

def cheb_diff_at(a_idx: int, b_idx: int, delta: int, alpha: int) -> int:
  """
  A/B coordinate numeric gap at a single position, given:
    - a_idx in {0,1,2} is the A label index at φ_A = 0
    - b_idx in {0,1,2} is the B label index at φ_B = 0
    - alpha in {0,1,2} is φ_A
    - delta in {0,1,2} is (φ_B - φ_A) mod 3
  """
  ra = (a_idx + alpha) % 3       # yields 0..2 → actual value is ra
  rb = (b_idx + alpha + delta) % 3   # yields 0..2 → actual value is 3 + rb
  return abs(ra - (3 + rb))      # ∈ {1,2,3,4,5}

def forbidden_deltas_for_coordinate(a_idx: int, b_idx: int) -> Set[int]:
  """
  F_i ⊆ {0,1,2}: the Δ values such that this coordinate can NEVER witness ≥3
  under rotations (i.e., gap < 3 for ALL α ∈ {0,1,2}).
  """
  bad = set()
  for delta in (0, 1, 2):
    max_over_alpha = max(cheb_diff_at(a_idx, b_idx, delta, alpha) for alpha in (0,1,2))
    if max_over_alpha <= 2:
      bad.add(delta)
  return bad  # typically either ∅ or a singleton

def pair_forbidden_delta(
  x: Coset, y: Coset, positions: Iterable[int], direction: str
) -> Set[int]:
  """
  Intersect F_i over positions where x supplies A and y supplies B at the same i (direction='A_vs_B'),
  or the reverse (direction='B_vs_A').
  Returns F_{xy} ⊆ {0,1,2}. If empty → pair is automatically safe in this direction.
  """
  assert direction in ("A_vs_B", "B_vs_A")
  F_intersection = {0,1,2}
  for i in positions:
    if direction == "A_vs_B":
      a_idx = x.A_idx_at_pos[i]
      b_idx = y.B_idx_at_pos[i]
    else:
      a_idx = y.A_idx_at_pos[i]  # now y is the A-side
      b_idx = x.B_idx_at_pos[i]  # and x is the B-side
    F_i = forbidden_deltas_for_coordinate(a_idx, b_idx)
    F_intersection &= F_i
    if not F_intersection:
      break
  return F_intersection  # usually ∅ or {δ}

# ---------- Build the H2 constraint graph ----------

def build_h2_constraints(cosets: List[Coset]):
  """
  For each unordered pair of cosets {u,v} that share the SAME C positions:
    - look at positions where u has A and v has B → compute F_{uv} (Δ forbidden)
    - look at positions where v has A and u has B → compute F_{vu} (Δ forbidden)
  Create a bipartite constraint graph with:
    - Left nodes: ('A', u)
    - Right nodes: ('B', v)
    - Edge: (('A', u), ('B', v), forbiddens) if F_{uv} non-empty
  Returns:
    edges: Dict[Tuple[str,int], List[Tuple[Tuple[str,int], Set[int]]]]
    A_nodes: List[Tuple[str,int]]
    B_nodes: List[Tuple[str,int]]
  """
  n = len(cosets)
  A_nodes = [('A', t) for t in range(n)]
  B_nodes = [('B', t) for t in range(n)]
  edges: Dict[Tuple[str,int], List[Tuple[Tuple[str,int], Set[int]]]] = {node: [] for node in A_nodes + B_nodes}

  # Helper: detect C-sharing pairs
  C_signature_to_indices: Dict[Tuple[int,int], List[int]] = {}
  for idx, c in enumerate(cosets):
    key = tuple(sorted(c.C_pos))
    C_signature_to_indices.setdefault(key, []).append(idx)

  for key, idxs in C_signature_to_indices.items():
    # Only pairs within the same C-signature are "hard"
    for i_idx in range(len(idxs)):
      u = idxs[i_idx]
      U = cosets[u]
      for j_idx in range(i_idx + 1, len(idxs)):
        v = idxs[j_idx]
        V = cosets[v]

        # Direction: U(A) vs V(B)
        AB_positions = set(U.A_pos) & set(V.B_pos)
        if AB_positions:
          F_uv = pair_forbidden_delta(U, V, AB_positions, "A_vs_B")
          if F_uv:  # non-empty → constraint edge
            edges[('A', u)].append((('B', v), F_uv))

        # Direction: V(A) vs U(B)
        BA_positions = set(V.A_pos) & set(U.B_pos)
        if BA_positions:
          F_vu = pair_forbidden_delta(U, V, BA_positions, "B_vs_A")
          if F_vu:  # non-empty → constraint edge
            edges[('A', v)].append((('B', u), F_vu))

  return edges, A_nodes, B_nodes

# ---------- Rotation-only CSP solver (optional Phase-1 check) ----------

def rotation_only_feasible(
  edges: Dict[Tuple[str,int], List[Tuple[Tuple[str,int], Set[int]]]],
  A_nodes: List[Tuple[str,int]],
  B_nodes: List[Tuple[str,int]],
  time_limit_nodes: Optional[int] = None,
) -> Tuple[bool, Optional[Dict[Tuple[str,int], int]]]:
  """
  Try to assign φ_A, φ_B ∈ {0,1,2} satisfying each edge constraint:
    φ_B(v) - φ_A(u) (mod 3) ∉ forbiddens(u,v).
  Returns (feasible?, assignment_or_None). Basic backtracking + forward checking.
  """
  from collections import defaultdict, deque
  import time

  # Domains
  domains: Dict[Tuple[str,int], Set[int]] = {node: {0,1,2} for node in A_nodes + B_nodes}

  # Undirected adjacency for quick neighbor scans
  nbrs = defaultdict(list)
  for u in A_nodes:
    for v, forb in edges[u]:
      nbrs[u].append((v, forb))
      nbrs[v].append((u, forb))

  # AC-3 style arc-consistency to prune domains
  def revise(u, v, forbiddens) -> bool:
    """Remove values from domains[u] that have no support in domains[v]."""
    removed = False
    supp_v = domains[v]
    to_remove = set()
    for a in domains[u]:
      ok = False
      for b in supp_v:
        if ((b - a) % 3) not in forbiddens:
          ok = True
          break
      if not ok:
        to_remove.add(a)
    if to_remove:
      domains[u] -= to_remove
      removed = True
    return removed

  # Initialize AC-3 queue with directed arcs from every edge
  Q = deque()
  for u in A_nodes:
    for v, forb in edges[u]:
      Q.append((u, v, forb))
      Q.append((v, u, forb))
  while Q:
    u, v, forb = Q.popleft()
    if revise(u, v, forb):
      if not domains[u]:
        return False, None
      for w, forb2 in nbrs[u]:
        if w != v:
          Q.append((w, u, forb2))

  # Backtracking with MRV + forward checking
  assignment: Dict[Tuple[str,int], int] = {}

  # Node ordering: interleave A and B nodes with smallest domains first
  all_nodes = A_nodes + B_nodes
  if time_limit_nodes is None:
    time_limit_nodes = len(all_nodes)  # no cut

  def select_unassigned() -> Optional[Tuple[str,int]]:
    un = [n for n in all_nodes if n not in assignment]
    if not un:
      return None
    # Minimum Remaining Values (MRV), tie-break by degree
    un.sort(key=lambda n: (len(domains[n]), -len(nbrs[n])))
    return un[0]

  def fc_consistent(u, a) -> List[Tuple[Tuple[str,int], int]]:
    """Assign u=a; forward-check neighbors (prune), return list of (v,removed_val) for undo."""
    removed = []
    for v, forb in nbrs[u]:
      if v in assignment:
        # Check immediate consistency
        b = assignment[v]
        if ((b - a) % 3) in forb:
          return None
      else:
        # Prune v's domain where constraint would be violated
        to_kill = {b for b in domains[v] if ((b - a) % 3) in forb}
        if to_kill:
          if len(domains[v] - to_kill) == 0:
            return None
          for bval in to_kill:
            domains[v].remove(bval)
            removed.append((v, bval))
    return removed

  def undo(removed):
    for v, bval in removed:
      domains[v].add(bval)

  def bt(assigned_count=0) -> bool:
    if assigned_count >= time_limit_nodes:
      # soft cut (mainly for huge instances)
      pass
    u = select_unassigned()
    if u is None:
      return True
    dom = list(domains[u])
    # Value ordering: try values that keep more neighbor options
    def score(a):
      s = 0
      for v, forb in nbrs[u]:
        if v not in assignment:
          s += sum(1 for b in domains[v] if ((b - a) % 3) not in forb)
      return -s
    dom.sort(key=score)
    for a in dom:
      removed = fc_consistent(u, a)
      if removed is None:
        continue
      assignment[u] = a
      ok = bt(assigned_count+1)
      if ok:
        return True
      del assignment[u]
      undo(removed)
    return False

  feasible = bt()
  return feasible, (assignment if feasible else None)


# ---------- Convenience wrapper to run "Phase 1 (H2)" ----------

def h2_phase1(cosets: List[Coset]):
  """
  Build the H2 constraint graph and try to find a rotation-only assignment.
  Returns:
    edges, A_nodes, B_nodes, feasible, assignment
  """
  edges, A_nodes, B_nodes = build_h2_constraints(cosets)
  feasible, assignment = rotation_only_feasible(edges, A_nodes, B_nodes)
  return edges, A_nodes, B_nodes, feasible, assignment


# 1) Build your cosets list
cosets = list(make_cosets())

# 2) Phase 1 (H2): build constraints & test rotation-only feasibility
edges, A_nodes, B_nodes, feasible, assignment = h2_phase1(cosets)

print("Edges (count):", sum(len(v) for v in edges.values()))
print("Rotation-only feasible?", feasible)
# If feasible==True, 'assignment' maps ('A',t) and ('B',t) to φ∈{0,1,2}.
