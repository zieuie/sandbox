from lib import *

# 0 1 2 3 4 5 6 7
def get_mask(n, k, row):
  return [(1 if e >= n-k else 0) for e in row]


def get_block_size(n, k, A):
  block_size = 0
  prev = None
  for row in A:
    m = get_mask(n, k, row)
    if prev is None or prev == m:
      prev = m
      block_size += 1
      continue
    break
  return block_size


def make_blocks(A, block_size):
  B = []
  block = []
  for x, row in enumerate(A):
    if len(block) % block_size == 0:
      if block:
        B.append(block)
      block = []
    block.append(row)

  if block:
    B.append(block)
  return B


def sep_cols(n, k, u, v):
  ret = []
  for i, (a,b) in enumerate(zip(u,v)):
    if abs(a-b) >= k:
      ret.append(i)
  return ret


def row_summary(n, k, B, b, r, u=None):
  u = u or B[b][r]

  nots = []
  onces = []
  multis = []

  for b2, block in enumerate(B):
    for r2, v in enumerate(block):
      if (b2,r2) == (b,r):
        continue
      s = sep_cols(n, k, u, v)
      if len(s) == 0:
        # not separated
        nots.append((b2,r2))
      elif len(s) == 1:
        # once separated
        onces.append((b2, r2, s))
      else:
        # multi separated
        multis.append((b2, r2, s))

  return nots, onces, multis


def block_print(n, k, B, hide_covered=False):
  block_size = len(B[0])
  idx = -1
  unseparated = []
  for b, block in enumerate(B):
    for r, row in enumerate(block):
      idx += 1
      x, y, z = row_summary(n, k, B, b, r)
      if not x and hide_covered:
        continue

      i = b*block_size + r
      for vb,vr in x:
        unseparated.append((i, vb*block_size + vr))

      print (f'{idx} or {b}:{r} : {row} : {len(x)} : {len(y)} : {len(z)} : {[d*block_size + r for d,r in x]}')
    print()
  
  if len(unseparated) < 20:
    print ('Unseparated pairs: ', ' '.join(map(str, unseparated)))
  print (f'({len(unseparated)} total unseparated pairs)')
  print()

def pick_range(prompt, limit):
  while True:
    usr = input(prompt)
    try:
      usr = int(usr)
      if 0 <= usr < limit:
        return usr
    except:
      pass


def pick_option(prompt, options):
  while True:
    print(prompt)
    for k,v in options.items():
      print (f'  - {k} : {v}')
    usr = input('Selection: ')
    if usr in options:
      return usr

# def print_summary(n, k, B, N):
#   print (f'Summary for PA({n}, {k}) >= {N}')
#   print (f'There are <blah> pairs of rows not separated')
#   print (f'The rows that are not separated are:')



def inspect_row(n, k, B, usr):
  block_size = len(B[0])

  b, r = usr//block_size, usr%block_size
  nots, onces, multis = row_summary(n, k, B, b, r)
  print (f'You are inspecting row {usr}')
  print(f'{B[b][r]} : {usr:4} : The selected row')

  if nots:
    for b2, r2 in nots:
      idx2 = b2*block_size + r2
      print(f'{B[b2][r2]} : {idx2:4} : Not separated')
  else:
    print('[Separated from all rows]')

  if onces:
    c = Counter()
    for b2, r2, s in onces:
      c.update((e, 1) for e in s)

  if multis:
    for b2, r2, s in multis:
      c.update((e, len(s)) for e in s)

  print ('Separations by column')
  for col in range(n):
    print ('  -', col, end=' ')
    for count in range(n):
      if (col,count) in c:
        print (f'{count:2}:{c[(col,count)]:<3}', end=' ')
    print()


def mutate_row(n, k, B, usr):
  print(f'Mutating row {usr}')
  block_size = len(B[0])

  b, r = usr//block_size, usr%block_size
  row = B[b][r]
  mask = get_mask(n, k, row)
  print('Mask:', mask)

  while True:
    pot = input('New row: ')
    try:
      pot = list(map(int, pot.split()))
    except:
      print ('Not parsable')
      continue

    if mask != get_mask(n, k, pot):
      print ('Cannot mutate the mask')
      continue
    
    nots, onces, multis = row_summary(n, k, B, b, r, u=pot)
    print(f'{pot} : {usr:4} : The possible row')

    if nots:
      for b2, r2 in nots:
        idx2 = b2*block_size + r2
        print(f'{B[b2][r2]} : {idx2:4} : Not separated')
    else:
      print('[Separated from all rows]')

    if onces:
      c = Counter()
      for b2, r2, s in onces:
        c.update((e, 1) for e in s)

    if multis:
      for b2, r2, s in multis:
        c.update((e, len(s)) for e in s)

    print ('Separations by column')
    for col in range(n):
      print ('  -', col, end=' ')
      for count in range(n):
        if (col,count) in c:
          print (f'{count:2}:{c[(col,count)]:<3}', end=' ')
      print()

    

  pass


def main():
  n, k = 8, 3
  A = load_pa('pa_8_choose_3_unfinished.txt')

  block_size = get_block_size(n, k, A)
  B = make_blocks(A, block_size)

  # for block in B:
  #   for row in block:
  #     print(get_mask(n, k, row))
  #   print('---')

  # for _ in range(1):
  while True:
    block_print(n, k, B, hide_covered=True)
    inspect_row(n, k, B, 557)
    mutate_row(n, k, B, 557)

    usr = pick_option('What do?', {
      '?': 'Help',
      'i': 'Inspect a row',
      'm': 'Mutate a row',
      'u': 'Print uncovered rows',
      'a': 'Print all rows',
      'p': 'Print a summary',
      # 's': 'Save changes'
    })

    if usr == '?':
      print ('No help for you')
    elif usr == 'i':
      usr = pick_range('Select a row number to inspect: ', len(A))
      inspect_row(n, k, B, usr)
    elif usr == 'm':
      usr = pick_range('Select a row number to mutate: ', len(A))
      mutate_row(n, k, B, usr)
    elif usr == 'u':
      block_print(n, k, B, hide_covered=True)
    elif usr == 'a':
      block_print(n, k, B, hide_covered=False)
    # elif usr == 's':
    #   print ('Not implemented')
    else:
      print ('Not a valid option:', usr)


main()