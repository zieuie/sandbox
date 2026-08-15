import unittest

import numpy as np

from spectral.third.weighted_sdp import (build_problem, cycle_type, forbidden_permutations,
    inverse, ordinary_hoffman, solve_weighted_problem, weight_classes, weighted_block)


class ClassTests(unittest.TestCase):
    def test_cycle_type(self):
        self.assertEqual(cycle_type((1, 0, 3, 2, 4)), (2, 2, 1))

    def test_classes_partition_forbidden_set_and_are_inverse_closed(self):
        for family in ("shell", "shell_cycle_type"):
            classes = weight_classes(5, 3, family)
            flattened = [p for _, members in classes for p in members]
            self.assertEqual(set(flattened), set(forbidden_permutations(5, 3)))
            self.assertEqual(len(flattened), len(set(flattened)))
            for _, members in classes:
                self.assertTrue(all(inverse(p) in set(members) for p in members))


class OptimizationTests(unittest.TestCase):
    def test_shell_solution_is_feasible_and_no_worse_than_ordinary(self):
        problem = build_problem(5, 3, "shell")
        result = solve_weighted_problem(problem)
        ordinary = ordinary_hoffman(problem)
        self.assertLessEqual(result["spectral_upper_bound_real"], ordinary["real_bound"] + 1e-6)
        self.assertGreaterEqual(result["minimum_block_eigenvalue"], -1.0 - 1e-8)
        weights = np.asarray(result["weights"])
        for shape in problem.partitions:
            self.assertGreaterEqual(np.linalg.eigvalsh(weighted_block(problem, shape, weights))[0], -1.0 - 1e-8)

    def test_cycle_refinement_is_no_worse_than_shell(self):
        shell = solve_weighted_problem(build_problem(5, 3, "shell"))
        refined = solve_weighted_problem(build_problem(5, 3, "shell_cycle_type"))
        self.assertLessEqual(refined["spectral_upper_bound_real"], shell["spectral_upper_bound_real"] + 1e-5)


if __name__ == "__main__":
    unittest.main()
