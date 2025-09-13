# sud_milp_gurobi_v2.py  (v2.1)
# -------------------------------------------------------------
# Gurobi MILP for Sud-group Phase 2 (full pairwise constraints)
# Ensures Chebyshev distance >= 3 for *all* relevant pairs, with
# AB/BA/BC/CB witnesses and AC/CA pairs auto-skipped in "auto" mode.
#
# Fix v2.1: Correct value-wise assignment constraints to sum x[(t,i,v)]
# over positions i in the appropriate block. This removes the KeyError
# caused by referencing x[(t,i,i_pos)].
# -------------------------------------------------------------

from dataclasses import dataclass
from typing import Dict, List, Tuple, Iterable, Optional, Set
import itertools
import gurobipy as gp
from gurobipy import GRB


@dataclass(frozen=True)
class Coset:
    A_pos: Tuple[int, int, int]
    B_pos: Tuple[int, int, int]
    C_pos: Tuple[int, int]
    A_idx_at_pos: Dict[int, int]
    B_idx_at_pos: Dict[int, int]

    def __post_init__(self):
        if set(self.A_pos) & set(self.B_pos) or set(self.A_pos) & set(self.C_pos) or set(self.B_pos) & set(self.C_pos):
            raise ValueError("Blocks must be disjoint.")
        if len(self.A_pos) != 3 or len(self.B_pos) != 3 or len(self.C_pos) != 2:
            raise ValueError("Sizes must be 3,3,2.")
        if set(self.A_idx_at_pos.keys()) != set(self.A_pos):
            raise ValueError("A_idx_at_pos must cover exactly A_pos.")
        if set(self.B_idx_at_pos.keys()) != set(self.B_pos):
            raise ValueError("B_idx_at_pos must cover exactly B_pos.")
        if sorted(self.A_idx_at_pos.values()) != [0,1,2]:
            raise ValueError("A_idx_at_pos values must be a permutation of {0,1,2}.")
        if sorted(self.B_idx_at_pos.values()) != [0,1,2]:
            raise ValueError("B_idx_at_pos values must be a permutation of {0,1,2}.")


def generate_arena_cosets(C_pos: Tuple[int,int]) -> List[Coset]:
    all_pos = set(range(8))
    rest = sorted(all_pos - set(C_pos))
    cosets: List[Coset] = []
    for A in itertools.combinations(rest, 3):
        A = tuple(sorted(A))
        B = tuple(sorted(set(rest) - set(A)))
        A_idx = {p:i for i,p in enumerate(sorted(A))}
        B_idx = {p:i for i,p in enumerate(sorted(B))}
        cosets.append(Coset(A_pos=A, B_pos=B, C_pos=tuple(sorted(C_pos)),
                            A_idx_at_pos=A_idx, B_idx_at_pos=B_idx))
    return cosets

def generate_all_560_cosets() -> List[Coset]:
    cosets: List[Coset] = []
    for C in itertools.combinations(range(8), 2):
        cosets.extend(generate_arena_cosets(C))
    return cosets


GOOD_AB = {(0,3),(0,4),(0,5),(1,4),(1,5),(2,5)}
GOOD_BC = {(3,6),(3,7),(4,7)}  # symmetric for CB

def build_milp(cosets: List[Coset],
               restrict_pairs: str = "auto",   # "auto" (skip AC/CA autosafe), "all", or "sameC"
               model_name: str = "sud_phase2_v2",
               log_to_console: int = 1,
               time_limit: Optional[float] = None,
               threads: Optional[int] = None,
               mip_focus: int = 1):
    n = len(cosets)
    m = gp.Model(model_name)
    m.Params.LogToConsole = log_to_console
    if time_limit is not None: m.Params.TimeLimit = time_limit
    if threads    is not None: m.Params.Threads   = threads
    if mip_focus  is not None: m.Params.MIPFocus  = mip_focus
    m.Params.Presolve = 2

    A_vals = [0,1,2]
    B_vals = [3,4,5]
    C_vals = [6,7]

    x = {}
    positions = list(range(8))

    A_pos = [set(c.A_pos) for c in cosets]
    B_pos = [set(c.B_pos) for c in cosets]
    C_pos = [set(c.C_pos) for c in cosets]

    # Create x only where allowed
    for t in range(n):
        for i in positions:
            if i in A_pos[t]:
                allowed = A_vals
            elif i in B_pos[t]:
                allowed = B_vals
            elif i in C_pos[t]:
                allowed = C_vals
            else:
                raise RuntimeError("Position not in any block.")
            for v in allowed:
                x[(t,i,v)] = m.addVar(vtype=GRB.BINARY, name=f"x_t{t}_i{i}_v{v}")
    m.update()

    # Assignment constraints
    for t in range(n):
        # position -> exactly one value
        for i in positions:
            allowed = (A_vals if i in A_pos[t] else (B_vals if i in B_pos[t] else C_vals))
            m.addConstr(gp.quicksum(x[(t,i,v)] for v in allowed) == 1, name=f"assign_pos_t{t}_i{i}")
        # value -> used exactly once (sum over positions in the corresponding block)
        for v in A_vals:
            m.addConstr(gp.quicksum(x[(t,i,v)] for i in A_pos[t]) == 1, name=f"assign_valA_t{t}_v{v}")
        for v in B_vals:
            m.addConstr(gp.quicksum(x[(t,i,v)] for i in B_pos[t]) == 1, name=f"assign_valB_t{t}_v{v}")
        for v in C_vals:
            m.addConstr(gp.quicksum(x[(t,i,v)] for i in C_pos[t]) == 1, name=f"assign_valC_t{t}_v{v}")

    # Pair selection
    def C_key(t: int) -> Tuple[int,int]:
        return tuple(sorted(cosets[t].C_pos))

    pairs: List[Tuple[int,int]] = []
    if restrict_pairs == "sameC":
        byC: Dict[Tuple[int,int], List[int]] = {}
        for t in range(n):
            byC.setdefault(C_key(t), []).append(t)
        for _, idxs in byC.items():
            idxs = sorted(idxs)
            for i in range(len(idxs)):
                for j in range(i+1, len(idxs)):
                    pairs.append((idxs[i], idxs[j]))
    elif restrict_pairs in ("auto", "all"):
        for t in range(n):
            for u in range(t+1, n):
                pairs.append((t,u))
    else:
        raise ValueError("restrict_pairs must be 'auto', 'all', or 'sameC'")

    z = {}
    y = {}

    for (t,u) in pairs:
        AB = sorted(A_pos[t] & B_pos[u])
        BA = sorted(A_pos[u] & B_pos[t])
        AC = sorted(A_pos[t] & C_pos[u])
        CA = sorted(A_pos[u] & C_pos[t])
        BC = sorted(B_pos[t] & C_pos[u])
        CB = sorted(B_pos[u] & C_pos[t])

        if restrict_pairs == "auto" and (AC or CA):
            continue  # auto-safe; Chebyshev >=4 via A/C

        z_terms = []

        # AB
        for i in AB:
            z[(t,u,i,"AB")] = m.addVar(vtype=GRB.BINARY, name=f"z_AB_t{t}_u{u}_i{i}")
            z_terms.append(z[(t,u,i,"AB")])
            for vA in A_vals:
                for vB in B_vals:
                    if (vA,vB) in GOOD_AB:
                        var = m.addVar(vtype=GRB.BINARY, name=f"y_AB_t{t}_u{u}_i{i}_a{vA}_b{vB}")
                        y[(t,u,i,"AB",vA,vB)] = var
                        m.addConstr(var <= x[(t,i,vA)])
                        m.addConstr(var <= x[(u,i,vB)])
            good_list = [y[(t,u,i,"AB",vA,vB)] for (vA,vB) in GOOD_AB if (t,u,i,"AB",vA,vB) in y]
            if good_list:
                m.addConstr(z[(t,u,i,"AB")] <= gp.quicksum(good_list))
            else:
                m.addConstr(z[(t,u,i,"AB")] == 0)

        # BA
        for i in BA:
            z[(t,u,i,"BA")] = m.addVar(vtype=GRB.BINARY, name=f"z_BA_t{t}_u{u}_i{i}")
            z_terms.append(z[(t,u,i,"BA")])
            for vA in A_vals:
                for vB in B_vals:
                    if (vA,vB) in GOOD_AB:
                        var = m.addVar(vtype=GRB.BINARY, name=f"y_BA_t{t}_u{u}_i{i}_a{vA}_b{vB}")
                        y[(t,u,i,"BA",vA,vB)] = var
                        m.addConstr(var <= x[(u,i,vA)])
                        m.addConstr(var <= x[(t,i,vB)])
            good_list = [y[(t,u,i,"BA",vA,vB)] for (vA,vB) in GOOD_AB if (t,u,i,"BA",vA,vB) in y]
            if good_list:
                m.addConstr(z[(t,u,i,"BA")] <= gp.quicksum(good_list))
            else:
                m.addConstr(z[(t,u,i,"BA")] == 0)

        # BC
        for i in BC:
            z[(t,u,i,"BC")] = m.addVar(vtype=GRB.BINARY, name=f"z_BC_t{t}_u{u}_i{i}")
            z_terms.append(z[(t,u,i,"BC")])
            for vB in B_vals:
                for vC in C_vals:
                    if (vB,vC) in GOOD_BC:
                        var = m.addVar(vtype=GRB.BINARY, name=f"y_BC_t{t}_u{u}_i{i}_b{vB}_c{vC}")
                        y[(t,u,i,"BC",vB,vC)] = var
                        m.addConstr(var <= x[(t,i,vB)])
                        m.addConstr(var <= x[(u,i,vC)])
            good_list = [y[(t,u,i,"BC",vB,vC)] for (vB,vC) in GOOD_BC if (t,u,i,"BC",vB,vC) in y]
            if good_list:
                m.addConstr(z[(t,u,i,"BC")] <= gp.quicksum(good_list))
            else:
                m.addConstr(z[(t,u,i,"BC")] == 0)

        # CB
        for i in CB:
            z[(t,u,i,"CB")] = m.addVar(vtype=GRB.BINARY, name=f"z_CB_t{t}_u{u}_i{i}")
            z_terms.append(z[(t,u,i,"CB")])
            for vB in B_vals:
                for vC in C_vals:
                    if (vB,vC) in GOOD_BC:
                        var = m.addVar(vtype=GRB.BINARY, name=f"y_CB_t{t}_u{u}_i{i}_b{vB}_c{vC}")
                        y[(t,u,i,"CB",vB,vC)] = var
                        m.addConstr(var <= x[(u,i,vB)])
                        m.addConstr(var <= x[(t,i,vC)])
            good_list = [y[(t,u,i,"CB",vB,vC)] for (vB,vC) in GOOD_BC if (t,u,i,"CB",vB,vC) in y]
            if good_list:
                m.addConstr(z[(t,u,i,"CB")] <= gp.quicksum(good_list))
            else:
                m.addConstr(z[(t,u,i,"CB")] == 0)

        if z_terms:
            m.addConstr(gp.quicksum(z_terms) >= 1, name=f"pair_cover_t{t}_u{u}")
        else:
            # No overlaps at all => infeasible
            m.addConstr(0 >= 1, name=f"pair_no_overlap_infeasible_t{t}_u{u}")

    m.update()
    m.ModelSense = GRB.MINIMIZE
    return m, x


def extract_permutations(cosets: List[Coset], x_vars: Dict, model: gp.Model) -> List[List[int]]:
    n = len(cosets)
    perms: List[List[int]] = []
    for t in range(n):
        perm = [None]*8
        for i in range(8):
            assigned = [v for v in range(8) if (t,i,v) in x_vars and x_vars[(t,i,v)].X > 0.5]
            if len(assigned) != 1:
                raise RuntimeError(f"Bad assignment at coset {t}, pos {i}: {assigned}")
            perm[i] = assigned[0]
        if sorted(perm) != list(range(8)):
            raise RuntimeError(f"Permutation not valid (not a bijection) at coset {t}: {perm}")
        perms.append(perm)
    return perms


def cheb_distance(p: List[int], q: List[int]) -> int:
    return max(abs(p[i]-q[i]) for i in range(8))


def verify_pairwise(perms: List[List[int]], cosets: List[Coset], mode: str = "auto") -> bool:
    n = len(perms)
    A_pos = [set(c.A_pos) for c in cosets]
    B_pos = [set(c.B_pos) for c in cosets]
    C_pos = [set(c.C_pos) for c in cosets]

    for t in range(n):
        for u in range(t+1, n):
            if mode == "sameC" and sorted(cosets[t].C_pos) != sorted(cosets[u].C_pos):
                continue
            if mode == "auto":
                AC = A_pos[t] & C_pos[u]
                CA = A_pos[u] & C_pos[t]
                if AC or CA:
                    continue
            if cheb_distance(perms[t], perms[u]) < 3:
                return False
    return True


def solve_sud_phase2(cosets: List[Coset],
                     restrict_pairs: str = "auto",
                     time_limit: Optional[float] = None,
                     threads: Optional[int] = None,
                     log_to_console: int = 1):
    model, x = build_milp(
        cosets,
        restrict_pairs=restrict_pairs,
        log_to_console=log_to_console,
        time_limit=time_limit,
        threads=threads,
        mip_focus=1,
    )
    model.optimize()
    if model.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        if model.SolCount > 0 and model.Status != GRB.INFEASIBLE:
            perms = extract_permutations(cosets, x, model)
            assert verify_pairwise(perms, cosets, mode=restrict_pairs), "Solution failed verification."
            return True, perms
    if model.Status == GRB.INFEASIBLE:
        model.computeIIS()
        model.write("sud_phase2_conflict.ilp")
    return False, None

# -----------------------------
# Minimal example usage (no demo printouts)
# -----------------------------

def main():
    # Example: build all 560 cosets; solve with 'auto' (skips AC/CA pairs).
    cosets = generate_all_560_cosets()
    feasible, perms = solve_sud_phase2(
        cosets,
        restrict_pairs="auto",  # use "all" for strict all-pairs enforcement (slower)
        time_limit=None,
        threads=8,
        log_to_console=1
    )
    print("Feasible:", feasible)
    if not feasible:
        print("No solution or time limit reached.")

    with open('moo', 'w+') as f:
        for t in range(len(perms)):
            print(' '.join(map(str, perms[t])))

    # Example 2: all 560 cosets (heavier). You can try 'sameC' first, then 'all' if needed.
    # cosets_all = generate_all_560_cosets()
    # feasible2, perms2 = solve_sud_phase2(cosets_all, restrict_pairs="sameC", time_limit=600, threads=None, log_to_console=1)
    # print("All-arenas (sameC) feasible:", feasible2)

if __name__ == "__main__":
    main()