"""Read rest/pose/binding data and retain measurable evidence by cause."""

from time import monotonic

from mathutils import Matrix, Vector

from ..review.native import set_frame
from ..spatial.distance import InspectionLimit
from ..wire import failure, success
from .evidence import overlay
from .metrics import angle, axis_direction, measure
from .profile import InspectionProfile, frames
from .weights import vertex_bindings


def _bone(layer, object_name, name):
    rig = layer.objects.get(object_name)
    if rig is None or rig.type != "ARMATURE":
        return None, None
    return rig, rig.pose.bones.get(name)


def _joint(matrix, tail):
    return dict(head=list(matrix.translation), tail=list(tail),
                axes={key: list(axis_direction(matrix, direction)) for key, direction in
                      zip(("x", "y", "z"), ((1, 0, 0), (0, 1, 0), (0, 0, 1)))})


def _constraint(constraint):
    target = getattr(constraint, "target", None)
    pole = getattr(constraint, "pole_target", None)
    return dict(name=constraint.name, type=constraint.type, influence=float(constraint.influence),
                active=not constraint.mute and constraint.influence > 1e-6 and constraint.is_valid,
                target=target.name if target else None, subtarget=getattr(constraint, "subtarget", None),
                pole=pole.name if pole else None, pole_bone=getattr(constraint, "pole_subtarget", None))


def _role(layer, term):
    rig, bone = _bone(layer, term.object, term.bone)
    if bone is None:
        return dict(status="unresolved", code="joint_role_missing", object=term.object, bone=term.bone), None
    rest = _joint(rig.matrix_world @ bone.bone.matrix_local, rig.matrix_world @ bone.bone.tail_local)
    deviations = {}
    if term.landmark is not None:
        target = Vector(term.landmark)
        rest["landmark"] = list(rig.matrix_world @ target)
        deviations["landmark_distance_rig_units"] = (target - bone.bone.head_local).length
    if term.expected_axis is not None:
        deviations["axis_error_degrees"] = angle(axis_direction(bone.bone.matrix_local, term.local_axis),
                                                  Vector(term.expected_axis))
    flags = []
    if deviations.get("landmark_distance_rig_units", 0) > term.landmark_tolerance:
        flags.append("rest_landmark_offset")
    if deviations.get("axis_error_degrees", 0) > term.axis_tolerance:
        flags.append("rest_axis_mismatch")
    control = rig.pose.bones.get(term.control) if term.control else None
    if term.control and control is None:
        flags.append("control_missing")
    control_rest = _joint(rig.matrix_world @ control.bone.matrix_local,
                          rig.matrix_world @ control.bone.tail_local) if control else None
    return dict(status="finding" if flags else "pass", object=rig.name, bone=bone.name,
                requested_control=term.control, control=control.name if control else None,
                rest=rest, control_rest=control_rest, deviations=deviations, findings=flags,
                parent=bone.parent.name if bone.parent else None, deform=bone.bone.use_deform,
                connected=bone.bone.use_connect), bone


def _switch(layer, term):
    rig, bone = _bone(layer, term.object, term.bone)
    value = bone.get(term.property) if bone else None
    if type(value) not in (int, float):
        return dict(status="unresolved", code="switch_property_missing")
    mode = "IK" if abs(value - term.ik) <= 1e-6 else "FK" if abs(value - term.fk) <= 1e-6 else "mixed"
    requested = term.ik_controls if mode == "IK" else term.fk_controls if mode == "FK" else (
        *term.ik_controls, *term.fk_controls,
    )
    missing = [key for key in requested if rig.pose.bones.get(key) is None]
    return dict(status="unresolved" if missing else "pass", mode=mode, value=value,
                controls=[key for key in requested if key not in missing], unresolved_controls=missing)


def _check(layer, check, bones, roles, time, initial):
    if check.frames is not None and not check.frames[0] <= time <= check.frames[1]:
        return None
    if any(bones[key] is None for key in check.roles):
        return dict(status="unresolved", code="check_bone_missing")
    selected = [bones[key] for key in check.roles]
    matrices = [bone.id_data.matrix_world @ bone.matrix for bone in selected]
    rests = [bone.id_data.matrix_world @ bone.bone.matrix_local for bone in selected]
    axes = [roles[key].local_axis for key in check.roles]
    if check.anchor:
        obj = layer.objects.get(check.anchor)
        if obj is None or abs(obj.matrix_world.determinant()) < 1e-12:
            return dict(status="unresolved", code="check_anchor_missing")
        anchor = obj.matrix_world
    else:
        anchor = selected[0].id_data.matrix_world if check.kind == "leg" else Matrix.Identity(4)
    try:
        if check.kind == "plant" and not initial:
            initial.extend([anchor.inverted() @ matrices[0], time])
        values = measure(check, matrices, rests, axes, selected, anchor, initial[0] if initial else None)
    except ValueError as exc:
        return dict(status="inconclusive", code="degenerate_pose", reason=str(exc))
    failed = [key for key, limit in check.limits.items() if not limit[0] <= values[key] <= limit[1]]
    return dict(status="finding" if failed else "pass", values=values, exceeded=failed,
                reference_frame=initial[1] if initial else None, contact_rule=check.contact_rule)


def _contact(report, profile, identity, samples):
    if report is None:
        return {"status": "not_supplied"}
    if not isinstance(report, dict) or not isinstance(report.get("identity"), dict):
        return {"status": "unresolved", "code": "contact_identity_missing"}
    source = report["identity"]
    keys = ("file", "scene", "view_layer")
    if any(source.get(key) != identity[key] for key in keys):
        return {"status": "unresolved", "code": "contact_scene_mismatch"}
    if source.get("action") is not None and identity["actions"].get(source.get("action_object")) != source["action"]:
        return {"status": "unresolved", "code": "contact_action_mismatch"}
    required = {check.contact_rule for check in profile.checks.values() if check.contact_rule}
    rules = report.get("rules")
    findings = report.get("findings")
    if not isinstance(rules, list) or not isinstance(findings, list):
        return {"status": "unresolved", "code": "invalid_contact_report"}
    linked = {row["rule"]: row for row in rules if isinstance(row, dict)
              and isinstance(row.get("rule"), str) and row["rule"] in required}
    coverage = {}
    for key, check in profile.checks.items():
        entry = linked.get(check.contact_rule)
        if entry:
            times = entry.get("sample_times", [])
            measured = [sample["frame"] for sample in samples if key in sample["checks"] and sample["frame"] in times]
            missing = [sample["frame"] for sample in samples if key in sample["checks"] and sample["frame"] not in times]
            coverage[key] = dict(measured=measured, unmeasured=missing)
    return dict(status="linked_sampled_evidence", range=report.get("range"),
                sampling_finished=report.get("sampling_finished") is True,
                rules={key: row.get("status") for key, row in linked.items()}, coverage=coverage,
                unresolved_rules=sorted(required - linked.keys()),
                findings=[row for row in findings if isinstance(row, dict) and row.get("rule") in linked])


def inspect(bpy, parameters, deadline):
    scene = bpy.context.scene
    layer = bpy.context.view_layer
    if scene is None or layer is None or bpy.context.mode not in {"OBJECT", "POSE"}:
        return failure("rig_context_required", "Use an evaluated Blender scene in Object or Pose Mode.")
    if bpy.app.autoexec_fail:
        return failure("drivers_not_evaluated", "Blender blocked authored drivers. Use the trusted live scene.")
    start = scene.frame_current, scene.frame_subframe
    try:
        profile = InspectionProfile.parse(parameters.get("profile"))
        times = frames(parameters.get("frames"), sum(start))
        if len(times) * len(profile.roles) > 1024:
            raise ValueError("Split the inspection into at most 1,024 joint/frame measurements.")
        views = parameters.get("views", [])
        if not isinstance(views, list) or (views and set(views) != {"front", "side"}) or len(views) not in (0, 2):
            raise ValueError("Request matched front and side views together.")
        evidence_frame = parameters.get("evidence_frame")
        if evidence_frame is None:
            evidence_frame = times[0]
        elif evidence_frame not in times or not views:
            raise ValueError("The evidence frame must be a requested sample with matched views.")
    except (ValueError, TypeError, OverflowError) as exc:
        return failure("bad_rig_profile", str(exc))
    results, bones = {}, {}
    for key, term in profile.roles.items():
        try:
            results[key], bones[key] = _role(layer, term)
        except ValueError as exc:
            results[key], bones[key] = dict(status="inconclusive", code="degenerate_rest", reason=str(exc)), None
    objects = {term.object for term in profile.roles.values()}
    actions = {}
    for key in sorted(objects):
        obj = layer.objects.get(key)
        anim = obj.animation_data if obj else None
        actions[key] = anim.action.name if anim and anim.action else None
    identity = dict(scene=scene.name, file=bpy.data.filepath or None, view_layer=layer.name, actions=actions)
    rest = {key: item["rest"] for key, item in results.items() if "rest" in item}
    initial = {key: [] for key in profile.checks}
    samples, evidence = [], []
    try:
        skin = {key: vertex_bindings(layer, term, deadline) for key, term in profile.bindings.items()}
        for time in times:
            if monotonic() >= deadline:
                raise InspectionLimit("The rig inspection reached its deadline.")
            set_frame(scene, time)
            posed = {}
            for key, bone in bones.items():
                if bone is None:
                    continue
                rig = bone.id_data
                try:
                    value = _joint(rig.matrix_world @ bone.matrix, rig.matrix_world @ bone.tail)
                except ValueError:
                    posed[key] = dict(status="inconclusive", code="degenerate_pose")
                    continue
                control = rig.pose.bones.get(profile.roles[key].control) if profile.roles[key].control else None
                if control:
                    try:
                        value["control"] = _joint(rig.matrix_world @ control.matrix, rig.matrix_world @ control.tail)
                    except ValueError:
                        value["control"] = dict(status="inconclusive", code="degenerate_control")
                value.update(constraints=[_constraint(c) for c in bone.constraints],
                             control_constraints=[_constraint(c) for c in control.constraints] if control else [])
                posed[key] = value
            checks = {key: _check(layer, term, bones, profile.roles, time, initial[key])
                      for key, term in profile.checks.items()}
            samples.append(dict(frame=time, evaluated_frame=scene.frame_current_final, posed=posed,
                                switches={key: _switch(layer, term) for key, term in profile.switches.items()},
                                checks={key: value for key, value in checks.items() if value is not None}))
            if time == evidence_frame:
                lines = {key: value for key, value in posed.items() if "head" in value}
                if rest and lines:
                    evidence = [dict(frame=time, view=view, svg=overlay(rest, lines, view, time)) for view in views]
    except InspectionLimit as exc:
        return failure("rig_inspection_deadline", str(exc), "completed")
    finally:
        if (scene.frame_current, scene.frame_subframe) != start:
            scene.frame_set(start[0], subframe=start[1])
    return success(dict(version=1, profile=profile.name, identity=identity, starting_frame=sum(start),
                        skeletal_fit=results, binding=skin, samples=samples, evidence=evidence,
                        attached_geometry=_contact(parameters.get("contact_report"), profile, identity, samples)))
