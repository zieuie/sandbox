import unittest
from math import factorial

import numpy as np

from spectral.chebyshev_spectra import (
    adjacency_matrix,
    band_matrix,
    chebyshev_distance,
    compose,
    determinant,
    displacement,
    experiment,
    generate_ball,
    inverse,
    permanent,
    q_matrix,
    standard_eigenvalues,
)


class PermutationTests(unittest.TestCase):
    def test_relative_displacement_identity(self):
        p = (2, 0, 3, 1)
        q = (1, 3, 0, 2)
        self.assertEqual(
            chebyshev_distance(p, q),
            displacement(compose(q, inverse(p))),
        )

    def test_right_invariance(self):
        p = (2, 0, 3, 1)
        q = (1, 3, 0, 2)
        gamma = (3, 1, 0, 2)
        self.assertEqual(
            chebyshev_distance(p, q),
            chebyshev_distance(compose(p, gamma), compose(q, gamma)),
        )


class MatrixTests(unittest.TestCase):
    def test_ball_permanent_and_q(self):
        for n in range(1, 6):
            for r in range(n):
                ball = list(generate_ball(n, r))
                self.assertEqual(len(ball), permanent(band_matrix(n, r)))
                q = q_matrix(n, r, ball)
                self.assertTrue(np.array_equal(q, q.T))
                self.assertTrue(np.all(q.sum(axis=0) == len(ball)))
                self.assertTrue(np.all(q.sum(axis=1) == len(ball)))

    def test_exact_determinant(self):
        self.assertEqual(determinant(np.array([[2, 3], [5, 7]])), -1)
        self.assertEqual(determinant(np.array([[1, 2], [2, 4]])), 0)

    def test_extreme_graphs(self):
        for n in range(2, 5):
            _, complete = adjacency_matrix(n, 1)
            self.assertEqual(int(complete.sum()), factorial(n) * (factorial(n) - 1))
            _, empty = adjacency_matrix(n, n)
            self.assertEqual(int(empty.sum()), 0)

    def test_standard_and_sign_predictions(self):
        for n in range(3, 6):
            for d in range(1, n + 1):
                result = experiment(n, d)
                full = []
                for group in result["spectrum"]:
                    full.extend([group["value"]] * group["multiplicity"])
                full = np.asarray(full)
                self.assertLess(np.min(np.abs(full - result["sign_adjacency_eigenvalue"])), 1e-7)
                q = q_matrix(n, d - 1)
                predicted = -standard_eigenvalues(q, result["ball_size"])
                for value in predicted:
                    self.assertLess(np.min(np.abs(full - value)), 1e-7)


if __name__ == "__main__":
    unittest.main()
