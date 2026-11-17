from time import monotonic
import unittest

import numpy as np

from stereograph_bridge.spatial.containment import _beyond_shared
from stereograph_bridge.spatial.distance import Surface, nearest
from stereograph_bridge.spatial.triangles import closest_pairs


def triangles(items):
    return np.asarray(items, dtype=float)


def surface(data):
    vertices = np.asarray(data, dtype=float).reshape(-1, 3)
    return Surface(vertices, np.arange(len(vertices)).reshape(-1, 3))


class SpatialMathTests(unittest.TestCase):
    def test_finite_edge_interior_minimum(self):
        a = triangles([[[-2, 0, 0], [2, 0, 0], [0, -2, -1]]])
        b = triangles([[[0, -1, 1], [0, 1, 1], [1, 0, 3]]])
        hit, crossing = closest_pairs(a, b, 1e-10)
        self.assertFalse(crossing[0])
        np.testing.assert_allclose(hit.a[0], [0, 0, 0])
        np.testing.assert_allclose(hit.b[0], [0, 0, 1])

    def test_transverse_segment_and_coplanar_contact(self):
        a = triangles([[[-2, -1, 0], [2, -1, 0], [0, 2, 0]]])
        b = triangles([[[0, -2, -1], [0, 2, -1], [0, 0, 2]]])
        hit, crossing = closest_pairs(a, b, 1e-10)
        self.assertTrue(crossing[0])
        self.assertEqual(hit.squared[0], 0)
        hit, crossing = closest_pairs(a, a + [.15, .15, 0], 1e-10)
        self.assertFalse(crossing[0])
        self.assertEqual(hit.squared[0], 0)

    def test_hierarchy_matches_exhaustive_search_and_swapped_order(self):
        rng = np.random.default_rng(658)
        first = rng.normal(0, .4, size=(73, 3, 3))
        second = rng.normal(0, .3, size=(67, 3, 3)) + [3, -1, .5]
        brute, _ = closest_pairs(np.repeat(first, len(second), axis=0),
                                  np.tile(second, (len(first), 1, 1)), 1e-10)
        a, b = surface(first), surface(second)
        ab = nearest(a, b, 1e-10, monotonic() + 10)
        ba = nearest(b, a, 1e-10, monotonic() + 10)
        self.assertAlmostEqual(ab.squared, brute.squared.min(), places=12)
        self.assertAlmostEqual(ba.squared, ab.squared, places=12)
        self.assertAlmostEqual(np.linalg.norm(ab.point_a - ab.point_b) ** 2, ab.squared, places=12)

    def test_manifold_adjacent_and_folded_overlap(self):
        a = triangles([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]])
        folded = triangles([[[1, 0, 0], [0, 0, 0], [.25, .25, 0]]])
        shared = np.zeros((1, 3, 3), dtype=bool)
        shared[0, 0, 1] = shared[0, 1, 0] = True
        count = np.array([2])
        self.assertTrue(_beyond_shared(a, folded, shared, count, 1e-10))
        folded[0, 2, 1] = -.25
        self.assertFalse(_beyond_shared(a, folded, shared, count, 1e-10))

    def test_topology_does_not_round_positive_separation_to_intersection(self):
        a = surface([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]])
        b = surface(a.triangles + [0, 0, 0.000001])
        hit = nearest(a, b, 1e-10, monotonic() + 5)
        self.assertAlmostEqual(hit.squared, 1e-12, places=20)
        self.assertFalse(hit.crossing)
