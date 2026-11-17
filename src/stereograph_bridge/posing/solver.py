"""Transport an FK elbow plane around the planted wrist axis."""

from math import acos, atan2, degrees, radians

from mathutils import Matrix, Quaternion, Vector


def _dot_angle(first, second):
    cosine = first.normalized().dot(second.normalized())
    return degrees(acos(max(-1.0, min(1.0, cosine))))


def pose_state(rig, upper, forearm, hand, hand_axis):
    world = rig.matrix_world
    shoulder, elbow, wrist = (world @ item.head for item in (upper, forearm, hand))
    palm = world @ hand.matrix
    direction = palm.to_3x3() @ Vector(hand_axis)
    return {
        "shoulder": list(shoulder), "elbow": list(elbow), "wrist": list(wrist),
        "palm_direction": list(direction.normalized()),
        "upper_length": (elbow - shoulder).length, "forearm_length": (wrist - elbow).length,
        "elbow_bend_degrees": _dot_angle(elbow - shoulder, wrist - elbow),
        "wrist_bend_degrees": _dot_angle(wrist - elbow, direction),
    }


def swivel_axis(state):
    shoulder, elbow, wrist = (Vector(state[key]) for key in ("shoulder", "elbow", "wrist"))
    reach = wrist - shoulder
    if reach.length < 1e-8:
        raise ValueError("The shoulder and wrist coincide.")
    axis = reach.normalized()
    radial = elbow - shoulder
    radial -= axis * radial.dot(axis)
    if radial.length < 1e-7:
        raise ValueError("The elbow plane is undefined at this arm extension.")
    return shoulder, axis, radial


def best_swivel(state):
    _, axis, _ = swivel_axis(state)
    hand = Vector(state["palm_direction"])
    forearm = Vector(state["wrist"]) - Vector(state["elbow"])
    u = forearm - axis * forearm.dot(axis)
    v = hand - axis * hand.dot(axis)
    if min(u.length, v.length) <= 1e-9:
        return 0.0
    return degrees(atan2(axis.dot(u.cross(v)), u.dot(v)))


def swivel_for_target(state, target):
    shoulder, axis, current = swivel_axis(state)
    requested = Vector(target) - shoulder
    direction = requested - axis * requested.dot(axis)
    if direction.length <= 1e-9:
        raise ValueError("The requested elbow target does not define an elbow plane.")
    delta = degrees(atan2(axis.dot(current.cross(direction)), current.dot(direction)))
    attainable = shoulder + axis * (Vector(state["elbow"]) - shoulder).dot(axis) + direction.normalized() * current.length
    return delta, (Vector(target) - attainable).length


def _local_rotation(bone, desired):
    kwargs = {}
    if bone.parent:
        kwargs.update(parent_matrix=bone.parent.matrix, parent_matrix_local=bone.parent.bone.matrix_local)
    basis = bone.bone.convert_local_to_pose(desired, bone.bone.matrix_local, invert=True, **kwargs)
    quaternion = basis.to_quaternion()
    if quaternion.dot(bone.rotation_quaternion) < 0:
        quaternion.negate()
    return quaternion


def place(bpy, rig, upper, hand, state, amount, palm_target):
    shoulder, axis, _ = swivel_axis(state)
    spin = Quaternion(axis, radians(amount)).to_matrix().to_4x4()
    rotation = Matrix.Translation(shoulder) @ spin @ Matrix.Translation(-shoulder)
    world = rig.matrix_world
    inverse = world.inverted()
    upper.rotation_quaternion = _local_rotation(upper, inverse @ rotation @ world @ upper.matrix)
    bpy.context.view_layer.update()
    hand.rotation_quaternion = _local_rotation(hand, inverse @ palm_target)
    bpy.context.view_layer.update()


def anchored_drift(before, after):
    translation = (after.translation - before.translation).length
    first, second = before.to_quaternion().normalized(), after.to_quaternion().normalized()
    # Use the quaternion difference norm to retain precision near a planted pose.
    if first.dot(second) < 0:
        second.negate()
    plus = sum((a + b) ** 2 for a, b in zip(first, second)) ** .5
    minus = sum((a - b) ** 2 for a, b in zip(first, second)) ** .5
    return {"position": translation, "rotation_degrees": degrees(4 * atan2(minus, plus))}
