from sys import argv

def hamming_distance(u, v):
  ret = 0
  for a,b in zip(u,v):
    if a != b:
      ret += 1
  return ret

required_distance = int(argv[2])
A = []
with open(argv[1], 'r') as f:
  for line in f:
    if '#' in line:
      line = line[line.index('#')-1:]
    line = line.strip()
    line = [int(x) for x in line.split()]
    A.append(line)

ret = True
for ux, u in enumerate(A):
  for vx in range(ux):
    d = hamming_distance(u, A[vx])
    if d < required_distance:
      print(f'Poor distance {d} between {ux+1} and {vx+1}')
      ret = False

if ret:
  print('Verified!')
else:
  print('Failed!')

exit(ret)
