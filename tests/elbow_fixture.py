"""Create a planted-hand FK fixture with animated prop and clearance surfaces."""

from math import radians
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion


def cube(name, location, size, parent=None, bone=None):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    if parent:
        obj.parent = parent
        if bone:
            obj.parent_type = "BONE"
            obj.parent_bone = bone
    obj.matrix_world = Matrix.Translation(location)
    bpy.context.view_layer.update()
    return obj


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "ElbowCalibration"
prop = bpy.data.objects.new("Moving prop", None)
scene.collection.objects.link(prop)
rig = bpy.data.objects.new("FK arm", bpy.data.armatures.new("FK anatomy"))
scene.collection.objects.link(rig)
rig.parent = prop
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
arm = rig.data.edit_bones
upper = arm.new("Upper")
upper.head, upper.tail = (0, 0, 0), (1, 0, .5)
forearm = arm.new("Forearm")
forearm.head, forearm.tail = upper.tail, (2, 0, 0)
forearm.parent, forearm.use_connect = upper, True
palm = arm.new("Palm")
palm.head, palm.tail = forearm.tail, (2.35, .18, -.10)
palm.parent, palm.use_connect = forearm, True
bpy.ops.object.mode_set(mode="OBJECT")
for bone in rig.pose.bones:
    bone.rotation_mode = "QUATERNION"
    for frame in (35, 36, 37, 38, 39):
        bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
rig.animation_data.action.name = "Calibrated FK pose"

center = (1.33, 0, .33)
guard = cube("Forearm guard", center, .105, rig, "Forearm")
glove = cube("Leather glove", (2.14, .06, -.035), .2, rig, "Palm")
blocked_center = Quaternion((1, 0, 0), radians(25)) @ guard.matrix_world.translation
obstacle = cube("Cuff obstacle", blocked_center, .11, prop)
# An independent bone-parented object checks the palm transform that an attached
# glove, nail or separate hand rig would receive.
attached = bpy.data.objects.new("Hand witness", None)
scene.collection.objects.link(attached)
attached.parent = rig
attached.parent_type = "BONE"
attached.parent_bone = "Palm"

for frame, value in ((35, -.06), (37, 0), (39, .06)):
    prop.location = (0, value, -value / 2)
    prop.rotation_euler = (0, .3 * value, value)
    prop.keyframe_insert(data_path="location", frame=frame)
    prop.keyframe_insert(data_path="rotation_euler", frame=frame)
scene.frame_set(37)
bpy.context.view_layer.objects.active = rig
bpy.ops.wm.save_as_mainfile(filepath=str(Path(sys.argv[-1])))
