import json
import unittest
from math import factorial
from pathlib import Path

import numpy as np

from spectral.first.chebyshev_spectra import band_matrix, determinant, q_matrix
from spectral.second.phase_d import (compare_direct_spectrum, d2_band_determinant,
    d2_q_matrix, dn1_candidate_spectrum, phase_d_experiment, reconstructed_adjacency_eigenvalues)
from spectral.second.young_irreps import (irrep_dimension, partitions_of_n,
    standard_tableaux, verify_coxeter_relations)


class YoungRepresentationTests(unittest.TestCase):
    def test_dimensions_and_tableaux(self):
        for n in range(2, 7):
            shapes = list(partitions_of_n(n))
            self.assertEqual(sum(irrep_dimension(shape) ** 2 for shape in shapes), factorial(n))
            for shape in shapes:
                self.assertEqual(len(standard_tableaux(shape)), irrep_dimension(shape))

    def test_coxeter_relations(self):
        for n in range(2, 7):
            for shape in partitions_of_n(n):
                verify_coxeter_relations(shape)


class PhaseDTests(unittest.TestCase):
    def test_reconstruction_invariants(self):
        for n in range(3, 6):
            for d in range(1, n + 1):
                record = phase_d_experiment(n, d)
                values = reconstructed_adjacency_eigenvalues(record)
                degree = factorial(n) - record["ball_size"]
                self.assertEqual(len(values), factorial(n))
                self.assertAlmostEqual(float(values.sum()), 0.0, places=7)
                self.assertAlmostEqual(float(values @ values), factorial(n) * degree, places=6)

    def test_phase_abc_spectra_when_present(self):
        path = Path(__file__).resolve().parents[1] / "first/spectral/results/phase_abc.json"
        if not path.exists():
            self.skipTest("Phase A--C result file is not present")
        for prior in json.loads(path.read_text()):
            n, d = int(prior["n"]), int(prior["d"])
            if n <= 5 and "spectrum" in prior:
                compare_direct_spectrum(phase_d_experiment(n, d), prior)

    def test_standard_and_sign_blocks(self):
        for n in range(3, 6):
            for d in range(1, n + 1):
                record = phase_d_experiment(n, d)
                standard = next(block for block in record["partitions"] if block["partition"] == [n - 1, 1])
                q_values = np.linalg.eigvalsh(q_matrix(n, d - 1).astype(float))
                q_values = np.delete(q_values, np.argmin(np.abs(q_values - record["ball_size"])))
                self.assertTrue(np.allclose(standard["eigenvalues"], q_values, atol=1e-8))
                sign = next(block for block in record["partitions"] if block["partition"] == [1] * n)
                self.assertAlmostEqual(sign["eigenvalues"][0], determinant(band_matrix(n, d - 1)))

    def test_d2_formulas(self):
        for n in range(2, 12):
            self.assertTrue(np.array_equal(d2_q_matrix(n), q_matrix(n, 1)))
            self.assertEqual(d2_band_determinant(n), determinant(band_matrix(n, 1)))

    def test_dn1_candidate_small_cases(self):
        for n in range(4, 6):
            record = phase_d_experiment(n, n - 1)
            expected = []
            for group in dn1_candidate_spectrum(n):
                expected.extend([group["value"]] * group["multiplicity"])
            self.assertTrue(np.allclose(np.sort(expected), reconstructed_adjacency_eigenvalues(record)))


if __name__ == "__main__":
    unittest.main()
