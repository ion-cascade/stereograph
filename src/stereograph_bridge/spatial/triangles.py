"""Batched finite triangle-pair witnesses in double precision."""

import numpy as np


EDGES = ((0, 1), (1, 2), (2, 0))


def dot(a, b):
    return np.einsum("...i,...i->...", a, b)


def unit_normals(triangles):
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    return normal / np.sqrt(dot(normal, normal))[:, None]


def in_triangle(point, triangle, normal, epsilon):
    start = triangle
    edges = triangle[:, (1, 2, 0)] - start
    height = dot(np.cross(edges, point[:, None, :] - start), normal[:, None, :])
    return np.all(height >= -epsilon * np.sqrt(dot(edges, edges)), axis=1)


def interpolate(start, delta, parameter):
    return start + delta * parameter[:, None]


def project_parameter(numerator, denominator):
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator != 0)


class Witnesses:
    def __init__(self, count):
        self.squared = np.full(count, np.inf)
        self.a = np.zeros((count, 3))
        self.b = np.zeros((count, 3))

    def consider(self, a, b, valid=True):
        delta = a - b
        squared = dot(delta, delta)
        better = (squared < self.squared) & valid
        self.squared[better] = squared[better]
        self.a[better] = a[better]
        self.b[better] = b[better]


def plane_hits(source, target, normal, epsilon):
    """Yield finite edge-plane intersections that lie on the target face."""
    side = dot(source - target[:, :1], normal[:, None])
    for i, j in EDGES:
        denominator = side[:, i] - side[:, j]
        parameter = project_parameter(side[:, i], denominator)
        points = interpolate(source[:, i], source[:, j] - source[:, i], parameter)
        valid = ((denominator != 0) & (parameter >= 0) & (parameter <= 1)
                 & in_triangle(points, target, normal, epsilon))
        yield points, valid


def intersections(a, b, normal_a, normal_b, epsilon):
    """Crossing requires a transverse overlap segment through both interiors."""
    da = dot(a - b[:, :1], normal_b[:, None])
    db = dot(b - a[:, :1], normal_a[:, None])
    across = ((da.min(axis=1) < -epsilon) & (da.max(axis=1) > epsilon)
              & (db.min(axis=1) < -epsilon) & (db.max(axis=1) > epsilon))
    first = np.zeros((len(a), 3))
    seen = np.zeros(len(a), dtype=bool)
    crossing = np.zeros(len(a), dtype=bool)
    for source, target, normal in ((a, b, normal_b), (b, a, normal_a)):
        for point, valid in plane_hits(source, target, normal, epsilon):
            delta = point - first
            crossing |= across & valid & seen & (dot(delta, delta) > epsilon ** 2)
            new = valid & ~seen
            first[new] = point[new]
            seen |= valid
    return first, seen, crossing


def point_faces(source, target, normal, epsilon, witness, swap=False):
    for index in range(3):
        point = source[:, index]
        projected = point - dot(point - target[:, 0], normal)[:, None] * normal
        a, b = (projected, point) if swap else (point, projected)
        witness.consider(a, b, in_triangle(projected, target, normal, epsilon))


def edge_pairs(a, b, witness):
    for i, j in EDGES:
        p = a[:, i]
        u = a[:, j] - p
        uu = dot(u, u)
        for k, l in EDGES:
            q = b[:, k]
            v = b[:, l] - q
            vv = dot(v, v)
            w = q - p
            normal = np.cross(u, v)
            determinant = dot(normal, normal)
            s = project_parameter(dot(np.cross(w, v), normal), determinant)
            t = project_parameter(dot(np.cross(w, u), normal), determinant)
            interior = (determinant != 0) & (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
            witness.consider(interpolate(p, u, s), interpolate(q, v, t), interior)

            for point in (p, a[:, j]):
                along = np.clip(project_parameter(dot(point - q, v), vv), 0, 1)
                witness.consider(point, interpolate(q, v, along))
            for point in (q, b[:, l]):
                along = np.clip(project_parameter(dot(point - p, u), uu), 0, 1)
                witness.consider(interpolate(p, u, along), point)


def closest_pairs(a, b, epsilon):
    """Return the exact feature minimum for each nondegenerate triangle pair."""
    normal_a, normal_b = unit_normals(a), unit_normals(b)
    point, found, crossing = intersections(a, b, normal_a, normal_b, epsilon)
    witness = Witnesses(len(a))
    witness.consider(point, point, found)
    unknown = ~found
    if unknown.any():
        other = Witnesses(int(unknown.sum()))
        a, b = a[unknown], b[unknown]
        point_faces(a, b, normal_b[unknown], epsilon, other)
        point_faces(b, a, normal_a[unknown], epsilon, other, swap=True)
        edge_pairs(a, b, other)
        witness.squared[unknown] = other.squared
        witness.a[unknown] = other.a
        witness.b[unknown] = other.b
    return witness, crossing
