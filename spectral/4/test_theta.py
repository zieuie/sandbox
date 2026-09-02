import unittest
from importlib import import_module

from spectral.third.weighted_sdp import forbidden_permutations

_theta = import_module("spectral.4.theta")
inverse_orbits = _theta.inverse_orbits
solve_theta = _theta.solve_theta
solve_unrestricted_weighted = _theta.solve_unrestricted_weighted
numerical_integer_upper_bound = _theta.numerical_integer_upper_bound


class ThetaTests(unittest.TestCase):
    def test_near_integer_reporting_is_not_overclaimed(self):
        self.assertEqual(numerical_integer_upper_bound(9.9999998), 10)
        self.assertEqual(numerical_integer_upper_bound(10.0000007), 10)

    def test_inverse_orbits_cover_forbidden_set(self):
        forbidden = forbidden_permutations(5, 3)
        orbits = inverse_orbits(forbidden)
        flattened = [p for orbit in orbits for p in orbit]
        self.assertEqual(set(flattened), set(forbidden))
        self.assertEqual(len(flattened), len(set(flattened)))

    def test_theta_normalization_extreme_graphs(self):
        # d=1: forbidden graph has no edges; theta is |S_n|.
        empty = solve_theta(3, 1)
        self.assertAlmostEqual(empty["theta_value"], 6.0, places=6)
        # d=n: every distinct pair is forbidden; theta is 1.
        complete = solve_theta(3, 3)
        self.assertAlmostEqual(complete["theta_value"], 1.0, places=6)
        self.assertLess(complete["max_constraint_residual"], 1e-6)

    def test_unrestricted_weighted_is_feasible(self):
        result = solve_unrestricted_weighted()
        self.assertGreaterEqual(result["minimum_block_eigenvalue"], -1.0 - 1e-8)
        self.assertGreater(result["number_of_variables"], 8)


if __name__ == "__main__":
    unittest.main()
