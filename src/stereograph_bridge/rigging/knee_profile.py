"""Resolve a native two-bone IK chain with an editable location-only pole."""

from dataclasses import dataclass

from ..posing.profile import PoseProblem
from ..review.profile import mapping, name, number
from .profile import axis


@dataclass(frozen=True)
class KneeProfile:
    rig: str
    upper: str
    lower: str
    foot: str
    mechanism: str
    pole: str
    forward: tuple
    anchor: str | None
    pairs: tuple
    swivel: float
    gain: float
    position: float
    rotation: float
    neighbour: float

    @classmethod
    def parse(cls, value):
        data = mapping(value, "Knee profile", {"version", "rig", "controls", "forward", "anchor", "clearance", "limits"})
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Use planted-foot knee profile version 1.")
        roles = ("upper", "lower", "foot", "ik_owner", "pole")
        controls = mapping(data.get("controls"), "Knee controls", set(roles))
        names = tuple(name(controls.get(key), f"{key} bone") for key in roles)
        if len(set(names[:3])) != 3 or names[-1] in names[:-1]:
            raise ValueError("Use distinct hip, knee, foot and pole bones.")
        limits = mapping(data.get("limits", {}), "Knee limits",
                         {"max_swivel", "minimum_improvement", "position", "rotation", "neighbour_step"})
        pairs = data.get("clearance")
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 3:
            raise ValueError("Provide one to three mesh clearance pairs.")
        checked = []
        for pair in pairs:
            pair = mapping(pair, "Clearance surface pair", {"pair", "minimum_gap"})
            objects = pair.get("pair")
            if not isinstance(objects, list) or len(objects) != 2 or objects[0] == objects[1]:
                raise ValueError("A clearance check needs two distinct meshes.")
            a, b = (name(obj, "mesh") for obj in objects)
            gap = number(pair.get("minimum_gap", 0), "Minimum gap", 0, 10)
            checked.append((a, b, gap))
        return cls(name(data.get("rig"), "armature"), *names, axis(data.get("forward", [0, 1, 0]), "Forward"),
                   name(data["anchor"], "anchor") if "anchor" in data else None, tuple(checked),
                   number(limits.get("max_swivel", 25), "Maximum swivel", .1, 60),
                   number(limits.get("minimum_improvement", 1), "Minimum tracking improvement", .01, 60),
                   number(limits.get("position", .0001), "Position tolerance", 1e-8, .01),
                   number(limits.get("rotation", .1), "Rotation tolerance", .001, 3),
                   number(limits.get("neighbour_step", 12), "Neighbour step limit", .01, 60))


@dataclass
class KneeContext:
    profile: KneeProfile
    rig: object
    bones: tuple
    animation: object
    action: object
    slot_identifier: str
    anchor: object
    witness: object
    meshes: dict

    @property
    def pole(self):
        return self.bones[4]

    @property
    def mechanism(self):
        return self.bones[3]

    @classmethod
    def resolve(cls, bpy, profile):
        layer = bpy.context.view_layer
        rig = layer.objects.get(profile.rig) if layer else None
        if not bpy.context.mode in {"OBJECT", "POSE"} or bpy.app.autoexec_fail:
            raise PoseProblem("knee_context_required", "Use Object or Pose Mode in a trusted, evaluated scene.")
        if rig is None or rig.type != "ARMATURE" or not rig.is_editable:
            raise PoseProblem("knee_rig_missing", "The named editable armature is unavailable.")
        roles = (profile.upper, profile.lower, profile.foot, profile.mechanism, profile.pole)
        bones = tuple(rig.pose.bones.get(key) for key in roles)
        if any(bone is None for bone in bones):
            raise PoseProblem("knee_control_missing", "A profile bone could not be resolved.")
        pole = bones[-1]
        if pole.bone.use_connect or any(pole.lock_location) or any(
            not con.mute and con.influence > 1e-6 for con in pole.constraints
        ):
            raise PoseProblem("knee_pole_locked", "The pole must have independent unlocked translation.")
        matrix = rig.matrix_world.to_3x3()
        columns = [matrix.col[index] for index in range(3)]
        sizes = [column.length for column in columns]
        if (min(sizes) < 1e-6 or max(sizes) - min(sizes) > max(sizes) * 1e-5 or matrix.determinant() <= 0
                or any(abs(columns[i].normalized().dot(columns[j].normalized())) > 1e-5
                       for i, j in ((0, 1), (0, 2), (1, 2)))):
            raise PoseProblem("unsupported_knee_transform", "Use a positive uniform rig scale without shear.")
        animation = rig.animation_data
        action = animation.action if animation else None
        if (action is None or animation.action_slot is None or len(action.layers) != 1
                or len(action.layers[0].strips) != 1 or action.layers[0].strips[0].type != "KEYFRAME"
                or animation.use_nla and any(not track.mute for track in animation.nla_tracks)
                or animation.action_blend_type != "REPLACE" or animation.action_influence != 1):
            raise PoseProblem("unsupported_knee_action", "Use a directly assigned Action with one keyframe strip.")
        if any(f.data_path == pole.path_from_id("location") for f in animation.drivers):
            raise PoseProblem("driven_knee_pole", "The pole's location is driven.")
        anchor = layer.objects.get(profile.anchor) if profile.anchor else None
        if profile.anchor and (anchor is None or abs(anchor.matrix_world.determinant()) < 1e-12):
            raise PoseProblem("knee_anchor_missing", "The named planted-foot reference is unavailable or degenerate.")
        meshes = {key: layer.objects.get(key) for a, b, _ in profile.pairs for key in (a, b)}
        if any(obj is None or obj.type != "MESH" for obj in meshes.values()):
            raise PoseProblem("knee_clearance_mesh_missing", "A clearance pair could not be resolved in this view layer.")
        return cls(profile, rig, bones, animation, action, animation.action_slot.identifier,
                   anchor, None, meshes)

    def require_ik(self):
        active = [con for con in self.mechanism.constraints
                  if con.type == "IK" and not con.mute and con.influence > 1e-6 and con.is_valid]
        if len(active) != 1:
            raise PoseProblem("knee_ik_required", "The named mechanism needs exactly one active IK constraint.")
        ik, = active
        if (abs(ik.influence - 1) > 1e-6 or ik.chain_count != 2 or not ik.target
                or ik.pole_target != self.rig or ik.pole_subtarget != self.pole.name):
            raise PoseProblem("knee_ik_required", "Use a full-influence two-bone IK chain with the named pole.")
        return ik
