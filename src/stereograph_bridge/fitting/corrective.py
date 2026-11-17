"""Stage and validate a bounded cloth corrective against the saved garment stack."""

from dataclasses import asdict, dataclass
import hashlib
from time import monotonic

import numpy as np

from ..review.native import set_frame
from ..spatial.distance import InspectionLimit
from ..spatial.evidence import VIEWS, diagram, witness_framing
from ..spatial.query import InvalidScene, _evaluated_surface, _measure
from .corrective_profile import Corrective
from .geometry import staged
from .inspect import describe, evaluated_at
from .mesh import edge_ratios, mesh_sample


@dataclass
class Sample:
    mesh: object
    protected: dict
    contacts: list
    evidence: tuple | None


@dataclass(frozen=True)
class Candidate:
    plan: Corrective
    identity: tuple
    signature: str


def _objects(bpy, plan):
    names = [plan.garment, *plan.protected_objects, *(pair.obstacle.object for pair in plan.pairs)]
    objects = {name: bpy.context.view_layer.objects.get(name) for name in names}
    for name, obj in objects.items():
        if obj is None or obj.type != "MESH" or not obj.visible_get():
            raise InvalidScene("corrective_object_unresolved", f"Resolve visible mesh object {name!r} in the current view layer.")
    garment = objects[plan.garment]
    if garment.library or garment.override_library or not garment.is_editable:
        raise InvalidScene("corrective_read_only", "Choose a writable local garment object.")
    return objects


def _cache_guard(obj, plan):
    for modifier in obj.modifiers:
        if modifier.type == "CLOTH" and (modifier.show_viewport or modifier.show_render):
            cache = modifier.point_cache
            if (not cache.is_baked or cache.is_outdated or cache.use_external
                    or min(plan.times) < cache.frame_start or max(plan.times) > cache.frame_end):
                raise InvalidScene("corrective_cache_unresolved", "Use authored geometry or a current internal cloth bake covering every sample.")
        if modifier.type in {"SOFT_BODY", "FLUID", "DYNAMIC_PAINT", "PARTICLE_SYSTEM", "MESH_CACHE", "MESH_SEQUENCE_CACHE"}:
            if modifier.show_viewport or modifier.show_render:
                raise InvalidScene("corrective_stack_unsupported", f"Active {modifier.type} is outside corrective support.")
        if modifier.type == "NODES" and modifier.node_group:
            pending, visited = [modifier.node_group], set()
            while pending:
                tree = pending.pop()
                if tree.as_pointer() in visited:
                    continue
                visited.add(tree.as_pointer())
                for node in tree.nodes:
                    if node.bl_idname in {"GeometryNodeSimulationInput", "GeometryNodeSimulationOutput", "GeometryNodeBake"}:
                        raise InvalidScene("corrective_stack_unsupported", "Geometry Nodes simulation and bake zones are outside corrective support.")
                    nested = getattr(node, "node_tree", None)
                    if nested:
                        pending.append(nested)


def _identity(bpy, plan, objects, start):
    scene = bpy.context.scene
    garment = objects[plan.garment]
    return (bpy.data.filepath, scene.session_uid, bpy.context.view_layer.name, tuple(start),
            garment.data.session_uid, tuple((name, obj.session_uid, action_identity(obj)) for name, obj in objects.items()),
            tuple((mod.name, mod.type, mod.show_viewport, mod.show_render) for mod in garment.modifiers))


def action_identity(obj):
    animation = obj.animation_data
    return (animation.action.session_uid, animation.action_slot_handle) if animation and animation.action else None


def _read(bpy, plan, objects, time, deadline, evidence):
    graph = evaluated_at(bpy, time, deadline)
    garment = objects[plan.garment]
    mesh = mesh_sample(garment, graph, (plan.mask, *plan.protected_groups))
    details = {name: mesh_sample(objects[name], graph) for name in plan.protected_objects}
    items = {}
    contacts, witness_data = [], None
    for i, pair in enumerate(plan.pairs):
        for region in (pair.surface, pair.obstacle):
            if region not in items:
                items[region] = _evaluated_surface(objects[region.object], graph, region)
        a, b = items[pair.surface], items[pair.obstacle]
        row, witness = _measure(a, b, (pair.surface.object, pair.obstacle.object), time, time,
                                pair.minimum_gap, deadline, check_containment=False)
        contacts.append(row)
        if i == 0 and evidence:
            witness_data = a, b, witness
    return Sample(mesh, details, contacts, witness_data)


def _validate_mask(mesh, plan):
    mask = mesh.groups[plan.mask] > 1e-7
    protected = {group: mesh.groups[group] > 1e-7 for group in plan.protected_groups}
    if not mask.any() or any(not region.any() or (mask & region).any() for region in protected.values()):
        raise InvalidScene("corrective_mask_invalid", "Provide nonempty feathered and protected groups with no evaluated overlap.")
    return mask, protected


def _hash(baseline, objects):
    digest = hashlib.sha256()
    for name, obj in objects.items():
        digest.update(repr(describe(obj)).encode())
        digest.update(name.encode())
    for time, sample in baseline.items():
        digest.update(repr((time, sample.contacts)).encode())
        for mesh in (sample.mesh, *sample.protected.values()):
            digest.update(mesh.signature.encode())
            digest.update(mesh.world.tobytes())
            for weights in mesh.groups.values():
                digest.update(weights.tobytes())
    return digest.hexdigest()


def _pair_conflict(index, time, plan, before, after):
    required = plan.pairs[index].minimum_gap
    if index == 0 and time == plan.frame:
        return after["surfaces_intersect"] or after["distance"] + 1e-8 < required
    if (after["transverse_crossing"] and not before["transverse_crossing"]
            or after["surfaces_intersect"] and not before["surfaces_intersect"]):
        return True
    # Preserve attained clearance, bounded by the declared guard distance.
    return after["distance"] + 1e-7 < min(required, before["distance"])


def _compare(time, plan, before, after):
    mask, protected = _validate_mask(before.mesh, plan)
    ratios = edge_ratios(before.mesh, after.mesh)
    displacement = np.linalg.norm(after.mesh.world - before.mesh.world, axis=1)
    moved = float(displacement.max())
    outside = float(displacement[~mask].max())
    groups = {group: float(displacement[region].max()) for group, region in protected.items()}
    anchors = max(groups.values())
    details = {}
    for name, mesh in before.protected.items():
        other = after.protected[name]
        if mesh.signature != other.signature or len(mesh.world) != len(other.world):
            raise InvalidScene("corrective_detail_topology", f"Protected detail {name!r} changed topology.")
        details[name] = float(np.linalg.norm(other.world - mesh.world, axis=1).max())
    row = dict(frame=time, before=before.contacts, after=after.contacts, max_displacement=moved,
               protected_displacement=anchors, protected_group_displacements=groups,
               outside_mask_displacement=outside, detail_displacement=details,
               edge_ratio=dict(minimum=ratios["minimum"], maximum=ratios["maximum"], edges=ratios["edges"]))
    conflicts = [
        code for code, failed in (
            ("displacement_limit", moved > plan.max_displacement),
            ("outside_mask_motion", outside > plan.protected_tolerance),
            ("protected_region_motion", max([anchors, *details.values()]) > plan.protected_tolerance),
            ("local_deformation_limit", ratios["minimum"] < plan.min_ratio or ratios["maximum"] > plan.max_ratio),
            ("neutral_pose_changed", abs(time-plan.frame) >= plan.span-1e-6 and moved > plan.protected_tolerance),
            ("mask_had_no_effect", time == plan.frame and moved <= 1e-8),
        ) if failed
    ]
    conflicts.extend(f"pair_{index+1}_clearance" for index, (first, last) in
                     enumerate(zip(before.contacts, after.contacts)) if _pair_conflict(index, time, plan, first, last))
    return row, [dict(frame=time, code=code) for code in conflicts]


def _assess(bpy, plan, deadline, views=(), *, accepted=None, checkpoint=None):
    scene = bpy.context.scene
    start = scene.frame_current, scene.frame_subframe
    objects = _objects(bpy, plan)
    obj = objects[plan.garment]
    key = _identity(bpy, plan, objects, start)
    if accepted is not None and accepted.identity != key:
        raise InvalidScene("stale_corrective", "The garment, stack, scene or current frame changed. Preview again.")
    for target in objects.values():
        _cache_guard(target, plan)
    rows, conflicts, evidence = [], [], []
    baseline = {}
    try:
        for time in plan.times:
            baseline[time] = _read(bpy, plan, objects, time, deadline, bool(views) and time == plan.frame)
            _validate_mask(baseline[time].mesh, plan)
        signature = _hash(baseline, objects)
        if accepted is not None and signature != accepted.signature:
            raise InvalidScene("stale_corrective", "Evaluated baseline geometry or dependencies changed. Preview again.")
        repair = baseline[plan.frame].contacts[0]
        if not repair["surfaces_intersect"] and repair["distance"] + 1e-8 >= plan.pairs[0].minimum_gap:
            conflicts.append(dict(frame=plan.frame, code="baseline_already_clear"))
        if checkpoint:
            scene.frame_set(start[0], subframe=start[1])
            if monotonic() >= deadline:
                raise InspectionLimit("Clothing evaluation reached its deadline before native apply.")
            checkpoint("Before Stereograph cloth corrective")
        with staged(bpy, obj, plan) as transaction:
            for time in plan.times:
                first = baseline[time]
                last = _read(bpy, plan, objects, time, deadline, bool(views) and time == plan.frame)
                row, failures = _compare(time, plan, first, last)
                rows.append(row)
                conflicts.extend(failures)
                if first.evidence:
                    a, b, witness = first.evidence
                    framing = witness_framing(a, b, witness)
                    for phase, source in (("Before", first), ("Candidate", last)):
                        for view in views:
                            evidence.append(dict(phase=phase, view=view, frame=time, svg=diagram(
                                *source.evidence, source.contacts[0], view, deadline, framing=framing, phase=phase)))
            result = dict(garment=plan.garment, frame=plan.frame, duration=[plan.frame-plan.span, plan.frame+plan.span],
                          stack_position="after_existing_modifiers", feasible=not conflicts, conflicts=conflicts,
                          pair_coverage=[asdict(pair) for pair in plan.pairs],
                          samples=rows, evidence=evidence, units="blender_units")
            if checkpoint:
                if conflicts:
                    raise InvalidScene("corrective_recheck_failed", "The staged correction no longer meets the clearance and deformation limits.")
                result.update(applied=True, undoable=True, modifier=transaction["modifier"].name, node_group=transaction["group"].name)
                scene.frame_set(start[0], subframe=start[1])
                if monotonic() >= deadline:
                    raise InspectionLimit("Clothing evaluation reached its deadline before native apply.")
                checkpoint("Stereograph cloth corrective")
                transaction["keep"] = True
            return result, Candidate(plan, key, signature) if not conflicts else None
    finally:
        scene.frame_set(start[0], subframe=start[1])


def preview(bpy, parameters, deadline):
    plan = Corrective.parse(parameters.get("profile"))
    views = parameters.get("views", [])
    if (not isinstance(views, list) or len(views) not in (0, 2) or
            any(view not in VIEWS for view in views) or (views and views[0] == views[1])):
        raise ValueError("Choose two distinct views from front, side, top and isometric.")
    return _assess(bpy, plan, deadline, views)


def apply(bpy, candidate, deadline, checkpoint):
    return _assess(bpy, candidate.plan, deadline, accepted=candidate, checkpoint=checkpoint)[0]
