"""Conservative containment from closed, oriented manifold surfaces."""

from time import monotonic

import numpy as np

from .distance import InspectionLimit, bounds_distance
from .triangles import (EDGES, closest_pairs, dot, in_triangle, interpolate, plane_hits,
                        project_parameter, unit_normals)


def _validate_connectivity(surface):
    """Validate edge pairing and exactly one incident fan at every vertex."""
    source = surface.indices.ravel()
    destination = surface.indices[:, (1, 2, 0)].ravel()
    if surface.degenerate:
        return "degenerate_triangles"
    undirected = np.sort(np.column_stack((source, destination)), axis=1)
    _, inverse, counts = np.unique(undirected, axis=0, return_inverse=True, return_counts=True)
    if not np.all(counts == 2):
        return "open_or_nonmanifold_edges"
    halfedges = np.argsort(inverse).reshape(-1, 2)
    a, b = halfedges.T
    if not np.all(source[a] == destination[b]):
        return "inconsistent_orientation"
    twins = np.empty_like(source)
    twins[a], twins[b] = b, a
    around_vertex = twins - twins % 3 + (twins + 1) % 3
    visited = np.zeros(len(source), dtype=bool)
    fans = set()
    for index in range(len(source)):
        if visited[index]:
            continue
        vertex = int(source[index])
        if vertex in fans:
            return "nonmanifold_vertex"
        fans.add(vertex)
        next_edge = index
        while not visited[next_edge]:
            visited[next_edge] = True
            next_edge = int(around_vertex[next_edge])
    return None


def _possible_self_pairs(surface, epsilon, deadline):
    pending = [(surface.root, surface.root)]
    while pending:
        if monotonic() >= deadline:
            raise InspectionLimit("The closed shell could not be validated within the deadline.")
        a, b = pending.pop()
        if bounds_distance(a, b) > epsilon ** 2:
            continue
        na, nb = len(a.indices), len(b.indices)
        if na <= 12 and nb <= 12:
            left = np.repeat(a.indices, nb)
            right = np.tile(b.indices, na)
            keep = (left < right) if a is b else (left != right)
            left, right = left[keep], right[keep]
            gap = np.maximum(np.maximum(surface.lower[left] - surface.upper[right],
                                        surface.lower[right] - surface.upper[left]), 0)
            keep = dot(gap, gap) <= epsilon ** 2
            yield left[keep], right[keep]
        elif a is b:
            left, right = surface.split(a)
            pending.extend(((left, left), (left, right), (right, right)))
        elif na > 12 and (nb <= 12 or na >= nb):
            pending.extend((child, b) for child in surface.split(a))
        else:
            pending.extend((a, child) for child in surface.split(b))


def _self_intersection(surface, epsilon, deadline):
    left, right, count = [], [], 0
    for a, b in _possible_self_pairs(surface, epsilon, deadline):
        left.append(a)
        right.append(b)
        count += len(a)
        if count >= 1024:
            if _invalid_overlap(surface, np.concatenate(left), np.concatenate(right), epsilon):
                return True
            left, right, count = [], [], 0
    return bool(count and _invalid_overlap(surface, np.concatenate(left), np.concatenate(right), epsilon))


def _invalid_overlap(surface, left, right, epsilon):
    first, second = surface.indices[left], surface.indices[right]
    shared = first[:, :, None] == second[:, None, :]
    count = shared.any(axis=2).sum(axis=1)
    a, b = surface.triangles[left], surface.triangles[right]
    checks, crossing = closest_pairs(a, b, epsilon)
    if crossing.any() or np.any((count == 0) & (checks.squared <= epsilon ** 2)) or np.any(count == 3):
        return True
    near = (count > 0) & (checks.squared <= epsilon ** 2)
    return bool(near.any() and _beyond_shared(a[near], b[near], shared[near], count[near], epsilon))


def _beyond_shared(a, b, shared, count, epsilon):
    """Disallow adjacent triangles touching away from their shared vertex or edge."""
    sides = np.argsort(~shared.any(axis=2), axis=1)[:, :2]
    row = np.arange(len(a))
    first, last = a[row, sides[:, 0]], a[row, sides[:, 1]]
    edge = last - first

    def new_point(points, valid):
        offset = points - first
        t = np.clip(project_parameter(dot(offset, edge), dot(edge, edge)), 0, 1)
        delta = offset - edge * (t * (count == 2))[:, None]
        return np.any(valid & (dot(delta, delta) > epsilon ** 2))

    normal_a, normal_b = unit_normals(a), unit_normals(b)
    # A non-coplanar pair shares its full edge by construction. A pair sharing a
    # single vertex can still meet elsewhere along the plane intersection line.
    for src, dst, normal in ((a, b, normal_b), (b, a, normal_a)):
        for points, valid in plane_hits(src, dst, normal, epsilon):
            if new_point(points, valid & (count == 1)):
                return True

    coplanar = (np.max(np.abs(dot(b - a[:, :1], normal_a[:, None])), axis=1) <= epsilon
                ) & (np.max(np.abs(dot(a - b[:, :1], normal_b[:, None])), axis=1) <= epsilon)
    if not coplanar.any():
        return False
    for src, dst, normal in ((a, b, normal_b), (b, a, normal_a)):
        for i in range(3):
            point = src[:, i]
            if new_point(point, coplanar & in_triangle(point, dst, normal, epsilon)):
                return True
    for i, j in EDGES:
        p, u = a[:, i], a[:, j] - a[:, i]
        for k, l in EDGES:
            q, v = b[:, k], b[:, l] - b[:, k]
            normal = np.cross(u, v)
            determinant = dot(normal, normal)
            s = project_parameter(dot(np.cross(q - p, v), normal), determinant)
            t = project_parameter(dot(np.cross(q - p, u), normal), determinant)
            valid = coplanar & (determinant != 0) & (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
            if new_point(interpolate(p, u, s), valid):
                return True
    return False


def _component_points(surface):
    used = np.unique(surface.indices)
    parent = np.arange(len(surface.vertices))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    for a, b, c in surface.indices:
        first = root(a)
        parent[root(b)] = first
        parent[root(c)] = first
    return surface.vertices[sorted({root(i) for i in used})]


def _winding(point, container):
    a, b, c = (container.triangles - point).transpose(1, 0, 2)
    u, v, w = (np.sqrt(dot(item, item)) for item in (a, b, c))
    numerator = dot(a, np.cross(b, c))
    denominator = u * v * w + dot(a, b) * w + dot(b, c) * u + dot(c, a) * v
    return float(np.arctan2(numerator, denominator).sum() / (2 * np.pi))


def relation(subject, container, boundary, epsilon, deadline):
    """Report whether the subject's entire surface is inside the container."""
    if np.any(subject.root.low < container.root.low - epsilon) or np.any(subject.root.high > container.root.high + epsilon):
        return {"state": "not_contained", "reason": "outside_container_bounds"}
    if boundary:
        return {"state": "indeterminate", "reason": "boundaries_meet"}
    if reason := _validate_connectivity(container):
        return {"state": "indeterminate", "reason": reason}
    reference = container.triangles - container.root.low
    signed_volume = float(dot(reference[:, 0], np.cross(reference[:, 1], reference[:, 2])).sum() / 6)
    scale = max(container.root.high - container.root.low)
    if abs(signed_volume) <= epsilon * scale ** 2:
        return {"state": "indeterminate", "reason": "zero_or_cancelling_volume"}
    try:
        if _self_intersection(container, epsilon, deadline):
            return {"state": "indeterminate", "reason": "self_intersection"}
    except InspectionLimit:
        return {"state": "indeterminate", "reason": "topology_check_deadline"}
    for point in _component_points(subject):
        if monotonic() >= deadline:
            return {"state": "indeterminate", "reason": "containment_deadline"}
        winding = abs(_winding(point, container))
        if winding <= 1e-6:
            return {"state": "not_contained", "reason": "exterior_component"}
        if abs(winding - 1) > 1e-6:
            return {"state": "indeterminate", "reason": "ambiguous_winding"}
    return {"state": "contained", "reason": "all_surface_components_inside"}
