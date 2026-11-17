"""Read Blender scene and file state through the native data API."""

from collections import defaultdict
from pathlib import Path
from os.path import normpath
from time import time

from .wire import failure, success


def datablocks(bpy):
    data = bpy.data
    scene = bpy.context.scene
    return dict(
        datablock_counts={
            prop.identifier: len(items)
            for prop in data.bl_rna.properties
            if prop.type == "COLLECTION" and (items := getattr(data, prop.identifier))
        },
        render_engine=scene.render.engine if scene else None,
        scene_name=scene.name if scene else None,
        workspaces=sorted(workspace.name for workspace in data.workspaces),
        active_workspace=_name(bpy.context.workspace),
    )


def _external_path(bpy, owner, relative):
    library = owner.parent if owner.id_type == "LIBRARY" else owner.library
    path = bpy.path.abspath(relative, library=library)
    return normpath(str(Path(path).absolute()))


def missing_files(bpy):
    paths = set(bpy.utils.blend_paths(absolute=True, packed=False))
    paths = {normpath(str(Path(path).absolute())) for path in paths if path}
    owners = defaultdict(list)
    for datablock in bpy.data.all_ids:
        value = getattr(datablock, "filepath", "")
        if value:
            path = _external_path(bpy, datablock, value)
            owners[path].append((datablock.name, datablock.bl_rna.identifier))

    missing = []
    for path in sorted(paths):
        if Path(path).exists():
            continue
        for name, kind in sorted(owners[path] or [(None, "EXTERNAL")]):
            missing.append(dict(id_name=name, id_type=kind, path=path))
    return dict(missing_files=missing, total_checked=len(paths))


def linked_libraries(bpy):
    direct, indirect = [], []
    counts = defaultdict(int)
    for datablock in bpy.data.all_ids:
        if datablock.library:
            counts[datablock.library] += 1
    for library in sorted(bpy.data.libraries, key=lambda item: item.name):
        entry = dict(name=library.name, filepath=library.filepath, linked_datablocks_count=counts[library])
        if library.parent is None:
            direct.append(entry)
        else:
            indirect.append(dict(entry, parent=library.parent.name))
    return dict(direct_libraries=direct, indirect_libraries=indirect,
                total_library_count=len(direct) + len(indirect))


def _file_info(path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return dict(age_seconds=round(max(0, time() - stat.st_mtime), 1), size_bytes=stat.st_size)


def path_info(bpy):
    data = bpy.data
    path = Path(data.filepath) if data.filepath else None
    primary = _file_info(path) if path else None
    backups = []
    if path is not None:
        for sibling in sorted(path.parent.glob(path.name + "[0-9]*")):
            if sibling.name.removeprefix(path.name).isdigit() and (info := _file_info(sibling)):
                backups.append(dict(path=str(sibling), **info))
    return dict(
        filepath=data.filepath,
        is_saved=data.is_saved,
        is_dirty=data.is_dirty,
        age_seconds=primary["age_seconds"] if primary else None,
        file_size_bytes=primary["size_bytes"] if primary else None,
        backups=backups,
    )


def usage_guess(bpy):
    data = bpy.data
    scene = bpy.context.scene
    meshes = list(data.meshes)
    nodes = [modifier for obj in data.objects for modifier in obj.modifiers if modifier.type == "NODES"]
    strips = list(scene.sequence_editor.strips) if scene and scene.sequence_editor else []
    signals = {
        "Animation": len(data.actions),
        "Rendering": sum(camera.users > 0 for camera in data.cameras) if len(data.materials) > 1 else 0,
        "Scripting": len(data.texts),
        "Video Editing": len(strips),
        "Modeling": sum(len(mesh.polygons) > 6 for mesh in meshes),
        "Grease Pencil": len(data.grease_pencils),
        "Geometry Nodes": len(nodes),
        "Compositing": sum(bool(getattr(item, "compositing_node_group", None)) for item in data.scenes),
        "UV Unwrapping": sum(len(mesh.uv_layers) > 1 for mesh in meshes),
        "Motion Tracking": sum(bool(clip.tracking.tracks) for clip in data.movieclips),
        "Audio": sum(strip.type == "SOUND" for strip in strips) + len(data.speakers),
    }
    return dict(usage_guesses={
        name: dict(score=min(100, count * 25), certainty=75 if count else 50)
        for name, count in signals.items()
    })


def _name(datablock):
    return datablock.name if datablock else None


def collection_summary(bpy):
    context = bpy.context
    layer = context.view_layer
    if layer is None:
        return failure("no_view_layer", "The active scene has no view layer.")

    def layer_summary(node):
        return dict(
            name=node.name,
            exclude=node.exclude,
            hide_viewport=node.hide_viewport or node.collection.hide_viewport,
            objects=[
                dict(name=obj.name, type=obj.type, parent=_name(obj.parent), data_name=_name(obj.data),
                     selected=obj.select_get(view_layer=layer), visible=obj.visible_get(view_layer=layer))
                for obj in node.collection.objects
            ],
            children=[layer_summary(child) for child in node.children],
        )

    return success(dict(
        scene_name=context.scene.name,
        active_workspace=_name(context.workspace),
        active_object=_name(context.active_object),
        object_mode=context.mode,
        camera_object=_name(context.scene.camera),
        collections=layer_summary(layer.layer_collection),
    ))


def object_summary(bpy, name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return failure("object_not_found", f"No object named {name!r}.")
    if obj.rotation_mode == "QUATERNION":
        rotation = list(obj.rotation_quaternion.to_euler())
    elif obj.rotation_mode == "AXIS_ANGLE":
        from mathutils import Quaternion

        angle, *axis = obj.rotation_axis_angle
        rotation = list(Quaternion(axis, angle).to_euler())
    else:
        rotation = list(obj.rotation_euler)
    return success(dict(
        name=obj.name, type=obj.type, data_name=_name(obj.data),
        location=list(obj.location), rotation=rotation, rotation_mode=obj.rotation_mode,
        scale=list(obj.scale), dimensions=list(obj.dimensions), matrix_world=[list(row) for row in obj.matrix_world],
        parent=_name(obj.parent), children=sorted(child.name for child in obj.children),
        collections=sorted(collection.name for collection in obj.users_collection),
        modifiers=[dict(name=mod.name, type=mod.type, show_render=mod.show_render,
                        show_viewport=mod.show_viewport) for mod in obj.modifiers],
        constraints=[dict(name=item.name, type=item.type, enabled=not item.mute) for item in obj.constraints],
        materials=[_name(slot.material) for slot in obj.material_slots],
        visibility=dict(hide_viewport=obj.hide_viewport, hide_render=obj.hide_render, hide_get=obj.hide_get()),
    ))


FILE_SUMMARIES = {
    "datablocks": datablocks,
    "missing_files": missing_files,
    "of_linked_libraries": linked_libraries,
    "path_info": path_info,
    "usage_guess": usage_guess,
}


def inspect(bpy, operation, parameters):
    if operation in FILE_SUMMARIES:
        return success(FILE_SUMMARIES[operation](bpy))
    if operation == "objects":
        return collection_summary(bpy)
    if operation == "object" and isinstance(parameters.get("name"), str):
        return object_summary(bpy, parameters["name"])
    return failure("bad_request", "Unknown inspection query or invalid arguments.")
