"""Author exact synthetic geometry and keyed cases in a disposable Blender file."""

from math import radians
from pathlib import Path
import sys

import bpy
from mathutils import Matrix


QUADS = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))


def geometry(name, vertices, polygons):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, (), polygons)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box_data(center, dimensions):
    return [tuple(c + d * f / 2 for c, d, f in zip(center, dimensions, signs)) for signs in (
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    )]


def box(name, location=(0, 0, 0), dimensions=(2, 2, 2), open_top=False):
    obj = geometry(name, box_data((0, 0, 0), dimensions), QUADS[:1] + QUADS[2:] if open_top else QUADS)
    obj.location = location
    return obj


def combined_boxes(name, boxes):
    points, faces = [], []
    for center, dimensions in boxes:
        start = len(points)
        points.extend(box_data(center, dimensions))
        faces.extend(tuple(index + start for index in quad) for quad in QUADS)
    return geometry(name, points, faces)


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "SpatialCalibration"
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = .1

anchor = box("Anchor")
distant = box("Known gap", (2.25, 0, 0))
box("Exactly touching", (2, 0, 0))
box("Crossing", (1.25, .25, .125))
box("Nested", dimensions=(1, 1, 1))
box("Open shell", open_top=True)
combined_boxes("Self-intersecting shell", [((-.2, 0, 0), (2, 2, 2)), ((.2, .1, .2), (2, 2, 2))])

turn = Matrix.Translation((4, -3, 2)) @ Matrix.Rotation(radians(37), 4, "Z")
box("Rotated anchor").matrix_world = turn @ anchor.matrix_world
box("Rotated gap").matrix_world = turn @ Matrix.Translation(distant.location)
scaled = box("Mirrored and stretched", (3.5, 0, 0))
scaled.scale = (-2, .75, 1.5)

geometry("Edge triangle A", [(-2, 0, 0), (2, 0, 0), (0, -2, -1)], [(0, 1, 2)])
geometry("Edge triangle B", [(0, -1, 1), (0, 1, 1), (1, 0, 3)], [(0, 1, 2)])
geometry("Invalid degenerate", [(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1, 2)])

moving = box("Subframe motion")
for frame, x in ((10, 2.5), (10.5, 1.25), (11, 2.5)):
    moving.location = (x, .375, .25)
    moving.keyframe_insert(data_path="location", frame=frame)

armature = bpy.data.armatures.new("Fixture bones")
rig = bpy.data.objects.new("Fixture armature", armature)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bone = armature.edit_bones.new("Deform")
bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
bpy.ops.object.mode_set(mode="OBJECT")
deformed = box("Armature-deformed", (3, .375, .25))
group = deformed.vertex_groups.new(name="Deform")
group.add(list(range(8)), 1, "REPLACE")
modifier = deformed.modifiers.new("Fixture armature", "ARMATURE")
modifier.object = rig
for frame, x in ((1, 0), (2, -1.75)):
    rig.pose.bones["Deform"].location = (x, 0, 0)
    rig.pose.bones["Deform"].keyframe_insert(data_path="location", frame=frame)

hand = combined_boxes(
    "Synthetic glove",
    [((0, 0, 0), (1.35, .46, .86)), ((-.77, -.16, .26), (.35, .55, .35))]
    + [((x, -.33, -.44), (.23, .53, .49)) for x in (-.48, -.16, .16, .48)],
)
hand.location = (0, 6, 1)
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=.25, depth=2, location=(.12, 5.64, .58),
                                    rotation=(0, radians(90), 0))
bpy.context.object.name = "Synthetic handle"

scene.frame_set(7, subframe=.25)
bpy.ops.wm.save_as_mainfile(filepath=str(Path(sys.argv[-1])))
