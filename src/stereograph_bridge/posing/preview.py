"""Evaluate elbow candidates against the actual keyed scene and nearby frames."""

from math import floor
from time import monotonic

from mathutils import Vector

from ..spatial.distance import InspectionLimit
from ..spatial.evidence import diagram, witness_framing
from ..spatial.query import InvalidScene, _evaluated_surface, _measure
from .actions import accept_target_curves, fingerprint, variant
from .evidence import compare
from .profile import PoseProblem, Profile, Context, number, vector
from .solver import anchored_drift, best_swivel, place, pose_state, swivel_for_target


def current_frame(scene):
    return scene.frame_current + scene.frame_subframe


def set_frame(scene, frame):
    whole = floor(frame)
    scene.frame_set(whole, subframe=frame - whole)


def _matrix_rows(matrix):
    return [list(row) for row in matrix]


def _pose(context):
    controls = context.bones
    state = pose_state(context.rig, *controls, context.profile.palm_axis)
    palm = context.rig.matrix_world @ controls[-1].matrix
    anchor = context.anchor.matrix_world if context.anchor else None
    inverse = anchor.inverted() if anchor is not None else None
    observed = context.witness.matrix_world.copy() if context.witness else palm
    observed = inverse @ observed if inverse is not None else observed
    planned = inverse @ palm if inverse is not None else palm
    return dict(state=state, palm=planned, witness=observed, prop=anchor.copy() if anchor is not None else None)


def _sample(bpy, context, time, deadline, views=(), framing=None, phase=None):
    if monotonic() >= deadline:
        raise InspectionLimit("Elbow verification reached its deadline.")
    scene = bpy.context.scene
    set_frame(scene, time)
    context.require_fk()
    measured = _pose(context)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    cache, results, pictures = {}, [], []
    for index, (first, second, gap) in enumerate(context.profile.clearance):
        for name in (first, second):
            if name not in cache:
                cache[name] = _evaluated_surface(context.meshes[name], depsgraph)
        a, b = cache[first], cache[second]
        sample, witness = _measure(a, b, (first, second), time, scene.frame_current_final, gap, deadline)
        results.append(sample)
        if index == 0 and views:
            framing = framing if framing is not None else witness_framing(a, b, witness)
            for view in views:
                pictures.append(dict(kind="surface", phase=phase, frame=time, view=view,
                                     svg=diagram(a, b, witness, sample, view, deadline, framing, phase)))
    measured.update(frame=time, clearance=results)
    return measured, pictures, framing


def _checks(profile, before, after, frame):
    target = abs(frame - after["frame"]) < 1e-6
    position = profile.position_tolerance if target else profile.neighbour_position
    rotation = profile.rotation_tolerance if target else profile.neighbour_rotation
    differences = for_report(before, after)
    for label in ("hand_drift", "control_drift"):
        drift = differences[label]
        if drift["position"] > position or drift["rotation_degrees"] > rotation:
            yield dict(code="hand_anchor_drift", frame=after["frame"], witness=label, **drift)
    if differences["prop_drift"] and (differences["prop_drift"]["position"] > profile.position_tolerance
                                     or differences["prop_drift"]["rotation_degrees"] > profile.rotation_tolerance):
        yield dict(code="prop_drift", frame=after["frame"], **differences["prop_drift"])
    if differences["shoulder_drift"] > position:
        yield dict(code="shoulder_drift", frame=after["frame"], distance=differences["shoulder_drift"])
    for segment in ("upper_length", "forearm_length"):
        original = before["state"][segment]
        if abs(after["state"][segment] - original) > max(original * 1e-5, 1e-7):
            yield dict(code="arm_length_changed", frame=after["frame"], segment=segment)
    b = after["state"]
    low, high = profile.elbow_range
    if not low <= b["elbow_bend_degrees"] <= high:
        yield dict(code="elbow_limit", frame=after["frame"], angle=b["elbow_bend_degrees"])
    if b["wrist_bend_degrees"] > profile.wrist_limit:
        yield dict(code="wrist_limit", frame=after["frame"], angle=b["wrist_bend_degrees"])
    if target:
        improvement = before["state"]["wrist_bend_degrees"] - b["wrist_bend_degrees"]
        if improvement < profile.min_improvement:
            yield dict(code="insufficient_wrist_improvement", frame=after["frame"], improvement=improvement)
    for check, sample in zip(profile.clearance, after["clearance"]):
        if sample["surfaces_intersect"] or sample["distance"] < check[2]:
            yield dict(code="clearance_conflict", frame=after["frame"], pair=sample["pair"],
                       distance=sample["distance"], required_gap=check[2], state=sample["surface_relation"],
                       witness=sample["witness"])


def for_report(before, after):
    return dict(
        frame=after["frame"], before=before["state"], after=after["state"],
        hand_drift=anchored_drift(before["witness"], after["witness"]),
        control_drift=anchored_drift(before["palm"], after["palm"]),
        prop_drift=anchored_drift(before["prop"], after["prop"]) if before["prop"] is not None else None,
        shoulder_drift=(Vector(before["state"]["shoulder"]) - Vector(after["state"]["shoulder"])).length,
        clearance_before=before["clearance"], clearance_after=after["clearance"],
    )


def _added_step(a, b, c, d, field):
    before = b["state"][field] - a["state"][field]
    after = d["state"][field] - c["state"][field]
    return abs(after - before)


def check_track(context, originals, staged, frame):
    conflicts = [item for first, second in zip(originals, staged)
                 for item in _checks(context.profile, first, second, frame)]
    limit = context.profile.neighbour_step
    for left, right, first, second in zip(originals, originals[1:], staged, staged[1:]):
        extra = max(_added_step(left, right, first, second, name) for name in
                    ("wrist_bend_degrees", "elbow_bend_degrees"))
        before_twist = anchored_drift(left["palm"], right["palm"])["rotation_degrees"]
        after_twist = anchored_drift(first["palm"], second["palm"])["rotation_degrees"]
        extra = max(extra, abs(after_twist - before_twist))
        if extra > limit:
            conflicts.append(dict(code="neighbour_motion_step", frame=second["frame"],
                                  added_degrees=extra, limit=limit))
    return conflicts


def _controls(bpy, context, state, angle):
    upper, _, hand = context.bones
    first, last = upper.rotation_quaternion.copy(), hand.rotation_quaternion.copy()
    try:
        palm = context.rig.matrix_world @ hand.matrix
        place(bpy, context.rig, upper, hand, state, angle, palm)
        return tuple(list(bone.rotation_quaternion) for bone in (upper, hand))
    finally:
        upper.rotation_quaternion, hand.rotation_quaternion = first, last
        bpy.context.view_layer.update()


def _options(scene, parameters, live):
    start = current_frame(scene)
    frame = parameters.get("frame")
    if frame is None:
        frame = start
    frame = number(frame, -1048573, 1048573, "frame")
    if abs(frame - round(frame, 4)) > 1e-6:
        raise PoseProblem("unsupported_keyframe_time", "Use a frame with up to four decimal places.")
    if live and abs(frame - start) > 1e-6:
        raise PoseProblem("wrong_frame", "Set the live timeline to the requested pose before previewing.")
    frames = [frame - 1, frame - .5, frame, frame + .5, frame + 1]
    views = parameters.get("views", [])
    if not isinstance(views, list) or any(view not in ("front", "side") for view in views):
        raise PoseProblem("invalid_pose_view", "Use front and/or side views for matched elbow evidence.")
    return start, frame, frames, list(dict.fromkeys(views))


def preview(bpy, parameters, deadline, live):
    profile = Profile.parse(parameters.get("profile"))
    scene = bpy.context.scene
    start, frame, times, views = _options(scene, parameters, live)
    context = Context.resolve(bpy, profile)
    accept_target_curves(context)
    try:
        set_frame(scene, frame)
        context.require_fk()
        initial = _pose(context)["state"]
        before_identity = fingerprint(bpy, context, frame)
        automatic = best_swivel(initial)
        desired = parameters.get("elbow_target")
        explicit = parameters.get("swivel_degrees")
        if explicit is not None and desired is not None:
            raise PoseProblem("ambiguous_elbow_request", "Choose an elbow target or a swivel angle.")
        if desired is not None:
            angle, target_error = swivel_for_target(initial, vector(desired, "elbow_target"))
            if target_error > profile.position_tolerance:
                return dict(feasible=False, frame=frame, before=initial,
                            conflicts=[dict(code="elbow_target_unreachable", distance_to_reachable_circle=target_error)]), None
        elif explicit is not None:
            angle = number(explicit, -180, 180, "swivel_degrees")
        else:
            angle = max(-profile.max_swivel, min(profile.max_swivel, automatic))
        if abs(angle) > profile.max_swivel or abs(angle) < .0001:
            return dict(feasible=False, frame=frame, before=initial,
                        conflicts=[dict(code="elbow_swivel_limit" if abs(angle) > profile.max_swivel
                                        else "no_improving_swivel", requested=angle, limit=profile.max_swivel)]), None

        originals, previous_pictures = [], []
        framing = None
        for time in times:
            original, images, selected = _sample(bpy, context, time, deadline,
                                                  views if time == frame else (), phase="Before")
            originals.append(original)
            if images:
                previous_pictures, framing = images, selected
        set_frame(scene, frame)
        values = _controls(bpy, context, initial, angle)
        staged, proposed_pictures = [], []
        with variant(bpy, context, frame, values) as changed:
            for time in times:
                after, images, _ = _sample(bpy, context, time, deadline, views if time == frame else (),
                                           framing, "Candidate")
                staged.append(after)
                proposed_pictures.extend(images)
            conflicts = check_track(context, originals, staged, frame)
            planned_keys = changed["keyed"]
        samples = [for_report(a, b) for a, b in zip(originals, staged)]
        middle = samples[2]
        result = dict(feasible=not conflicts, frame=frame, rig=profile.rig, source_action=context.action.name,
                      swivel_degrees=angle, unconstrained_best_swivel_degrees=automatic,
                      anchor=profile.anchor or "world", sample=middle, neighbours=samples[:2] + samples[3:],
                      planned_keys=planned_keys, conflicts=conflicts, units="blender_units",
                      evidence=previous_pictures + proposed_pictures + [
                          dict(kind="joint_comparison", view=view, frame=frame,
                               svg=compare(middle["before"], middle["after"], frame, view)) for view in views
                      ])
        candidate = None if conflicts or not live else dict(
            profile=parameters["profile"], frame=frame, identity=before_identity, values=values,
        )
        return result, candidate
    except InvalidScene as exc:
        raise PoseProblem(exc.code, str(exc)) from exc
    except ValueError as exc:
        if isinstance(exc, PoseProblem):
            raise
        raise PoseProblem("unreachable_elbow", str(exc)) from exc
    finally:
        set_frame(scene, start)


def apply(bpy, candidate, deadline, checkpoint):
    scene = bpy.context.scene
    frame = candidate["frame"]
    if abs(current_frame(scene) - frame) > 1e-6:
        raise PoseProblem("stale_pose_preview", "The timeline frame changed after preview.")
    context = Context.resolve(bpy, Profile.parse(candidate["profile"]))
    context.require_fk()
    if fingerprint(bpy, context, frame) != candidate["identity"]:
        raise PoseProblem("stale_pose_preview", "The file, Action, rig, or planted pose changed after preview.")
    accept_target_curves(context)
    times = [frame - 1, frame - .5, frame, frame + .5, frame + 1]
    try:
        originals = [_sample(bpy, context, time, deadline)[0] for time in times]
        with variant(bpy, context, frame, candidate["values"]) as changed:
            staged = [_sample(bpy, context, time, deadline)[0] for time in times]
            conflicts = check_track(context, originals, staged, frame)
            if conflicts:
                return dict(applied=False, frame=frame, conflicts=conflicts)
            sample = for_report(originals[2], staged[2])
        # Capture a native undo step with the original Action restored. Only
        # commit a variant after the complete keyed clearance pass succeeds.
        set_frame(scene, frame)
        checkpoint("Before Stereograph elbow edit")
        fake_user = context.action.use_fake_user
        with variant(bpy, context, frame, candidate["values"]) as committed:
            try:
                context.action.use_fake_user = True
                set_frame(scene, frame)
                checkpoint("Stereograph elbow edit")
                committed["keep"] = True
            finally:
                if not committed["keep"]:
                    context.action.use_fake_user = fake_user
            return dict(applied=True, frame=frame, rig=context.rig.name, previous_action=context.action.name,
                        action=committed["action"].name, keyed=committed["keyed"], undoable=True,
                        sample=sample)
    except InvalidScene as exc:
        raise PoseProblem(exc.code, str(exc)) from exc
    finally:
        if current_frame(scene) != frame:
            set_frame(scene, frame)
        else:
            bpy.context.view_layer.update()
