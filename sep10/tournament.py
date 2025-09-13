import itertools as it
import json
import random
import re
from collections import *
from copy import deepcopy
from datetime import datetime
from sys import argv


def ceildiv(n,d):
  return n//d + int(bool(n%d))


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def dump_pa(A, filename):
  with open(filename, 'w+') as f:
    for line in A:
      f.write(' '.join(map(str, line)) + '\n')


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  i = 0
  while i < len(u) and (u[i]-v[i])**2 < dd:
    i += 1
  return i < len(u)


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True


def init_foes(A,n,d):
  lut = [set() for _ in A]
  for vx,v in enumerate(A):
    v = [e//d for e in v]
    for ux in range(vx):
      sep = False
      for dx,y in zip(A[ux],v):
        if abs(dx//d - y) > 1:
          sep = True
          break
      if not sep:
        lut[ux].add(vx)
        lut[vx].add(ux)
  return lut


def init_problems(A, d, foes):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in foes[vx]:
      if ux < vx and not separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, ux, d, upot, lut, asdf):
  gain = []
  loss = []
  umad = lut[ux]
  for vx in asdf:
    if ux == vx:
      continue
    v = A[vx]
    sep = separated(upot, v, d)
    if sep and vx in umad:
      gain.append(vx)
    elif not sep and vx not in umad:
      loss.append(vx)
  return gain, loss


def update_diffs(A, n, d, lut, i, row, gain, loss):
  A[i] = row
  for x in gain:
    lut[i].discard(x)
    lut[x].discard(i)
  for x in loss:
    lut[i].add(x)
    lut[x].add(i)


def pull_group(u,n,d,x):
  return [i for i,e in enumerate(u) if e//d == x]


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


def varmint(T):
  lut = defaultdict(lambda: defaultdict(list))
  for vx,v in enumerate(T):
    for ux in range(vx):
      u = T[ux]
      foe = True
      cols = 0
      for x,y in zip(u,v):
        if abs(x-y) > 1:
          foe = False
        if abs(x-y) > 0:
          cols += 1
      if foe:
        lut[ux][cols].append(vx)
        lut[vx][cols].append(ux)
  return lut


def main(A,T,n,d):
  # algorithm
  colin = varmint(T)
  a_foes = init_foes(A,n,d)
  a_problems = init_problems(A,d,a_foes)
  a_score = sum(map(len, a_problems))
  ANODE = deepcopy(A)
  print('Starting score:', a_score)

  while a_score:
    try:
      while True:
        u = random.randrange(len(colin))
        kvs = colin[u]
      # for u, kvs in colin.items():
        print(a_score, u)
        B = [A[u]]
        bidxs = [u]
        for k, vs in kvs.items():
          for v in vs:
            B.append(A[v])
            bidxs.append(v)

        lastx = -1
        lastscore = float('inf')
        best_b = deepcopy(B)

        b_foes = init_foes(B,n,d)
        b_lut = init_problems(B, d, b_foes)
        b_score = sum(map(len, b_lut))
        x = 0
        while True:
          if x-lastx > 100:
            break
          while True:
            i = random.randrange(len(b_foes))
            one = pull_group(B[i],n,d,random.randrange(ceildiv(n,d)))
            two = [e for e in one]
            random.shuffle(two)
            row = apply_permutation(B[i], one, two)
            gain, loss = eval_permutation(B, i, d, row, b_lut, b_foes[i])
            if len(gain) < len(loss):
              continue

            if b_score > 0:
              update_diffs(B, n, d, b_lut, i, row, gain, loss)
              b_score = sum(map(len, b_lut))
              break
            else:
              gain, loss = eval_permutation(A, bidxs[i], d, row, a_problems, a_foes[bidxs[i]])
              if len(gain) >= len(loss):
                B[i] = row
                b_lut = init_problems(B, d, b_foes)
                b_score = sum(map(len, b_lut))
                break

          if b_score > 0:
            continue

          x += 1
          for i,b in zip(bidxs, B):
            A[i] = b
          a_problems = init_problems(A,d,a_foes)
          score = sum(map(len, a_problems))
          if score < lastscore:
            best_b = deepcopy(B)
            lastscore = score
            lastx = x
            if lastscore < a_score:
              a_score = lastscore
              print(datetime.now(), '::', lastscore,u,x, len(A))
              ANODE = deepcopy(A)

        if lastscore <= a_score:
          a_score = lastscore
          for i,b in zip(bidxs, best_b):
            A[i] = b
          ANODE = deepcopy(A)

    except KeyboardInterrupt:
      print('Cancelling')
      break

  dump_pa(ANODE, f'pa_tournament_{n}_{d}.txt')


# scratch = True
scratch = False
if scratch:
  n,d = 8,3
  T = weave_template(n,d)
  A = [quick_fill(t,n,d) for t in T]
else:
  infile = argv[1]
  A = load_pa(infile)
  n,d = len(A[0]), int(re.findall(r'\d+', infile)[1])
  T = [[e//d for e in t] for t in A]

main(A,T,n,d)