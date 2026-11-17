"""Create a native IK leg, articulated hand, and intentional rig inspection faults."""

from math import atan2, radians
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "RigRepairCalibration"
rig = bpy.data.objects.new("Test rig", bpy.data.armatures.new("Calibration anatomy"))
scene.collection.objects.link(rig)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
bones = rig.data.edit_bones


def bone(name, head, tail, parent=None, connected=False, deform=False):
    target = bones.new(name)
    target.head, target.tail = head, tail
    if parent:
        target.parent, target.use_connect = bones[parent], connected
    target.use_deform = deform


bone("Upper", (0, 0, 2), (0, .35, 1), deform=True)
bone("Lower", (0, .35, 1), (0, 0, 0), "Upper", True, True)
bone("Foot", (0, 0, 0), (0, .4, 0), deform=True)
bone("Foot target", (0, 0, 0), (0, .4, 0))
bone("Knee pole", (0, 1, 1), (0, 1.1, 1))
bone("Bad axis", (0, 0, 2.2), (.2, 0, 2.2))
bone("Shoulder", (0, 0, 3), (.7, .1, 3), deform=True)
bone("Elbow", (.7, .1, 3), (1.3, .38, 2.9), "Shoulder", True, True)
bone("Palm", (1.3, .38, 2.9), (1.5, .43, 2.91), "Elbow", True, True)
bone("Thumb base", (1.36, .37, 2.9), (1.42, .27, 2.9), "Palm", deform=True)
bone("Thumb middle", (1.42, .27, 2.9), (1.49, .24, 2.87), "Thumb base", True, True)
bone("Thumb tip", (1.49, .24, 2.87), (1.55, .22, 2.86), "Thumb middle", True, True)
bone("Finger base", (1.5, .43, 2.91), (1.6, .45, 2.91), "Palm", True, True)
bone("Finger middle", (1.6, .45, 2.91), (1.67, .46, 2.91), "Finger base", True, True)
bone("Finger tip", (1.67, .46, 2.91), (1.73, .47, 2.91), "Finger middle", True, True)
bpy.ops.object.mode_set(mode="OBJECT")
pose = rig.pose.bones
ik = pose["Lower"].constraints.new("IK")
ik.target, ik.subtarget = rig, "Foot target"
ik.pole_target, ik.pole_subtarget = rig, "Knee pole"
ik.chain_count, ik.use_stretch, ik.iterations = 2, False, 128
copied = pose["Foot"].constraints.new("COPY_TRANSFORMS")
copied.target, copied.subtarget = rig, "Foot target"
pose["Upper"]["IK_FK"] = 0.0
bpy.context.view_layer.update()
hip, knee, foot = (pose[name].head.copy() for name in ("Upper", "Lower", "Foot"))
axis = (foot - hip).normalized()
forward = Vector((0, 1, 0))
radial = knee - hip
radial -= axis * radial.dot(axis)
tracking = atan2(axis.dot(forward.cross(radial)), forward.dot(radial))
pole = pose["Knee pole"]
rest = pole.bone.matrix_local
desired = hip + Quaternion(axis, radians(17) - tracking) @ (pole.head - hip)
pole.location = rest.to_3x3().inverted() @ (desired - rest.translation)
for frame in (8, 9, 10, 11, 12):
    pole.keyframe_insert("location", frame=frame)
    pose["Foot target"].keyframe_insert("location", frame=frame)
for name, magnitude in (("Thumb base", 28), ("Thumb middle", 14), ("Finger middle", 37), ("Finger tip", 28)):
    control = pose[name]
    for frame, angle in ((9, 0), (10, 0), (11, magnitude), (12, 0)):
        control.rotation_quaternion = Quaternion((1, 0, 0), radians(angle))
        control.keyframe_insert("rotation_quaternion", frame=frame)
rig.animation_data.action.name = "Calibration walk"


def box(name, center, size, mount=None):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    if mount:
        obj.parent, obj.parent_type, obj.parent_bone = rig, "BONE", mount
    obj.matrix_world = Matrix.Translation(center)
    bpy.context.view_layer.update()
    return obj


scene.frame_set(10)
pad = box("Knee guard", (.1, .3, 1), .12, "Lower")
box("Side obstacle", (.8, .3, 1), .15)
skin = box("Calibrated skin", (0, .2, .9), .19)
lower = skin.vertex_groups.new(name="Lower")
upper = skin.vertex_groups.new(name="Upper")
mask = skin.vertex_groups.new(name="Knee patch")
mask.add(list(range(8)), 1, "REPLACE")
lower.add([1], .7, "REPLACE")
upper.add([1], .3, "REPLACE")
lower.add([2], .4, "REPLACE")
lower.add([3, 4, 5, 6, 7], 1, "REPLACE")
modifier = skin.modifiers.new("Authored bind", "ARMATURE")
modifier.object = rig
bpy.context.view_layer.objects.active = rig
bpy.ops.wm.save_as_mainfile(filepath=str(Path(sys.argv[-1])))
