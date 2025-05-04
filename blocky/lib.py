from collections import Counter
import itertools as it
import random


def load_pa(filename):
  ret = []
  with open(filename, 'r') as f:
    for line in f:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      ret.append(list(map(int, line.split())))
  return ret


def apply_permutation(perm, src, dst):
  ret = [e for e in perm]
  for u,v in zip(src, dst):
    ret[u] = perm[v]
  return ret


def separated(u, v, d):
  dd = d*d
  for a,b in zip(u,v):
    if (a-b)**2 >= dd:
      return True
  return False


def verify(pa, d):
  for vx in range(len(pa)):
    for ux in range(vx):
      if not separated(pa[ux], pa[vx], d):
        return False
  return True
       

def disagreement_counter(pa, d):
  ret = []
  c = Counter()
  for vx, v in enumerate(pa):
    for ux in range(vx):
      u = pa[ux]
      separated = False
      for a,b in zip(u,v):
        if abs(a-b) >= d:
          separated = True
          break
      if not separated:
        ret.append((ux,vx))
        c.update([ux])
        c.update([vx])
  return c


def init_separations(A, d):
  s = [set() for _ in A]
  for vx in range(len(A)):
    for ux in range(vx):
      if ux != vx and separated(A[ux], A[vx], d):
        s[ux].add(vx)
        s[vx].add(ux)
  return s


def eval_permutation(A, start, target, s, i, d):
  pot = apply_permutation(A[i], start, target)
  news = []
  adders = []
  subers = []
  for x, e in enumerate(A):
    if x == i:
      continue
    if separated(pot, e, d):
      if x not in s[i]:
        news.append(x)
      if i not in s[x]:
        adders.append(x)
    elif x in s[i]:
      subers.append(x)
  return adders, subers, news


def update_diffs(A, s, i, row, adders, subers, news):
  A[i] = row
  s[i].update(news)
  for x in adders:
    s[x].add(i)
  for x in subers:
    s[i].discard(x)
    s[x].discard(i)


# Mutates A, s
def gently_disturb(A, H, L, s, d, high_only=False):
  while True:
    i = random.randrange(len(A))
    one = random.choice((H, L))[i%len(H)]
    if high_only:
      one = H[i%len(H)]
    two = [e for e in one]
    random.shuffle(two)
    adders, subers, news = eval_permutation(A, one, two, s, i, d)
    if len(news) + len(adders) >= 2*len(subers):
      break

  row = apply_permutation(A[i], one, two)
  update_diffs(A, s, i, row, adders, subers, news)  
  return len(news) + len(adders) > 2*len(subers)


def greatly_disturb(A, H, L, s, d, high_only=False):
  q = sorted([(len(si), idx) for idx,si in enumerate(s)])
  # i = random.randrange(len(A))
  # i = random.randrange(10)
  i = q[-1][1]

  hps = [e for e in H[i % len(H)]]
  random.shuffle(hps)

  lps = [e for e in L[i % len(H)]]
  random.shuffle(lps)

  one = list(H[i % len(H)]) + list(L[i % len(H)])
  two = hps + lps
  
  if high_only:
    one = list(H[i % len(H)])
    two = hps

  adders, subers, news = eval_permutation(A, one, two, s, i, d)

  row = apply_permutation(A[i], one, two)
  update_diffs(A, s, i, row, adders, subers, news)
