"""Stage a single-frame FK edit as an independent Blender Action variant."""

from contextlib import contextmanager
import hashlib
import json

from bpy_extras.anim_utils import action_get_channelbag_for_slot

from .profile import PoseProblem


def _action_signature(action):
    records = []
    strip = action.layers[0].strips[0]
    for bag in strip.channelbags:
        for curve in bag.fcurves:
            keys = [
                (tuple(key.co), tuple(key.handle_left), tuple(key.handle_right), key.interpolation,
                 key.handle_left_type, key.handle_right_type, key.easing,
                 key.amplitude, key.back, key.period)
                for key in curve.keyframe_points
            ]
            records.append((bag.slot_handle, curve.data_path, curve.array_index, curve.mute, curve.extrapolation, keys))
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def _matrix(matrix):
    return [[round(item, 8) for item in row] for row in matrix]


def fingerprint(bpy, context, frame):
    scene = bpy.context.scene
    objects = [obj for obj in (context.rig, context.anchor, context.witness) if obj is not None]
    action = context.animation.action
    state = {
        "file": bpy.data.filepath,
        "scene": (scene.name, scene.session_uid, bpy.context.view_layer.name),
        "frame": frame,
        "rig": (context.rig.name, context.rig.session_uid, context.rig.data.session_uid),
        "bones": [[bone.name, _matrix(bone.matrix), _matrix(bone.bone.matrix_local)] for bone in context.bones],
        "world": [[obj.name, obj.session_uid, _matrix(obj.matrix_world)] for obj in objects],
        "action": (action.name, action.session_uid, context.slot_identifier, _action_signature(action)),
        "surfaces": [[obj.name, obj.session_uid, obj.data.session_uid, len(obj.data.vertices),
                      _matrix(obj.matrix_world)] for obj in context.meshes.values()],
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def accept_target_curves(context, targets=None):
    if targets is None:
        targets = [(bone, "rotation_quaternion", range(4)) for bone in (context.bones[0], context.bones[2])]
    bag = action_get_channelbag_for_slot(context.action, context.animation.action_slot)
    if bag is None:
        raise PoseProblem("action_slot_required", "The assigned Action has no editable keyframe channel bag.")
    for bone, property_name, indices in targets:
        path = bone.path_from_id(property_name)
        for index in indices:
            curve = bag.fcurves.find(path, index=index)
            if curve is not None and (curve.mute or curve.lock or len(curve.modifiers) or len(curve.sampled_points)):
                raise PoseProblem("locked_action_channel", "Editable channels must be unmuted, unlocked and unmodified.")


def _assign(context, action):
    slot = next((item for item in action.slots if item.identifier == context.slot_identifier), None)
    if slot is None:
        raise PoseProblem("action_slot_required", "The native Action variant lost the source slot.")
    context.animation.action = action
    context.animation.action_slot = slot
    return slot


def _write_keys(bag, frame, edits):
    changed = []
    for bone, property_name, values in edits:
        path = bone.path_from_id(property_name)
        for index, value in enumerate(values):
            curve = bag.fcurves.find(path, index=index)
            if curve is None:
                curve = bag.fcurves.new(path, index=index, group_name=bone.name)
            existing = next((key for key in curve.keyframe_points if abs(key.co.x - frame) < 1e-6), None)
            if existing:
                shift = value - existing.co.y
                existing.co.y = value
                existing.handle_left.y += shift
                existing.handle_right.y += shift
            else:
                previous = [key for key in curve.keyframe_points if key.co.x < frame]
                interpolation = previous[-1].interpolation if previous else "BEZIER"
                created = curve.keyframe_points.insert(frame, value, options={"FAST"})
                created.interpolation = interpolation
                created.handle_left_type = created.handle_right_type = "AUTO_CLAMPED"
            curve.update()
            changed.append(dict(control=bone.name, path=path, index=index, frame=frame))
    return changed


@contextmanager
def variant(bpy, context, frame, values):
    edits = [(bone, "rotation_quaternion", rotation)
             for bone, rotation in zip((context.bones[0], context.bones[2]), values)]
    with staged_action(bpy, context, frame, edits, "Elbow") as action:
        yield action


@contextmanager
def staged_action(bpy, context, frame, edits, label):
    original = context.action
    staged = original.copy()
    staged.name = f"{original.name} · {label} {frame:g}"
    action = {"keep": False, "action": staged, "keyed": []}
    try:
        slot = _assign(context, staged)
        bag = action_get_channelbag_for_slot(staged, slot)
        action["keyed"] = _write_keys(bag, frame, edits)
        yield action
    finally:
        if not action["keep"]:
            _assign(context, original)
            bpy.data.actions.remove(staged)
