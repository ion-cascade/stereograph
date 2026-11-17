"""Exact evaluated mesh samples with stable edge and vertex correspondence."""

from dataclasses import dataclass
import hashlib

import numpy as np

from ..spatial.query import InvalidScene


@dataclass
class MeshSample:
    world: np.ndarray
    local: np.ndarray
    edges: np.ndarray
    signature: str
    groups: dict


def mesh_sample(obj, graph, groups=(), *, topology=True):
    evaluated = obj.evaluated_get(graph)
    requested = {key: obj.vertex_groups.get(key) for key in groups}
    if any(value is None for value in requested.values()):
        raise InvalidScene("fit_vertex_group_missing", f"A requested vertex group is missing from {obj.name!r}.")
    mesh = (evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
            if groups else evaluated.to_mesh())
    try:
        if mesh is None or len(mesh.vertices) == 0:
            raise InvalidScene("fit_surface_missing", f"{obj.name!r} has no evaluated mesh points.")
        total = len(mesh.vertices)
        coords = np.empty(3 * total, np.float32)
        mesh.vertices.foreach_get("co", coords)
        local = coords.reshape(-1, 3).astype(np.float64)
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
        world = local @ matrix[:3, :3].T + matrix[:3, 3]
        if not np.isfinite(world).all():
            raise InvalidScene("fit_surface_nonfinite", f"{obj.name!r} has nonfinite evaluated points.")
        weights = {}
        if requested:
            by_index = {group.index: key for key, group in requested.items()}
            weights = {key: np.zeros(total, np.float32) for key in requested}
            for vertex in mesh.vertices:
                for entry in vertex.groups:
                    if entry.group in by_index:
                        weights[by_index[entry.group]][vertex.index] = entry.weight
        edges = np.empty(2 * len(mesh.edges) if topology else 0, np.int32)
        if topology:
            mesh.edges.foreach_get("vertices", edges)
            loops = np.empty(len(mesh.loops), np.int32)
            mesh.loops.foreach_get("vertex_index", loops)
            counts = np.empty(len(mesh.polygons), np.int32)
            mesh.polygons.foreach_get("loop_total", counts)
            signature = hashlib.sha256(edges.tobytes() + loops.tobytes() + counts.tobytes()).hexdigest()
        else:
            signature = ""
        return MeshSample(world, local, edges.reshape(-1, 2), signature, weights)
    finally:
        evaluated.to_mesh_clear()


def edge_ratios(before, after, *, selection=None):
    if before.signature != after.signature or len(before.world) != len(after.world):
        raise InvalidScene("fit_topology_changed", "Evaluated vertex and edge correspondence changed.")
    edges = before.edges
    if selection is not None:
        edges = edges[selection[edges].all(axis=1)]
    lengths = np.linalg.norm(before.world[edges[:, 0]] - before.world[edges[:, 1]], axis=1)
    valid = lengths > 1e-8
    edges, lengths = edges[valid], lengths[valid]
    if not len(lengths):
        raise InvalidScene("fit_edges_missing", "There are no usable edges in the measured region.")
    moved = np.linalg.norm(after.world[edges[:, 0]] - after.world[edges[:, 1]], axis=1)
    ratios = moved / lengths
    low, high = int(ratios.argmin()), int(ratios.argmax())
    return dict(minimum=float(ratios[low]), maximum=float(ratios[high]), edges=len(ratios),
                short_edge=edges[low].tolist(), long_edge=edges[high].tolist(),
                short_witness=after.world[edges[low]].tolist(), long_witness=after.world[edges[high]].tolist())
