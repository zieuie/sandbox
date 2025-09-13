from dataclasses import dataclass, replace
from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict, deque
import heapq

# =========================
# Data model (same Coset as before)
# =========================

from lib import Coset, make_cosets

# =========================
# Utilities for Phase 2
# =========================

def rA(a_idx: int, phiA: int) -> int:
    return (a_idx + phiA) % 3

def rB(b_idx: int, phiB: int) -> int:
    return (b_idx + phiB) % 3

def pair_satisfied_AB(U: Coset, V: Coset, phiA_u: int, phiB_v: int) -> bool:
    """Check if there is an A/B-overlap position i with rB(i) >= rA(i)."""
    for i in set(U.A_pos) & set(V.B_pos):
        if rB(V.B_idx_at_pos[i], phiB_v) >= rA(U.A_idx_at_pos[i], phiA_u):
            return True
    return False

def pair_satisfied(U: Coset, V: Coset, phiA_u: int, phiB_u: int, phiA_v: int, phiB_v: int) -> bool:
    """Pair satisfied if AB-direction or BA-direction has a witnessing position."""
    return pair_satisfied_AB(U, V, phiA_u, phiB_v) or pair_satisfied_AB(V, U, phiA_v, phiB_u)


# =========================
# Build arenas (pairs interact only if C-positions match)
# =========================

def arenas_by_C(cosets: List[Coset]) -> Dict[Tuple[int,int], List[int]]:
    arenas = defaultdict(list)
    for t, c in enumerate(cosets):
        arenas[tuple(sorted(c.C_pos))].append(t)
    return arenas


# =========================
# Current violations → bipartite graph (for heuristic)
# =========================

def build_violation_graph(
    cosets: List[Coset],
    phiA: List[int],
    phiB: List[int],
):
    """
    Return a bipartite graph (A-nodes on left, B-nodes on right) of 'violating pairs'.
    For each arena (fixed C-positions), add an edge (u,v) if the pair (u,v) is NOT satisfied
    under current rotations (i.e., neither direction yields a witness).
    """
    n = len(cosets)
    edges: Set[Tuple[int,int]] = set()
    byC = arenas_by_C(cosets)
    for _, idxs in byC.items():
        m = len(idxs)
        for i in range(m):
            u = idxs[i]; U = cosets[u]
            for j in range(i+1, m):
                v = idxs[j]; V = cosets[v]
                if not pair_satisfied(U, V, phiA[u], phiB[u], phiA[v], phiB[v]):
                    # We encode this undirected "unsatisfied pair" as an edge between A(u) and B(v)
                    # (vertex cover counts vertices to rotate at least once; either endpoint suffices).
                    edges.add((u, v))
    return edges


# =========================
# Min vertex cover on bipartite graph (Kőnig via max matching)
# =========================

def hopcroft_karp(adj: Dict[int, List[int]], n_left: int, n_right: int):
    INF = 10**9
    matchL = [-1]*n_left
    matchR = [-1]*n_right
    dist = [0]*n_left

    def bfs():
        from collections import deque
        q = deque()
        D = INF
        for u in range(n_left):
            if matchL[u] == -1:
                dist[u] = 0
                q.append(u)
            else:
                dist[u] = INF
        while q:
            u = q.popleft()
            if dist[u] < D:
                for v in adj.get(u, []):
                    mu = matchR[v]
                    if mu == -1:
                        D = dist[u] + 1
                    elif dist[mu] == INF:
                        dist[mu] = dist[u] + 1
                        q.append(mu)
        return D != INF

    def dfs(u):
        for v in adj.get(u, []):
            mu = matchR[v]
            if mu == -1 or (dist[mu] == dist[u] + 1 and dfs(mu)):
                matchL[u] = v
                matchR[v] = u
                return True
        dist[u] = 10**9
        return False

    msize = 0
    while bfs():
        for u in range(n_left):
            if matchL[u] == -1 and dfs(u):
                msize += 1
    return msize, matchL, matchR

def min_vertex_cover_bipartite(edges: Set[Tuple[int,int]], n_left: int, n_right: int):
    """
    Return (size, cover_left, cover_right) for a bipartite graph with left nodes 0..n_left-1
    and right nodes 0..n_right-1, where edges are (u_left, v_right).
    """
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
    msize, matchL, matchR = hopcroft_karp(adj, n_left, n_right)

    # Kőnig's theorem: build minimum vertex cover from maximum matching
    from collections import deque
    visL = [False]*n_left
    visR = [False]*n_right
    dq = deque([u for u in range(n_left) if matchL[u] == -1])
    while dq:
        u = dq.popleft()
        if visL[u]: continue
        visL[u] = True
        for v in adj[u]:
            if matchL[u] != v and not visR[v]:
                visR[v] = True
                mu = matchR[v]
                if mu != -1 and not visL[mu]:
                    dq.append(mu)

    cover_left  = {u for u in range(n_left) if not visL[u]}
    cover_right = {v for v in range(n_right) if visR[v]}
    size = len(cover_left) + len(cover_right)
    assert size == msize
    return size, cover_left, cover_right


# =========================
# A* for Phase 2 (rotations)
# =========================

class AStarPhase2:
    """
    Search over rotations φ_A, φ_B ∈ {0,1,2} per coset to satisfy: every pair of cosets has
    Chebyshev gap ≥ 3 (i.e., at least one A/B overlap with rB >= rA in either direction).
    Move = rotate A or B at a single coset by +1 (mod 3), cost = 1.
    Heuristic h = sum over arenas of min vertex cover sizes of current violation graphs.
    """

    def __init__(self, cosets: List[Coset]):
        self.cosets = cosets
        self.n = len(cosets)
        self.byC = arenas_by_C(cosets)
        # Preindex arenas for speed
        self.arenas = list(self.byC.values())

    def initial_state(self):
        # Start at zero rotations
        return (tuple([0]*self.n), tuple([0]*self.n))  # (phiA, phiB)

    def heuristic_and_goal(self, state):
        phiA, phiB = state
        total_h = 0
        cover_nodes: Set[Tuple[str,int]] = set()

        for idxs in self.arenas:
            # Build violating edges within this arena
            edges = set()
            m = len(idxs)
            for i in range(m):
                u = idxs[i]; U = self.cosets[u]
                for j in range(i+1, m):
                    v = idxs[j]; V = self.cosets[v]
                    if not pair_satisfied(U, V, phiA[u], phiB[u], phiA[v], phiB[v]):
                        edges.add((u, v))

            if not edges:
                continue

            # Compute min vertex cover for this arena
            # Left side is A-nodes indexed by actual coset indices; right side too (we reindex)
            # Build compact indices for this arena only
            left_map  = {u:i for i,u in enumerate(sorted({u for (u,_) in edges}))}
            right_set = sorted({v for (_,v) in edges})
            right_map = {v:j for j,v in enumerate(right_set)}
            compact_edges = {(left_map[u], right_map[v]) for (u,v) in edges}

            size, coverL, coverR = min_vertex_cover_bipartite(
                compact_edges, n_left=len(left_map), n_right=len(right_map)
            )
            total_h += size
            # Lift cover nodes back to global node IDs for branching
            for u_local in coverL:
                u_global = [k for k,v in left_map.items() if v == u_local][0]
                cover_nodes.add(('A', u_global))
            for v_local in coverR:
                v_global = right_set[v_local]
                cover_nodes.add(('B', v_global))

        is_goal = (total_h == 0)
        return total_h, is_goal, cover_nodes

    def expand(self, state, cover_nodes: Set[Tuple[str,int]]):
        """Generate successors: rotate +1 (mod 3) on one node in the current min cover."""
        phiA, phiB = state
        if not cover_nodes:
            # Fallback: rotate any node (rare if heuristic built correctly)
            for t in range(self.n):
                yield ((tuple((a + (1 if i==t else 0)) % 3 for i,a in enumerate(phiA)), phiB), 1)
                yield ((phiA, tuple((b + (1 if i==t else 0)) % 3 for i,b in enumerate(phiB))), 1)
            return

        for side, t in cover_nodes:
            if side == 'A':
                newA = list(phiA); newA[t] = (newA[t] + 1) % 3
                yield ((tuple(newA), phiB), 1)
            else:
                newB = list(phiB); newB[t] = (newB[t] + 1) % 3
                yield ((phiA, tuple(newB)), 1)

    def solve(self, max_expansions: int = 100000):
        start = self.initial_state()
        g0 = 0
        h0, goal, cover = self.heuristic_and_goal(start)
        if goal:
            return 0, start[0], start[1]

        frontier = []
        heapq.heappush(frontier, (g0 + h0, h0, 0, start))
        best_g = {start: 0}
        expansions = 0
        cnt = 1

        while frontier and expansions < max_expansions:
            f, h, _, state = heapq.heappop(frontier)
            print(len(frontier), f, h, _)
            g = best_g[state]
            h_now, goal, cover_nodes = self.heuristic_and_goal(state)
            if goal:
                return g, state[0], state[1]

            # Expand only along the current min vertex-cover nodes (focused branching)
            for next_state, step in self.expand(state, cover_nodes):
                ng = g + step
                if next_state not in best_g or ng < best_g[next_state]:
                    best_g[next_state] = ng
                    nh, _, _ = self.heuristic_and_goal(next_state)
                    heapq.heappush(frontier, (ng + nh, nh, cnt, next_state))
                    cnt += 1
            expansions += 1

        return None  # not found within expansion cap

ret = AStarPhase2(list(make_cosets())).solve()
print(ret)