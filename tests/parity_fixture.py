"""Create the shared parity fixture and its linked library in an empty directory."""

from pathlib import Path
import shutil
import sys

import bpy

path = Path(sys.argv[-1])
library_path = path.with_name("fixture library.blend")

mesh = bpy.data.meshes.new("Library Mesh")
linked = bpy.data.objects.new("Linked Marker", mesh)
bpy.context.scene.collection.objects.link(linked)
bpy.ops.wm.save_as_mainfile(filepath=str(library_path))

bpy.ops.wm.read_factory_settings(use_empty=False)
scene = bpy.context.scene
scene.name = "StereographFixture"
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 480
scene.render.resolution_y = 320
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = "//previous-render"
scene["fixture_value"] = 10

cube = bpy.data.objects["Cube"]
cube.name = "Inspection Cube"
cube.data.name = "Fixture Geometry"
cube.location = (1, 0.25, 0)
cube.modifiers.new("Fixture Bevel", "BEVEL").width = .1
cube.keyframe_insert(data_path="location", frame=1)
scene.frame_set(1)
nested = bpy.data.collections.new("Inspection Objects")
scene.collection.children.link(nested)
nested.objects.link(cube)
original = bpy.data.collections["Collection"]
original.objects.unlink(cube)
hidden = bpy.data.objects.new("Hidden Marker", None)
hidden.hide_viewport = True
nested.objects.link(hidden)

with bpy.data.libraries.load(str(library_path), link=True) as (_, target):
    target.objects = ["Linked Marker"]
nested.objects.link(target.objects[0])

for name, filename in (("Missing Fixture Image", "missing-texture.png"), ("Existing Fixture Image", "real-texture.ppm")):
    image = bpy.data.images.new(name, 4, 4)
    image.source = "FILE"
    image.filepath = f"//{filename}"
    image.use_fake_user = True
path.with_name("real-texture.ppm").write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")

bpy.ops.wm.save_as_mainfile(filepath=str(path))
shutil.copyfile(path, path.with_name(path.name + "1"))
