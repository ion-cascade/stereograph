"""Review authored dependencies and sampled clothing relationships."""

from collections import deque
from html import escape
from time import monotonic

import numpy as np
from mathutils import Vector

from ..review.native import inspect_motion, set_frame
from ..rigging.profile import frames
from ..spatial.distance import InspectionLimit
from ..spatial.query import InvalidScene
from ..spatial.evidence import VIEWS
from .mesh import edge_ratios, mesh_sample
from .profile import FitProfile


def action_name(owner):
    animation = getattr(owner, "animation_data", None)
    return animation.action.name if animation and animation.action else None


def describe(obj, representation=None):
    mods = []
    for position, modifier in enumerate(obj.modifiers):
        entry = dict(index=position, name=modifier.name, type=modifier.type,
                     viewport=modifier.show_viewport, render=modifier.show_render)
        for field in ("object", "target", "origin"):
            target = getattr(modifier, field, None)
            if target:
                entry[field] = target.name
        for field in ("vertex_group", "is_bound", "use_deform_preserve_volume"):
            if hasattr(modifier, field):
                entry[field] = getattr(modifier, field)
        if modifier.type == "CLOTH":
            cache = modifier.point_cache
            entry["cloth"] = dict(pin_group=modifier.settings.vertex_group_mass,
                                  baked=cache.is_baked, outdated=cache.is_outdated,
                                  external=cache.use_external, range=[cache.frame_start, cache.frame_end])
        group = getattr(modifier, "node_group", None)
        if group:
            entry["node_group"] = group.name
            entry["node_action"] = action_name(group)
            entry["node_inputs"] = sorted({socket.default_value.name for node in group.nodes
                                           if node.bl_idname == "GeometryNodeObjectInfo"
                                           for socket in [node.inputs.get("Object")]
                                           if socket and socket.default_value})
        mods.append(entry)
    geometry = obj.data if obj.type == "MESH" else None
    keys = geometry.shape_keys if geometry else None
    deform_names = {bone.name for mod in obj.modifiers if mod.type == "ARMATURE" and mod.object
                    for bone in mod.object.data.bones if bone.use_deform}
    parent = obj.parent
    return dict(object=obj.name, object_type=obj.type, representation=representation,
                parent=dict(object=parent.name, type=obj.parent_type, bone=obj.parent_bone or None) if parent else None,
                constraints=[dict(name=c.name, type=c.type, target=getattr(getattr(c, "target", None), "name", None))
                             for c in obj.constraints],
                action=action_name(obj), shape_key_action=action_name(keys),
                shape_keys=dict(count=len(keys.key_blocks), active=[key.name for key in keys.key_blocks
                                if abs(key.value) > 1e-6][:24],
                                drivers=len(keys.animation_data.drivers) if keys.animation_data else 0) if keys else None,
                vertex_groups=[group.name for group in obj.vertex_groups][:128],
                bone_weight_groups=sorted(deform_names & {group.name for group in obj.vertex_groups})[:128],
                modifiers=mods)


def _weight_summary(obj, groups):
    if not groups:
        return {}
    if obj.type != "MESH":
        return {group: {"status": "unresolved"} for group in groups}
    output, indexes = {}, {}
    for name in groups:
        group = obj.vertex_groups.get(name)
        if group is None:
            output[name] = {"status": "unresolved"}
            continue
        output[name] = dict(status="resolved", count=0, maximum=0., mean=0.)
        indexes[group.index] = name
    for vertex in obj.data.vertices:
        for item in vertex.groups:
            if item.group in indexes and item.weight > 0:
                row = output[indexes[item.group]]
                row["count"] += 1
                row["maximum"] = max(row["maximum"], item.weight)
                row["mean"] += item.weight
    for row in output.values():
        if row.get("count"):
            row["mean"] /= row["count"]
    return output


def _dependency_names(row):
    if row["parent"]:
        yield row["parent"]["object"]
    for constraint in row["constraints"]:
        if constraint["target"]:
            yield constraint["target"]
    for modifier in row["modifiers"]:
        for key in ("object", "target", "origin"):
            if key in modifier:
                yield modifier[key]
        yield from modifier.get("node_inputs", ())


def _dependencies(bpy, layers):
    known = {row["object"] for row in layers.values()}
    todo = deque((name, 1) for row in layers.values() if row["status"] == "resolved"
                 for name in sorted(set(_dependency_names(row))))
    output = {}
    while todo:
        name, level = todo.popleft()
        if name in known:
            continue
        if level > 3 or len(output) >= 32:
            return output, True
        known.add(name)
        obj = bpy.context.view_layer.objects.get(name)
        if obj is None:
            output[name] = dict(object=name, status="unresolved", depth=level)
            continue
        row = describe(obj)
        output[name] = dict(status="resolved", depth=level, **row)
        todo.extend((source, level+1) for source in sorted(set(_dependency_names(row))))
    return output, False


def evaluated_at(bpy, time, deadline):
    if monotonic() >= deadline:
        raise InspectionLimit("Clothing evaluation reached its deadline.")
    set_frame(bpy.context.scene, time)
    return bpy.context.evaluated_depsgraph_get()


def _landmark(mark, objects, graph, samples):
    obj = objects[mark.layer]
    if mark.vertex is None:
        return np.array(obj.evaluated_get(graph).matrix_world @ Vector(mark.point)), None
    if mark.layer not in samples:
        samples[mark.layer] = mesh_sample(obj, graph)
    mesh = samples[mark.layer]
    if mark.vertex >= len(mesh.world):
        raise InvalidScene("fit_landmark_missing", f"{obj.name!r} lacks the requested evaluated vertex.")
    return mesh.world[mark.vertex], mesh.signature


def _attachment(check, objects, graph, samples, baseline):
    a, topology_a = _landmark(check.a, objects, graph, samples)
    b, topology_b = _landmark(check.b, objects, graph, samples)
    inverse = objects[check.a.layer].evaluated_get(graph).matrix_world.to_3x3().inverted()
    relative = np.array(inverse @ Vector(b - a))
    topology = (topology_a, topology_b)
    if baseline is not None and baseline[1] != topology:
        raise InvalidScene("fit_topology_changed", "The evaluated landmark topology changed.")
    reference = baseline[0] if baseline is not None else relative
    drift = float(np.linalg.norm(relative - reference))
    gap = float(np.linalg.norm(b - a))
    return dict(gap=gap, drift=drift, witness=[a.tolist(), b.tolist()],
                status="outside_limits" if gap > check.max_gap or drift > check.max_drift else "within_limits"), (reference, topology)


def _matched_landmarks(first, last, view, label):
    anchors = np.array([first, last]) @ np.array(VIEWS[view][:2]).T
    center = anchors.reshape(-1, 2).mean(axis=0)
    scale = 85 / max(float(np.abs(anchors - center).max()), .0005)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="480" height="256" viewBox="0 0 480 256">',
             '<rect width="480" height="256" fill="#162133"/>',
             f'<text x="16" y="23" fill="#fff">{escape(label)} · {view}</text>']
    for i, (pair, heading) in enumerate(zip(anchors, ("Reference", "Sample"))):
        points = (pair - center) * (scale, -scale) + (120 + 240*i, 134)
        a, b = points
        parts.append(f'<path d="M{a[0]:g} {a[1]:g} L{b[0]:g} {b[1]:g}" stroke="#f8c779" stroke-width="2"/>')
        parts.extend(f'<circle cx="{x:g}" cy="{y:g}" r="4" fill="{color}"/>'
                     for (x, y), color in zip(points, ("#66dbcc", "#ffab91")))
        parts.append(f'<text x="{16+240*i}" y="241" fill="#fff">{heading}</text>')
    return "".join(parts) + "</svg>"


def inspect_fit(bpy, parameters, deadline):
    profile = FitProfile.parse(parameters.get("profile"))
    scene = bpy.context.scene
    start = (scene.frame_current, scene.frame_subframe)
    times = frames(parameters.get("frames"), sum(start))
    views = tuple(parameters.get("views", []))
    if len(views) not in (0, 2) or len(set(views)) != len(views) or any(view not in VIEWS for view in views):
        raise ValueError("Choose two distinct views from front, side, top and isometric.")
    objects, layers = {}, {}
    for name, layer in profile.layers.items():
        obj = bpy.context.view_layer.objects.get(layer.object)
        if obj is None:
            layers[name] = dict(object=layer.object, kind=layer.kind, status="unresolved")
            continue
        objects[name] = obj
        layers[name] = dict(kind=layer.kind, status="resolved", visible=obj.visible_get(),
                            authored_weights=_weight_summary(obj, layer.weight_groups),
                            **describe(obj, layer.representation))
    dependencies, truncated = _dependencies(bpy, layers)
    rows, attachments, references, limits = [], {}, {}, []
    if truncated:
        limits.append("dependency_limit")
    try:
        for time in times if profile.attachments or profile.strain else ():
            graph = evaluated_at(bpy, time, deadline)
            meshes = {}
            for name, check in profile.attachments.items():
                try:
                    row, reference = _attachment(check, objects, graph, meshes, attachments.get(name))
                    attachments.setdefault(name, reference)
                except (InvalidScene, KeyError, ValueError) as exc:
                    row = dict(status="unresolved", reason=str(exc))
                rows.append(dict(check=name, kind=check.kind, frame=time, **row))
            for name, check in profile.strain.items():
                try:
                    current = mesh_sample(objects[check.layer], graph, [check.group] if check.group else [])
                    baseline = references.setdefault(name, current)
                    mask = baseline.groups[check.group] > 0 if check.group else None
                    result = edge_ratios(baseline, current, selection=mask)
                    outside = result["minimum"] < check.min_ratio or result["maximum"] > check.max_ratio
                    row = dict(status="outside_limits" if outside else "within_limits", **result)
                except (InvalidScene, KeyError) as exc:
                    row = dict(status="unresolved", reason=str(exc))
                rows.append(dict(check=name, kind="strain", frame=time, **row))
    except InspectionLimit:
        limits.append("fit_deadline")
    finally:
        if (scene.frame_current, scene.frame_subframe) != start:
            scene.frame_set(start[0], subframe=start[1])
    evidence = []
    for name in profile.attachments.keys() | profile.strain.keys() if views else ():
        matches = [row for row in rows if row["check"] == name and row["status"] != "unresolved"]
        if not matches:
            continue
        first = matches[0]
        last = next((row for row in matches if row["status"] == "outside_limits"), matches[-1])
        if "witness" in first:
            a, b = first["witness"], last["witness"]
        else:
            # Use the same evaluated edge index in the baseline and failing sample.
            key = "short" if last["minimum"] < profile.strain[name].min_ratio else "long"
            a = references[name].world[last[f"{key}_edge"]].tolist()
            b = last[f"{key}_witness"]
        evidence.append(dict(check=name, frame=last["frame"], reference_frame=first["frame"],
                             diagrams={view: _matched_landmarks(a, b, view, name) for view in views}))
    contact = None
    if profile.contacts:
        remaining = deadline - monotonic()
        if remaining > .5:
            contact = inspect_motion(bpy, dict(profile=profile.contacts, views=list(views)), remaining)
        else:
            limits.append("contact_review_not_run")
    return dict(version=1, profile=profile.name, frames=times, layers=layers, measurements=rows,
                evidence=evidence, contact_review=contact, dependencies=dependencies, limits=limits, units="blender_units",
                interpretation=["Surface, seam and strain measurements describe sampled evaluated geometry.",
                                "A crossing does not identify its authoring cause. Instances and excluded geometry may need separate evidence.",
                                "Simulation and baked playback describe the loaded evaluation state. This review does not rebake."])
