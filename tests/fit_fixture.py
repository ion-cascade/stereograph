"""Create a synthetic skirt, animated knee plate, trim and drifting fastener."""

from pathlib import Path
import sys

import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "ClothingFitCalibration"
scene.frame_start, scene.frame_end = 7, 13

mesh = bpy.data.meshes.new("Panel grid")
points = [(x*.02, 0, z*.02) for z in range(-8, 9) for x in range(-8, 9)]
faces = [(y*17+x, y*17+x+1, (y+1)*17+x+1, (y+1)*17+x) for y in range(16) for x in range(16)]
mesh.from_pydata(points, [], faces)
mesh.update()
panel = bpy.data.objects.new("Front skirt", mesh)
scene.collection.objects.link(panel)
mask = panel.vertex_groups.new(name="Feathered knee patch")
pin = panel.vertex_groups.new(name="Waist and sewn edges")
for vertex in mesh.vertices:
    x, _, z = vertex.co
    radius = max(abs(x), abs(z))
    if radius < .112:
        value = min(1, (.112-radius)/.062)
        mask.add([vertex.index], value, "REPLACE")
    if radius > .135:
        pin.add([vertex.index], 1, "REPLACE")
wall = panel.modifiers.new("Fabric wall", "SOLIDIFY")
wall.thickness = .002


def box(name, center, scale):
    bpy.ops.mesh.primitive_cube_add(size=2, location=center)
    obj = bpy.context.object
    obj.name = name
    for vertex in obj.data.vertices:
        for i, factor in enumerate(scale):
            vertex.co[i] *= factor
    return obj


plate = box("Knee plate", (0, -.026, 0), (.025, .023, .034))
for time, y in ((8, -.04), (10, -.014), (12, -.04)):
    plate.location.y = y
    plate.keyframe_insert(data_path="location", index=1, frame=time)
plate.animation_data.action.name = "Knee approaches skirt"
box("Clearance guard", (0, .028, 0), (.026, .004, .034))
trim = box("Embroidered edge", (0, 0, .154), (.135, .002, .003))
trim.parent = panel
joint = box("Hanger", (.146, .005, .154), (.008, .007, .008))
joint.parent = panel
for time, x in ((8, .146), (10, .151), (12, .17)):
    joint.location.x = x
    joint.keyframe_insert(data_path="location", index=0, frame=time)
for obj in bpy.context.selected_objects:
    obj.select_set(False)
panel.select_set(True)
bpy.context.view_layer.objects.active = panel
scene.frame_set(10)
bpy.ops.wm.save_as_mainfile(filepath=str(Path(sys.argv[-1])))
