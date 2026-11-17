"""Read evaluated Blender geometry and report reproducible frame witnesses."""

from math import floor, isfinite, sqrt
from time import monotonic

import numpy as np

from ..wire import failure, success
from .containment import relation
from .distance import InspectionLimit, Surface, nearest
from .evidence import VIEWS, diagram

MAX_SAMPLES = 128


class InvalidScene(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _frames(scene, parameters):
    frames = parameters.get("frames")
    frame_range = parameters.get("frame_range")
    if frames is not None and frame_range is not None:
        raise ValueError("Use explicit frames or a frame range.")
    if frame_range is not None:
        if not isinstance(frame_range, list) or len(frame_range) != 3:
            raise ValueError("Provide a range with start, end and step.")
        begin, end, step = frame_range
        if not all(_number(item) for item in frame_range) or not step > 0 or begin > end:
            raise ValueError("Frame ranges must be finite, ordered and use a positive step.")
        ratio = (end - begin) / step
        if not isfinite(ratio) or ratio >= MAX_SAMPLES:
            raise ValueError(f"Sample at most {MAX_SAMPLES} frames per request.")
        frames = [min(end, begin + i * step) for i in range(floor(ratio + 1e-10) + 1)]
        if abs(frames[-1] - end) > 1e-9:
            frames.append(end)
    if frames is None:
        frames = [scene.frame_current + scene.frame_subframe]
    if (not isinstance(frames, list) or not 1 <= len(frames) <= MAX_SAMPLES
            or not all(_number(frame) and -1048574 <= frame <= 1048574 for frame in frames)):
        raise ValueError(f"Provide up to {MAX_SAMPLES} finite Blender frames in the supported range.")
    return list(dict.fromkeys(frames))


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def _pairs(parameters):
    pairs = parameters.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Provide one or more named object pairs.")
    for pair in pairs:
        if (not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(name, str) and name for name in pair)
                or pair[0] == pair[1]):
            raise ValueError("Each pair must name two distinct mesh objects.")
    return list(dict.fromkeys(map(tuple, pairs)))


def _evaluated_surface(obj, graph):
    evaluated = obj.evaluated_get(graph)
    if not evaluated.is_evaluated:
        raise InvalidScene("object_not_evaluated", f"{obj.name!r} has no evaluated object in this view layer.")
    mesh = evaluated.to_mesh()
    try:
        if mesh is None:
            raise InvalidScene("no_surface", f"{obj.name!r} has no evaluated mesh.")
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles:
            raise InvalidScene("no_surface", f"{obj.name!r} has no evaluated triangles.")
        vertices = np.empty(3 * len(mesh.vertices), dtype=np.float32)
        indices = np.empty(3 * len(mesh.loop_triangles), dtype=np.int32)
        mesh.vertices.foreach_get("co", vertices)
        mesh.loop_triangles.foreach_get("vertices", indices)
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
        world = vertices.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        if not np.all(np.isfinite(world)):
            raise InvalidScene("invalid_surface", f"{obj.name!r} has nonfinite evaluated coordinates.")
        surface = Surface(world, indices.reshape(-1, 3))
        if not len(surface.triangles):
            raise InvalidScene("no_surface", f"{obj.name!r} has only degenerate triangles.")
        return surface
    finally:
        evaluated.to_mesh_clear()


def _measure(a, b, pair, frame, evaluated_frame, tolerance, deadline):
    extent = max(abs(a.root.low).max(), abs(a.root.high).max(),
                 abs(b.root.low).max(), abs(b.root.high).max(), 1.)
    epsilon = max(1e-10, float(np.spacing(extent)) * 64)
    witness = nearest(a, b, epsilon, deadline)
    distance = sqrt(witness.squared)
    boundary = distance <= epsilon
    if witness.crossing:
        surface_relation = "crossing"
    elif boundary:
        surface_relation = "touching"
    elif distance <= tolerance:
        surface_relation = "within_tolerance"
    else:
        surface_relation = "separated"
    sample = dict(
        pair=list(pair), frame=frame, evaluated_frame=evaluated_frame,
        surface_relation=surface_relation, distance=distance, tolerance=tolerance,
        numerical_epsilon=epsilon, surfaces_intersect=boundary,
        transverse_crossing=witness.crossing,
        containment={"a_in_b": relation(a, b, boundary, epsilon, deadline),
                     "b_in_a": relation(b, a, boundary, epsilon, deadline)},
        witness={"a": witness.point_a.tolist(), "b": witness.point_b.tolist(),
                 "a_triangle": int(a.source_indices[witness.indices[0]]),
                 "b_triangle": int(b.source_indices[witness.indices[1]])},
        triangle_counts=[len(a.triangles), len(b.triangles)],
        degenerate_triangles_ignored=[a.degenerate, b.degenerate],
    )
    return sample, witness


def _validate(scene, parameters):
    pairs = _pairs(parameters)
    frames = _frames(scene, parameters)
    if len(frames) * len(pairs) > MAX_SAMPLES:
        raise ValueError(f"Request at most {MAX_SAMPLES} pair/frame samples at a time.")
    tolerance = parameters.get("tolerance", .001)
    if not _number(tolerance) or tolerance < 0:
        raise ValueError("Use a finite, nonnegative contact tolerance in Blender units.")
    views = parameters.get("views", [])
    if not isinstance(views, list) or not all(isinstance(view, str) and view in VIEWS for view in views):
        raise ValueError("Choose views from front, side, top and isometric.")
    views = list(dict.fromkeys(views))
    if len(pairs) * len(views) > 8:
        raise ValueError("Request at most eight annotated views per response.")
    image_frame = parameters.get("evidence_frame")
    if image_frame is None:
        image_frame = frames[0]
    elif image_frame not in frames or not views:
        raise ValueError("The evidence frame must be one of the sampled frames and views must be requested.")
    return pairs, frames, tolerance, views, image_frame


def inspect(bpy, parameters, seconds):
    scene = bpy.context.scene
    if scene is None or bpy.context.view_layer is None:
        return failure("no_scene", "An active scene and view layer are required.")
    if bpy.context.mode != "OBJECT":
        return failure("object_mode_required", "Use Object Mode for evaluated mesh inspection.")
    try:
        pairs, frames, tolerance, views, image_frame = _validate(scene, parameters)
    except (TypeError, ValueError, OverflowError) as exc:
        return failure("bad_spatial_request", str(exc))

    deadline = monotonic() + max(.01, seconds - .35)
    objects = {}
    for name in dict.fromkeys(name for pair in pairs for name in pair):
        obj = bpy.context.view_layer.objects.get(name)
        if obj is None:
            return failure("object_not_evaluated", f"{name!r} is unavailable in this view layer.")
        if obj.type != "MESH":
            return failure("not_a_mesh", f"{name!r} is not a mesh object.")
        objects[name] = obj

    start = scene.frame_current, scene.frame_subframe
    samples, images = [], []
    try:
        for frame in frames:
            if monotonic() >= deadline:
                raise InspectionLimit("The spatial inspection reached its deadline.")
            whole = floor(frame)
            current = frame - whole
            if scene.frame_current != whole or abs(scene.frame_subframe - current) > 1e-9:
                scene.frame_set(whole, subframe=current)
            graph = bpy.context.evaluated_depsgraph_get()
            evaluated_frame = scene.frame_current_final
            cache = {}
            for pair in pairs:
                for name in pair:
                    if name not in cache:
                        cache[name] = _evaluated_surface(objects[name], graph)
                a, b = (cache[name] for name in pair)
                sample, witness = _measure(a, b, pair, frame, evaluated_frame, tolerance, deadline)
                samples.append(sample)
                if frame == image_frame:
                    for view in views:
                        images.append(dict(pair=list(pair), frame=frame, view=view, mime_type="image/svg+xml",
                                           svg=diagram(a, b, witness, sample, view, deadline)))
    except InvalidScene as exc:
        return failure(exc.code, str(exc))
    except InspectionLimit as exc:
        return failure("spatial_deadline", str(exc) + " Try a smaller selection or longer timeout.", "completed")
    finally:
        if (scene.frame_current, scene.frame_subframe) != start:
            scene.frame_set(start[0], subframe=start[1])

    units = scene.unit_settings
    return success(dict(scene=scene.name, file=bpy.data.filepath or None, view_layer=bpy.context.view_layer.name,
                        space="world", units="blender_units", unit_system=units.system,
                        meters_per_unit=units.scale_length if units.system != "NONE" else None,
                        starting_frame=start[0] + start[1], samples=samples, evidence=images))
