"""Profile parsing and native FK rig validation."""

from dataclasses import dataclass
from math import isfinite

from mathutils import Vector


class PoseProblem(ValueError):
    """An editable rig or requested pose cannot satisfy the declared profile."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(message):
    raise PoseProblem("invalid_rig_profile", message)


def number(value, minimum, maximum, label):
    if type(value) not in (int, float) or not isfinite(value) or not minimum <= value <= maximum:
        _fail(f"{label} must be a finite number between {minimum:g} and {maximum:g}.")
    return float(value)


def vector(value, label, size=3):
    if not isinstance(value, list) or len(value) != size:
        _fail(f"{label} must have {size} values.")
    return [number(val, -1e8, 1e8, label) for val in value]


def _name(value, label):
    if not isinstance(value, str) or not value or len(value) > 256:
        _fail(f"Provide a valid {label}.")
    return value


def _descends(bone, ancestor):
    while bone.parent is not None:
        bone = bone.parent
        if bone == ancestor:
            return True
    return False


@dataclass(frozen=True)
class Profile:
    rig: str
    controls: tuple
    palm_axis: tuple
    anchor: str | None
    hand_witness: str | None
    fk_switch: dict | None
    clearance: tuple
    max_swivel: float
    min_improvement: float
    elbow_range: tuple
    wrist_limit: float
    position_tolerance: float
    rotation_tolerance: float
    neighbour_position: float
    neighbour_rotation: float
    neighbour_step: float

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            _fail("Use elbow rig profile version 1.")
        controls = data.get("controls")
        if not isinstance(controls, dict):
            _fail("Supply upper, forearm and hand control names.")
        names = tuple(_name(controls.get(key), f"{key} control") for key in ("upper", "forearm", "hand"))
        if len(set(names)) != 3:
            _fail("Use three distinct controls.")
        axis = Vector(vector(data.get("palm_axis"), "palm_axis"))
        if axis.length <= 1e-8:
            _fail("palm_axis must identify a nonzero hand-local direction.")
        limits = data.get("limits", {})
        if not isinstance(limits, dict):
            _fail("limits must be an object.")
        span = vector(limits.get("elbow_bend", [5, 150]), "elbow_bend", 2)
        if not 0 < span[0] <= span[1] < 180:
            _fail("elbow_bend must be an increasing interval within (0, 180).")
        clearance = data.get("clearance")
        if not isinstance(clearance, list) or not 1 <= len(clearance) <= 4:
            _fail("Specify one to four measurable clearance pairs.")
        checks = []
        for check in clearance:
            if not isinstance(check, dict) or not isinstance(check.get("pair"), list) or len(check["pair"]) != 2:
                _fail("Each clearance check needs two object names in pair.")
            a, b = (_name(item, "mesh name") for item in check["pair"])
            if a == b:
                _fail("Clearance pairs must name distinct meshes.")
            checks.append((a, b, number(check.get("minimum_gap", 0), 0, 1e6, "minimum_gap")))
        switch = data.get("fk_switch")
        if switch is not None:
            if not isinstance(switch, dict):
                _fail("fk_switch must name a bone, property and FK value.")
            switch = {
                "bone": _name(switch.get("bone"), "FK switch bone"),
                "property": _name(switch.get("property"), "FK switch property"),
                "value": number(switch.get("value"), -1e6, 1e6, "FK switch value"),
            }
        anchor = data.get("anchor_object")
        witness = data.get("hand_witness_object")
        return cls(
            _name(data.get("rig"), "armature name"), names, tuple(axis.normalized()),
            _name(anchor, "anchor object") if anchor is not None else None,
            _name(witness, "hand witness object") if witness is not None else None,
            switch, tuple(checks),
            number(limits.get("max_swivel", 20), .1, 65, "max_swivel"),
            number(limits.get("min_improvement", .1), .001, 45, "min_improvement"),
            tuple(span),
            number(limits.get("wrist_bend", 65), 1, 170, "wrist_bend"),
            number(limits.get("position_tolerance", .0001), 1e-8, 1, "position_tolerance"),
            number(limits.get("rotation_tolerance", .1), .0001, 30, "rotation_tolerance"),
            number(limits.get("neighbour_position", .001), 1e-8, 1, "neighbour_position"),
            number(limits.get("neighbour_rotation", 1), .0001, 45, "neighbour_rotation"),
            number(limits.get("neighbour_step", 9), .001, 90, "neighbour_step"),
        )


@dataclass
class Context:
    profile: Profile
    rig: object
    bones: tuple
    animation: object
    action: object
    slot_identifier: str
    anchor: object
    witness: object
    meshes: dict

    @classmethod
    def resolve(cls, bpy, p):
        layer = bpy.context.view_layer
        if layer is None or bpy.context.mode != "OBJECT" and not bpy.context.mode.startswith("POSE"):
            raise PoseProblem("unsupported_pose_context", "Use Object or Pose Mode with an evaluated view layer.")
        if bpy.app.autoexec_fail:
            raise PoseProblem("drivers_not_evaluated", "Blender blocked authored drivers. Use a trusted live copy to preview this rig.")
        rig = layer.objects.get(p.rig)
        if rig is None or rig.type != "ARMATURE" or not rig.is_editable:
            raise PoseProblem("rig_unavailable", "The named editable armature is unavailable in this view layer.")
        bones = tuple(rig.pose.bones.get(name) for name in p.controls)
        if any(bone is None for bone in bones):
            raise PoseProblem("control_unavailable", "The profile's FK controls do not exist on this rig.")
        upper, forearm, hand = bones
        if forearm.parent != upper or not _descends(hand, forearm):
            raise PoseProblem("unsupported_fk_chain", "The named control chain cannot transport the elbow plane.")
        if any(bone.rotation_mode != "QUATERNION" or any(bone.lock_rotation) or bone.lock_rotation_w
               or any(not c.mute and c.influence > 1e-6 for c in bone.constraints)
               for bone in (upper, forearm, hand)):
            raise PoseProblem("unsupported_fk_controls", "Use unlocked quaternion FK controls without active constraints.")
        world = rig.matrix_world.to_3x3()
        columns = [world.col[i] for i in range(3)]
        sizes = [column.length for column in columns]
        if (min(sizes) < 1e-6 or max(sizes) - min(sizes) > max(sizes) * 1e-5 or world.determinant() <= 0
                or any(abs(columns[i].normalized().dot(columns[j].normalized())) > 1e-5
                       for i, j in ((0, 1), (0, 2), (1, 2)))):
            raise PoseProblem("unsupported_rig_transform", "Use a rig object with uniform positive scale and no shear.")
        animation = rig.animation_data
        if animation is None or animation.action is None or animation.action_slot is None:
            raise PoseProblem("action_required", "The FK rig needs an assigned layered Action and slot.")
        action = animation.action
        if (len(action.layers) != 1 or len(action.layers[0].strips) != 1
                or action.layers[0].strips[0].type != "KEYFRAME"
                or animation.use_nla and any(not track.mute for track in animation.nla_tracks)
                or animation.action_blend_type != "REPLACE" or animation.action_influence != 1):
            raise PoseProblem("unsupported_action", "Use one directly assigned keyframe strip without active NLA blending.")
        paths = {bone.path_from_id("rotation_quaternion") for bone in bones}
        if any(driver.data_path in paths for driver in animation.drivers):
            raise PoseProblem("driven_fk_control", "A profile control is driven rather than keyed directly.")
        anchor = layer.objects.get(p.anchor) if p.anchor else None
        witness = layer.objects.get(p.hand_witness) if p.hand_witness else None
        if p.anchor and anchor is None or p.hand_witness and witness is None:
            raise PoseProblem("anchor_unavailable", "The anchor or hand witness object is unavailable.")
        meshes = {name: layer.objects.get(name) for a, b, _ in p.clearance for name in (a, b)}
        if any(not item or item.type != "MESH" for item in meshes.values()):
            raise PoseProblem("clearance_surface_unavailable", "A profile clearance surface is missing or not a mesh.")
        return cls(p, rig, bones, animation, action, animation.action_slot.identifier, anchor, witness, meshes)

    def require_fk(self):
        switch = self.profile.fk_switch
        if not switch:
            return
        control = self.rig.pose.bones.get(switch["bone"])
        actual = control.get(switch["property"]) if control else None
        if type(actual) not in (int, float) or abs(actual - switch["value"]) > 1e-6:
            raise PoseProblem("fk_mode_required", "The profile's FK switch must be active at all checked frames.")
