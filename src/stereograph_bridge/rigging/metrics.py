"""Measured pose checks in declared joint and anchor coordinates."""

from math import atan2, degrees

from mathutils import Vector

from ..posing.solver import anchored_drift, swivel_axis


def axis_direction(matrix, local):
    result = matrix.to_3x3() @ Vector(local)
    if result.length < 1e-8:
        raise ValueError("The measured joint axis collapsed.")
    return result.normalized()


def angle(a, b):
    if min(a.length, b.length) < 1e-8:
        raise ValueError("The measured joint direction collapsed.")
    return degrees(a.angle(b))


def signed_angle(a, b, normal):
    if min(a.length, b.length, normal.length) < 1e-8:
        raise ValueError("The measured bend plane is undefined.")
    return degrees(atan2(normal.normalized().dot(a.cross(b)), a.dot(b)))


def knee_state(hip, knee, ankle, forward):
    state = {"shoulder": list(hip), "elbow": list(knee), "wrist": list(ankle)}
    origin, axis, radial = swivel_axis(state)
    target = forward - axis * forward.dot(axis)
    upper, lower = knee - hip, ankle - knee
    return dict(tracking=signed_angle(target, radial, axis), knee_bend=angle(upper, lower),
                upper_length=upper.length, lower_length=lower.length), (origin, axis, radial)


def arm(matrices, axes):
    shoulder, elbow, wrist = (matrix.translation for matrix in matrices)
    upper, lower = elbow - shoulder, wrist - elbow
    length = upper.length + lower.length
    return dict(reach_ratio=(wrist - shoulder).length / length if length > 1e-8 else 0,
                elbow_bend=angle(upper, lower),
                wrist_bend=angle(lower, axis_direction(matrices[-1], axes[-1])))


def finger(matrices, axes):
    first, middle, tip = (axis_direction(matrix, axis) for matrix, axis in zip(matrices, axes))
    base, end = angle(first, middle), angle(middle, tip)
    return dict(base_bend=base, tip_bend=end, curl=base + end)


def thumb(matrices, rests, axes, bones):
    palm, cmc, mcp, ip = matrices
    ref_palm, ref_cmc, _, _ = rests
    relative = palm.to_quaternion().inverted() @ cmc.to_quaternion()
    reference = ref_palm.to_quaternion().inverted() @ ref_cmc.to_quaternion()
    local = relative @ Vector(axes[1])
    baseline = reference @ Vector(axes[1])
    first, second, third = (axis_direction(matrix, axis) for matrix, axis in zip(matrices[1:], axes[1:]))
    span = signed_angle(Vector((baseline.x, baseline.y, 0)), Vector((local.x, local.y, 0)), Vector((0, 0, 1)))
    elevation = lambda direction: degrees(atan2(direction.z, (direction.x ** 2 + direction.y ** 2) ** .5))
    tip = bones[-1].id_data.matrix_world @ bones[-1].tail
    return dict(basal_rotation=anchored_drift(reference.to_matrix().to_4x4(),
                                             relative.to_matrix().to_4x4())["rotation_degrees"],
                basal_sweep=span, basal_elevation=elevation(local) - elevation(baseline),
                mcp_bend=angle(first, second), ip_bend=angle(second, third),
                tip_to_palm=(tip - palm.translation).length)


def measure(check, matrices, rests, axes, bones, anchor, reference):
    if check.kind == "arm":
        return arm(matrices, axes)
    if check.kind == "finger":
        return finger(matrices, axes)
    if check.kind == "thumb":
        return thumb(matrices, rests, axes, bones)
    if check.kind == "leg":
        forward = axis_direction(anchor, check.forward)
        return knee_state(*(matrix.translation for matrix in matrices), forward)[0]
    drift = anchored_drift(reference, anchor.inverted() @ matrices[0])
    return dict(position_drift=drift["position"], rotation_drift=drift["rotation_degrees"])
