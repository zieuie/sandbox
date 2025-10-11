P(6, 3)


5 3 4 0 2 1 
4 5 2 3 1 0 
3 4 2 1 5 0 
3 5 2 1 0 4 
5 2 3 4 1 0 
5 0 3 2 4 1 
5 2 3 1 0 4 
5 1 0 3 4 2 
5 2 1 4 0 3 
3 1 2 0 5 4 
2 4 5 3 1 0 
2 3 5 1 4 0 
2 4 5 1 0 3 
0 3 2 4 5 1 
2 4 1 5 0 3 
2 4 1 0 5 3 
2 1 5 4 3 0 
2 1 5 3 0 4 
2 1 5 0 3 4 
2 1 0 3 5 4 

P(6,3) -> P(9,3)

P(9,3) <= (9 choose 3) * (6 choose 3) * 1
P(6,3) <= (6 choose 3) * 1

P(k*d, d) <= (i choose d) for i in (0..n, skipping by d)

Combinatorics
Permutation array problem
 -> under Chebychev Metric
 - Hal Sudborough

6 7 8 2 1 0 3 5 4 
6 7 2 8 1 0 3 5 4 
0 0 1 1 0 0 0 0 0 = 2 (Hamming Distance)


6 7 2 1 8 0 3 5 4 
...
2 1 0 6 3 7 5 4 8 
2 1 0 6 3 5 7 4 8 


0 1 2 3 4

y = 2*x  (mod 5)

x  | y = 2*x (mod 5) 
0  | 0
1  | 2
2  | 4
3  | 1
4  | 3



x  | y = 2*x (mod 6) 
0  | 0
1  | 2
2  | 4
3  | 0
4  | 2
5  | 4

3 = 2 * x (mod 6)

totient(n) := the number of numbers < n that are coprime to n

coprime := gcd(a, b) = 1 
        := there is no x > 1 that divides a and b


n | totient(n)
2 | 1      (1)
3 | 2      (1 and 2)
4 | 2      (1 and 3)
5 | 4      (1, 2, 3, 4)
6 | 2      (1, 5)
7 | 6      (1 through 6)
8 | 4      (1, 3, 5, 7)

3 and 6 are not coprime because
3 | 3 and 3 | 6 and 3 > 1

2 and 7 are coprime because
  nothing > 1 can divide 2 and 7.

totient(p) = p-1  (when p is prime)

prime := n is prime if nothing > 1 divides n.

Fundamental theorem of arithmetic:
  Any natural number n has a unique prime factorization


n | prime factors
2 | 2      [1]
3 | 3      [1]
4 | 2*2    [2]
5 | 5      [1]
6 | 2*3    [1, 1]
7 | 7      [1]
8 | 2*2*2  [3]
9 | 3*3    [2]
10 | 2*5   [1, 1]


n | totient(n)
2 | 1      (1)
3 | 2      (1 and 2)
4 | 2      (1 and 3)
5 | 4      (1, 2, 3, 4)
6 | 2      (1, 5)
7 | 6      (1 through 6)
8 | 4      (1, 3, 5, 7)

n | prime factors
2 | 2      [1]
3 | 3      [1]
4 | 2*2    [2]
5 | 5      [1]
6 | 2*3    [1, 1]
7 | 7      [1]
8 | 2*2*2  [3]
9 | 3*3    [2]
10 | 2*5   [1, 1]


n | divisors(n)
2 | 2
3 | 2
4 | 3
5 | 2
6 | 4
7 | 2
8 | 4

divisors(p) = 2  (for p prime)
if u and v are coprime:
  divisors (u*v) = divisors(u) * divisors(v)




totient(8):

candidates:
1 | yes
2 | no because gcd(2,8) = 2 
3 | yes
4 | no because gcd(4,8) = 4
5 | yes
6 | no because gcd(6,8) = 2
7 | yes


totient(6):


1 yes
2 no
3 no 
4 no
5 yes



divisors(p) = 2  (for p prime)
if u and v are coprime:
  divisors(u*v) = divisors(u) * divisors(v)



n | divisors(n)
2 | 2
3 | 2
4 | 3
5 | 2
6 | 4
7 | 2
8 | 4

6 = 2*3
2 coprime 3
diviors(2) = 2
diviors(3) = 2
divisors(6) = 2*2 = 4

         2  3  5  7  11  13  17  19 ...
u = 15 = 0  1  1 ...
v = 10 = 1  0  1 ...
w = 75 = 0  1  2 ...
x = 3  = 0  1  0 ...


75 -> 3*25 -> 3*5*5



         2  3  4  5  6  11  13  17  19 ...
x = 3  = 0  1  0  0 ...
u = 4  = 2  0  1  0 ...
v = 8  = 3  0  2  0 ...
w = 12 = 2  1  0  0 ...


      2  3  5
75 =  0  1  2
---------------
1  =  0  0  0
3  =  0  1  0
5  =  0  0  1
15 =  0  1  1
25 =  0  0  2
75 =  0  1  2

         2  3  4  5  6  11  13  17  19 ...
65                1          1

if u coprime v:
    divisors(u*v) = divisors(u) * divisors(v)




      2  3  5
25 =  0  0  2


25 = 5*5 = 5^2


for n prime:
  a ^ totient(n) == 1    (mod n)


