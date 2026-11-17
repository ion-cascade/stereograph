"""Preview and key a location-only IK pole edit with planted-foot validation."""

import hashlib
import json
from math import radians
from time import monotonic

from mathutils import Matrix, Quaternion, Vector

from ..posing.actions import accept_target_curves, fingerprint, staged_action
from ..posing.evidence import compare
from ..posing.preview import current_frame, set_frame
from ..posing.profile import PoseProblem
from ..posing.solver import anchored_drift, swivel_for_target
from ..review.profile import FRAME_LIMIT, number
from ..spatial.distance import InspectionLimit
from ..spatial.evidence import diagram, witness_framing
from ..spatial.query import _evaluated_surface, _measure
from .knee_profile import KneeContext, KneeProfile
from .metrics import axis_direction, knee_state
from .profile import vector

LABEL = "Knee tracking"


def _edit(context, values):
    return [(context.pole, "location", values)]


def _keys(action, values):
    return [dict(entry, value=float(values[entry["index"]])) for entry in action["keyed"]]


def _fingerprint(bpy, context, frame):
    constraint = context.require_ik()
    state = (fingerprint(bpy, context, frame), constraint.name, constraint.target.session_uid,
             constraint.subtarget, constraint.pole_angle, constraint.use_stretch,
             list(context.pole.location))
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def _knee(context):
    world = context.rig.matrix_world
    upper, lower, foot = context.bones[:3]
    head, joint, ankle = (world @ bone.head for bone in (upper, lower, foot))
    anchor = context.anchor.matrix_world if context.anchor else Matrix.Identity(4)
    if abs(anchor.determinant()) < 1e-12:
        raise PoseProblem("knee_anchor_missing", "The planted-foot reference degenerated at a sampled frame.")
    forward = axis_direction(context.anchor.matrix_world if context.anchor else world, context.profile.forward)
    state, plane = knee_state(head, joint, ankle, forward)
    sole = world @ foot.tail
    state.update(hip=list(head), knee=list(joint), ankle=list(ankle), toe=list(sole))
    return state, plane, anchor.inverted() @ world @ foot.matrix, anchor.copy()


def pole_values(context, plane, degrees):
    origin, axis, _ = plane
    pole = context.pole
    world = context.rig.matrix_world
    wanted = (world @ pole.matrix).copy()
    radial = wanted.translation - origin
    if (radial - axis * radial.dot(axis)).length < 1e-7:
        raise PoseProblem("invalid_knee_pole", "The pole lies on the hip-to-foot line.")
    wanted.translation = origin + Quaternion(axis, radians(degrees)) @ radial
    options = {}
    if pole.parent:
        options = dict(parent_matrix=pole.parent.matrix, parent_matrix_local=pole.parent.bone.matrix_local)
    local = pole.bone.convert_local_to_pose(world.inverted() @ wanted, pole.bone.matrix_local,
                                           invert=True, **options)
    return list(local.translation)


def _sample(bpy, context, time, deadline, views=(), framing=None, label=""):
    if monotonic() >= deadline:
        raise InspectionLimit("The knee verification reached its deadline.")
    scene = bpy.context.scene
    set_frame(scene, time)
    context.require_ik()
    state, _, foot, anchor = _knee(context)
    cache, measurements, images = {}, [], []
    graph = bpy.context.evaluated_depsgraph_get()
    for index, (first, second, gap) in enumerate(context.profile.pairs):
        for key in (first, second):
            if key not in cache:
                cache[key] = _evaluated_surface(context.meshes[key], graph)
        a, b = cache[first], cache[second]
        row, witness = _measure(a, b, (first, second), time, scene.frame_current_final, gap, deadline,
                                check_containment=False)
        measurements.append(row)
        if index == 0 and views:
            framing = framing or witness_framing(a, b, witness)
            images = [dict(frame=time, view=view, phase=label,
                           svg=diagram(a, b, witness, row, view, deadline, framing, label)) for view in views]
    return dict(frame=time, state=state, foot=foot, anchor=anchor, clearances=measurements), images, framing


def _report(before, after):
    return dict(frame=after["frame"], before=before["state"], after=after["state"],
                foot_drift=anchored_drift(before["foot"], after["foot"]),
                anchor_drift=anchored_drift(before["anchor"], after["anchor"]),
                clearance_before=before["clearances"], clearance_after=after["clearances"])


def _difference(left, right):
    return (right - left + 180) % 360 - 180


def _validate(context, frame, target, after):
    issues = []
    for item in after:
        a, b, time = item["before"], item["after"], item["frame"]
        for label in ("foot_drift", "anchor_drift"):
            drift = item[label]
            if drift["position"] > context.profile.position or drift["rotation_degrees"] > context.profile.rotation:
                issues.append(dict(frame=time, code=label, **drift))
        if (Vector(a["hip"]) - Vector(b["hip"])).length > context.profile.position:
            issues.append(dict(frame=time, code="hip_drift"))
        for length in ("upper_length", "lower_length"):
            if abs(b[length] - a[length]) > max(a[length] * 1e-5, 1e-7):
                issues.append(dict(frame=time, code="segment_length_changed", segment=length))
        if time == frame:
            previous = abs(_difference(a["tracking"], target))
            current = abs(_difference(b["tracking"], target))
            if previous - current < context.profile.gain or current > .75:
                issues.append(dict(frame=time, code="tracking_target_unmet", before_error=previous,
                                   after_error=current))
        for spec, row in zip(context.profile.pairs, item["clearance_after"]):
            if row["surfaces_intersect"] or row["distance"] < spec[2]:
                issues.append(dict(frame=time, code="knee_clearance_conflict", pair=row["pair"],
                                   distance=row["distance"], witness=row["witness"]))
    for left, right in zip(after, after[1:]):
        old = _difference(left["before"]["tracking"], right["before"]["tracking"])
        new = _difference(left["after"]["tracking"], right["after"]["tracking"])
        if abs(_difference(old, new)) > context.profile.neighbour:
            issues.append(dict(frame=right["frame"], code="knee_neighbour_step", added_degrees=abs(_difference(old, new))))
    return issues


def _as_arm(state):
    return dict(shoulder=state["hip"], elbow=state["knee"], wrist=state["ankle"],
                palm_direction=list((Vector(state["toe"]) - Vector(state["ankle"])).normalized()),
                forearm_length=state["lower_length"], wrist_bend_degrees=state["tracking"])


def _joint_views(before, after, frame, views):
    # The elbow and knee share the same four-point overlay layout.
    shifts = (("Planted hand", "Planted foot"), ("elbow comparison", "knee comparison"),
              ("Wrist bend", "Knee tracking"),
              ("wrist and palm orientation", "ankle and foot orientation"),
              ("Elbow displacement", "Knee displacement"), (">shoulder<", ">hip<"),
              (">elbow<", ">knee<"), (">wrist<", ">ankle<"), (">palm<", ">foot<"))
    for view in views:
        svg = compare(_as_arm(before), _as_arm(after), frame, view)
        for old, new in shifts:
            svg = svg.replace(old, new)
        yield dict(frame=frame, view=view, phase="Joint comparison", svg=svg)


def _options(scene, params, live):
    start = current_frame(scene)
    requested = start if params.get("frame") is None else params["frame"]
    frame = round(number(requested, "Frame", -FRAME_LIMIT + 1, FRAME_LIMIT - 1), 6)
    if live and abs(start - frame) > 1e-6:
        raise PoseProblem("knee_frame_mismatch", "Set the live timeline to the requested edit frame.")
    views = params.get("views", [])
    if not isinstance(views, list) or (views and set(views) != {"front", "side"}) or len(views) not in (0, 2):
        raise ValueError("Request matched front and side views together.")
    return start, frame, [frame - 1, frame - .5, frame, frame + .5, frame + 1], views


def preview(bpy, params, deadline, live):
    profile = KneeProfile.parse(params.get("profile"))
    scene = bpy.context.scene
    start, frame, times, views = _options(scene, params, live)
    context = KneeContext.resolve(bpy, profile)
    accept_target_curves(context, [(context.pole, "location", range(3))])
    try:
        set_frame(scene, frame)
        context.require_ik()
        state, plane, _, _ = _knee(context)
        initial = _fingerprint(bpy, context, frame)
        requested = params.get("tracking_degrees")
        desired = params.get("knee_target")
        if requested is not None and desired is not None:
            raise ValueError("Choose a tracking angle or a world-space knee target.")
        if desired is not None:
            target_point = vector(desired, "Knee target")
            try:
                delta, error = swivel_for_target(
                    dict(shoulder=state["hip"], elbow=state["knee"], wrist=state["ankle"]), target_point
                )
            except ValueError as exc:
                return dict(feasible=False, frame=frame, before=state, conflicts=[
                    dict(code="knee_target_unreachable", reason=str(exc))]), None
            if error > profile.position:
                return dict(feasible=False, frame=frame, conflicts=[dict(code="knee_target_unreachable",
                            distance_to_reachable_circle=error)], before=state), None
            target = _difference(0, state["tracking"] + delta)
        else:
            target = number(requested if requested is not None else 0, "Tracking angle", -180, 180)
            delta = _difference(state["tracking"], target)
        if abs(delta) > profile.swivel or abs(delta) < profile.gain:
            return dict(feasible=False, frame=frame, before=state, conflicts=[
                dict(code="knee_swivel_limit" if abs(delta) > profile.swivel else "knee_already_aligned",
                     requested=delta, limit=profile.swivel)]), None
        values = pole_values(context, plane, delta)
        originals, evidence, framing = [], [], None
        for time in times:
            sampled, images, anchor = _sample(bpy, context, time, deadline, views if time == frame else (), label="Before")
            originals.append(sampled)
            if images:
                evidence, framing = images, anchor
        reports = []
        with staged_action(bpy, context, frame, _edit(context, values), LABEL) as action:
            for original in originals:
                time = original["frame"]
                altered, images, _ = _sample(bpy, context, time, deadline, views if time == frame else (),
                                              framing, "Candidate")
                evidence.extend(images)
                reports.append(_report(original, altered))
            conflicts = _validate(context, frame, target, reports)
            planned = _keys(action, values)
        centre = reports[2]
        evidence.extend(_joint_views(centre["before"], centre["after"], frame, views))
        result = dict(feasible=not conflicts, frame=frame, rig=context.rig.name, source_action=context.action.name,
                      target_tracking=target, pole_swivel=delta, anchor=profile.anchor or "world", sample=centre,
                      neighbours=reports[:2] + reports[3:], planned_keys=planned, conflicts=conflicts, evidence=evidence)
        candidate = dict(profile=params["profile"], frame=frame, target=target, values=values, identity=initial)
        return result, candidate if live and not conflicts else None
    finally:
        set_frame(scene, start)
        bpy.context.view_layer.update()


def apply(bpy, candidate, deadline, checkpoint):
    scene = bpy.context.scene
    frame = candidate["frame"]
    if abs(current_frame(scene) - frame) > 1e-6:
        raise PoseProblem("stale_knee_preview", "The frame changed after preview.")
    context = KneeContext.resolve(bpy, KneeProfile.parse(candidate["profile"]))
    if _fingerprint(bpy, context, frame) != candidate["identity"]:
        raise PoseProblem("stale_knee_preview", "The file, rig, pose or Action changed after preview.")
    accept_target_curves(context, [(context.pole, "location", range(3))])
    edits = _edit(context, candidate["values"])
    times = [frame - 1, frame - .5, frame, frame + .5, frame + 1]
    try:
        originals = [_sample(bpy, context, time, deadline)[0] for time in times]
        with staged_action(bpy, context, frame, edits, LABEL):
            reports = [_report(row, _sample(bpy, context, row["frame"], deadline)[0]) for row in originals]
            conflicts = _validate(context, frame, candidate["target"], reports)
            if conflicts:
                return dict(applied=False, frame=frame, conflicts=conflicts)
        set_frame(scene, frame)
        if monotonic() >= deadline:
            raise InspectionLimit("Knee verification completed after the deadline. The Action was preserved.")
        checkpoint("Before Stereograph knee repair")
        if monotonic() >= deadline:
            raise InspectionLimit("Blender recorded the baseline after the deadline. The Action was preserved.")
        keep = context.action.use_fake_user
        with staged_action(bpy, context, frame, edits, LABEL) as action:
            try:
                context.action.use_fake_user = True
                bpy.context.view_layer.update()
                checkpoint("Stereograph knee repair")
                action["keep"] = True
            finally:
                if not action["keep"]:
                    context.action.use_fake_user = keep
            return dict(applied=True, undoable=True, frame=frame, previous_action=context.action.name,
                        action=action["action"].name, keyed=_keys(action, candidate["values"]), sample=reports[2])
    finally:
        set_frame(scene, frame)
        bpy.context.view_layer.update()
