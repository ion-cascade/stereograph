"""Nearest finite surfaces using a best-first pair of AABB hierarchies."""

from dataclasses import dataclass
import heapq
from itertools import count
from time import monotonic

import numpy as np

from .triangles import closest_pairs, dot


class InspectionLimit(RuntimeError):
    """The query could not certify its result within the request deadline."""


@dataclass
class Node:
    indices: np.ndarray
    low: np.ndarray
    high: np.ndarray
    children: tuple = ()


class Surface:
    def __init__(self, vertices, indices):
        all_triangles = vertices[indices]
        cross = np.cross(all_triangles[:, 1] - all_triangles[:, 0], all_triangles[:, 2] - all_triangles[:, 0])
        valid = dot(cross, cross) > 0
        self.vertices = vertices
        self.indices = indices[valid]
        self.triangles = all_triangles[valid]
        self.source_indices = np.flatnonzero(valid)
        self.degenerate = int((~valid).sum())
        self.lower = self.triangles.min(axis=1)
        self.upper = self.triangles.max(axis=1)
        self.centers = (self.lower + self.upper) / 2
        self.root = self.node(np.arange(len(self.triangles))) if len(self.triangles) else None

    def node(self, indices):
        return Node(indices, self.lower[indices].min(axis=0), self.upper[indices].max(axis=0))

    def split(self, node):
        if not node.children:
            positions = self.centers[node.indices]
            axis = int(np.ptp(positions, axis=0).argmax())
            offset = len(positions) // 2
            ids = node.indices[np.argpartition(positions[:, axis], offset)]
            node.children = (self.node(ids[:offset]), self.node(ids[offset:]))
        return node.children


def bounds_distance(a, b):
    gap = np.maximum(np.maximum(a.low - b.high, b.low - a.high), 0)
    return float(gap @ gap)


@dataclass
class Closest:
    squared: float
    point_a: np.ndarray
    point_b: np.ndarray
    indices: tuple[int, int]
    crossing: bool = False


def _solve_batch(a, b, leaves, epsilon, best):
    ai = np.concatenate([np.repeat(x.indices, len(y.indices)) for x, y in leaves])
    bi = np.concatenate([np.tile(y.indices, len(x.indices)) for x, y in leaves])
    # Most leaf pairs have only a small subset of plausible triangle pairs.
    gap = np.maximum(np.maximum(a.lower[ai] - b.upper[bi], b.lower[bi] - a.upper[ai]), 0)
    upper = np.sqrt(best.squared) if best else np.inf
    valid = dot(gap, gap) <= (upper + epsilon) ** 2
    ai, bi = ai[valid], bi[valid]
    if not len(ai):
        return best
    witness, crossing = closest_pairs(a.triangles[ai], b.triangles[bi], epsilon)
    hits = np.flatnonzero(crossing)
    index = int(hits[0]) if len(hits) else int(witness.squared.argmin())
    minimum = float(witness.squared[index])
    if len(hits) or best is None or minimum < best.squared:
        return Closest(minimum, witness.a[index], witness.b[index], (int(ai[index]), int(bi[index])), bool(len(hits)))
    return best


def nearest(a, b, epsilon, deadline):
    """Prune only with a lower bound on every omitted pair."""
    order = count()
    heap = [(bounds_distance(a.root, b.root), next(order), a.root, b.root)]
    leaves = []
    leaf_pairs = 0
    best = None
    while heap:
        if monotonic() >= deadline:
            raise InspectionLimit("The spatial search reached its deadline.")
        bound, _, left, right = heapq.heappop(heap)
        limit = (np.sqrt(best.squared) + epsilon) ** 2 if best else np.inf
        if bound > limit:
            break
        n, m = len(left.indices), len(right.indices)
        if n <= 12 and m <= 12:
            leaves.append((left, right))
            leaf_pairs += n * m
            if leaf_pairs >= 1024:
                best = _solve_batch(a, b, leaves, epsilon, best)
                if best and best.crossing:
                    return best
                leaves.clear()
                leaf_pairs = 0
            continue
        if n > 12 and (m <= 12 or max(left.high - left.low) >= max(right.high - right.low)):
            children = ((item, right) for item in a.split(left))
        else:
            children = ((left, item) for item in b.split(right))
        for u, v in children:
            bound = bounds_distance(u, v)
            if bound <= limit:
                heapq.heappush(heap, (bound, next(order), u, v))
    if leaves:
        best = _solve_batch(a, b, leaves, epsilon, best)
    return best
