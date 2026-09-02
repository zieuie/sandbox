import unittest
from importlib import import_module
from itertools import permutations
from math import factorial

import numpy as np

from spectral.second.young_irreps import irrep_dimension, partitions_of_n

_module = import_module("spectral.5.theta_prime")


class ThetaPrimeTests(unittest.TestCase):
    def test_tiny_fourier_reconstruction_and_primal_convention(self):
        n = 3
        # Identity Fourier blocks give the positive-type delta function f(e)=1.
        matrices = {shape: np.eye(irrep_dimension(shape)) for shape in partitions_of_n(n)}
        group = list(permutations(range(n)))
        f_values = {p: _module.reconstruct_f(n, matrices, p) for p in group}
        identity = tuple(range(n))
        self.assertAlmostEqual(f_values[identity], 1.0)
        for p in group:
            if p != identity:
                self.assertAlmostEqual(f_values[p], 0.0, places=12)
        ordered, primal = _module.invariant_primal_matrix(n, f_values)
        self.assertTrue(np.allclose(primal, np.eye(factorial(n)) / factorial(n)))
        self.assertAlmostEqual(float(np.trace(primal)), 1.0)

    def test_p53_theta_prime_matches_known_value(self):
        result = _module.solve_theta_prime(5, 3, tolerance=1e-6)
        self.assertAlmostEqual(result["theta_prime_value"], 10.0, places=4)
        self.assertEqual(result["integer_upper_bound"], 10)
        self.assertLess(result["max_forbidden_edge_residual"], 1e-4)
        self.assertGreater(result["minimum_reconstructed_f_value"], -1e-5)


if __name__ == "__main__":
    unittest.main()
