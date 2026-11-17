"""Window state, navigation and captures in Blender's active window."""

import base64
from pathlib import Path
from contextlib import ExitStack
from tempfile import TemporaryDirectory

from .wire import MAX_MESSAGE, failure, success

IMAGE_LIMIT = (MAX_MESSAGE - 4096) * 3 // 4
PNG_END = bytes.fromhex("0000000049454e44ae426082")


def _largest(items):
    return max(items, key=lambda item: item.width * item.height, default=None)


def _bounds(item):
    return dict(type=item.type, x=item.x, y=item.y, width=item.width, height=item.height)


def layout(bpy, window):
    areas = []
    for area in window.screen.areas:
        space = area.spaces.active
        details = dict(type=space.type)
        if area.type == "VIEW_3D":
            region = space.region_3d
            details.update(view_perspective=region.view_perspective, view_location=list(region.view_location),
                           shading_type=space.shading.type, show_overlays=space.overlay.show_overlays)
        for attribute in ("context", "display_mode", "tree_type"):
            if hasattr(space, attribute):
                details[attribute] = getattr(space, attribute)
        for attribute in ("text", "node_tree"):
            if value := getattr(space, attribute, None):
                details[f"{attribute}_name"] = value.name
        areas.append(dict(_bounds(area), ui_type=area.ui_type, space=details,
                          regions=[_bounds(region) for region in area.regions if region.width and region.height]))
    active = bpy.context.active_object
    return success(dict(window_width=window.width, window_height=window.height,
                        screen_name=window.screen.name, workspace=window.workspace.name, scene=window.scene.name,
                        areas=areas, active_object=dict(name=active.name, type=active.type) if active else None,
                        selected_objects=[dict(name=obj.name, type=obj.type) for obj in bpy.context.selected_objects]))


def switch_workspace(bpy, window, name):
    workspace = bpy.data.workspaces.get(name)
    if workspace is None:
        return failure("workspace_not_found", f"No workspace named {name!r}.")
    window.workspace = workspace
    return success(dict(workspace=workspace.name))


def switch_space(bpy, window, space_type, allow_edits):
    allowed = bpy.types.Area.bl_rna.properties["type"].enum_items.keys()
    if space_type not in allowed or space_type in {"EMPTY", "TOPBAR", "STATUSBAR"}:
        return failure("unknown_space_type", f"Unknown editor type {space_type!r}.")
    workspaces = [window.workspace, *(item for item in bpy.data.workspaces if item != window.workspace)]
    for workspace in workspaces:
        for screen in workspace.screens:
            area = _largest(area for area in screen.areas if area.type not in {"TOPBAR", "STATUSBAR"})
            if area is not None and area.type == space_type:
                window.workspace = workspace
                return success(dict(workspace=workspace.name, space_type=space_type, created=False))
    if not allow_edits:
        return failure("workspace_not_found", f"No workspace has a main editor of type {space_type!r}.")
    before = set(bpy.data.workspaces)
    if "FINISHED" not in bpy.ops.workspace.duplicate():
        return failure("workspace_creation_failed", "Blender could not duplicate the workspace.", "unknown")
    new = next((item for item in bpy.data.workspaces if item not in before), None)
    if new is None:
        return failure("workspace_creation_failed", "Blender did not return a new workspace.", "unknown")
    new.name = space_type.replace("_", " ").title()
    area = _largest(area for screen in new.screens for area in screen.areas
                    if area.type not in {"TOPBAR", "STATUSBAR"})
    if area is None:
        return failure("area_not_found", "The duplicated workspace has no editor.", "may_have_changed")
    area.type = space_type
    window.workspace = new
    return success(dict(workspace=new.name, space_type=space_type, created=True))


def _reveal(layer_collection, obj):
    # Prefer the first usable collection path and open just that branch.
    if obj in layer_collection.collection.objects.values():
        found = True
    else:
        found = any(_reveal(child, obj) for child in layer_collection.children)
    if found:
        layer_collection.exclude = False
        layer_collection.hide_viewport = False
        layer_collection.collection.hide_viewport = False
    return found


def focus_object(bpy, window, name, allow_edits, by_data=False):
    layer = bpy.context.view_layer
    if by_data:
        obj = next((item for item in window.scene.objects if item.data and item.data.name == name), None)
    else:
        obj = window.scene.objects.get(name)
    if obj is None:
        return failure("object_not_found", f"No scene object matches {name!r}.")
    area = _largest(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = next((region for region in area.regions if region.type == "WINDOW"), None) if area else None
    if region is None:
        return failure("area_not_found", "The active workspace has no 3D viewport.")
    if bpy.context.mode != "OBJECT":
        return failure("wrong_object_mode", "Switch to Object Mode before framing another object.")
    if allow_edits and not obj.visible_get(view_layer=layer):
        _reveal(layer.layer_collection, obj)
        obj.hide_viewport = False
        obj.hide_set(False, view_layer=layer)
    if not obj.visible_get(view_layer=layer) or obj.hide_select:
        return failure("object_not_selectable", f"{obj.name!r} is hidden or selection is disabled.")
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    layer.objects.active = obj
    if area.spaces.active.region_3d.view_perspective == "CAMERA":
        area.spaces.active.region_3d.view_perspective = "PERSP"
    with bpy.context.temp_override(window=window, area=area, region=region):
        if "FINISHED" not in bpy.ops.view3d.view_selected():
            return failure("focus_failed", "Blender could not frame the selected object.", "may_have_changed")
    return success(dict(object=obj.name, type=obj.type, data_name=obj.data.name if obj.data else None,
                        location=list(obj.matrix_world.translation)))


def _screenshot_bytes(bpy, window, area, maximum):
    import imbuf

    root = Path(bpy.app.tempdir)
    with TemporaryDirectory(prefix="stereograph-capture-", dir=root) as scratch:
        path = Path(scratch) / "capture.png"
        with bpy.context.temp_override(window=window, area=area):
            capture = bpy.ops.screen.screenshot_area if area else bpy.ops.screen.screenshot
            result = capture(filepath=str(path), check_existing=False)
        if "FINISHED" not in result or not path.is_file():
            return None
        data = path.read_bytes()
        if not data.endswith(PNG_END):
            return None
        if len(data) <= maximum:
            return data
        image = imbuf.load(str(path))
        width, height = image.size
        while max(width, height) > 1:
            width, height = max(1, width * 3 // 4), max(1, height * 3 // 4)
            image.resize((width, height), method="BILINEAR")
            imbuf.write(image, filepath=str(path))
            data = path.read_bytes()
            if len(data) <= maximum and data.endswith(PNG_END):
                return data
    return b""


def screenshot(bpy, window, size_limit_in_bytes, area_ui_type=None):
    if type(size_limit_in_bytes) is not int or size_limit_in_bytes < 0:
        return failure("invalid_size", "Provide a nonnegative PNG byte limit.")
    area = None
    if area_ui_type is not None:
        area = _largest(area for area in window.screen.areas if area.ui_type == area_ui_type)
        if area is None:
            return failure("area_not_found", f"No area of type {area_ui_type!r} in the current window.")
    maximum = min(size_limit_in_bytes or IMAGE_LIMIT, IMAGE_LIMIT)
    data = _screenshot_bytes(bpy, window, area, maximum)
    if data is None:
        return failure("capture_failed", "Blender could not produce a complete PNG.")
    if not data:
        return failure("image_too_large", "The PNG could not fit within the requested limit.")
    return success(dict(image_base64=base64.b64encode(data).decode("ascii"), byte_count=len(data)))


def _assign(settings, changes, restore):
    for attribute, value in changes.items():
        restore.callback(setattr, settings, attribute, getattr(settings, attribute))
        setattr(settings, attribute, value)


def _thumbnail_settings(scene, restore):
    settings = scene.render
    longest = max(settings.resolution_x, settings.resolution_y)
    scale = min(settings.resolution_percentage / 100, 320 / longest)
    _assign(settings, dict(
        resolution_x=max(4, int(scale * settings.resolution_x)),
        resolution_y=max(4, int(scale * settings.resolution_y)),
        resolution_percentage=100, use_simplify=True, simplify_subdivision_render=1,
    ), restore)
    if settings.engine == "CYCLES" and hasattr(scene, "cycles"):
        _assign(scene.cycles, {"samples": min(scene.cycles.samples, 16)}, restore)
    if settings.engine in {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"} and hasattr(scene, "eevee"):
        _assign(scene.eevee, {"taa_render_samples": min(scene.eevee.taa_render_samples, 16)}, restore)


def render(bpy, output_path, thumbnail):
    scene = bpy.context.scene
    if scene.camera is None:
        return failure("no_camera", "The active scene needs a camera to render.")
    if not output_path or not Path(output_path).name:
        return failure("invalid_output_path", "Provide a file path for the rendered image.")
    target = Path(bpy.path.abspath(output_path)).absolute()
    if not target.parent.is_dir() or target.is_dir():
        return failure("invalid_output_path", "The output must be a file in an existing directory.")
    settings = scene.render
    if target.suffix.lower() != settings.file_extension.lower():
        return failure("invalid_output_format", f"The scene's current format expects {settings.file_extension}.")
    try:
        with TemporaryDirectory(prefix=".stereograph-render-", dir=target.parent) as scratch, ExitStack() as restore:
            staged = Path(scratch) / target.name
            _assign(settings, dict(filepath=str(staged), use_file_extension=False), restore)
            if thumbnail:
                _thumbnail_settings(scene, restore)
            if "FINISHED" not in bpy.ops.render.render(write_still=True) or not staged.is_file():
                return failure("render_failed", "Blender did not produce an image.", "may_have_changed")
            staged.replace(target)
    except (OSError, RuntimeError, ValueError) as exc:
        return failure("render_failed", str(exc), "may_have_changed")
    return success(dict(filepath=str(target), size_bytes=target.stat().st_size))


def dispatch(bpy, query, parameters):
    if query in {"thumbnail", "render"}:
        return render(bpy, parameters["output_path"], thumbnail=query == "thumbnail")
    window = bpy.context.window
    if bpy.app.background or window is None:
        return failure("window_required", "This operation needs an interactive Blender window.")
    if query == "layout":
        return layout(bpy, window)
    if query == "workspace":
        return switch_workspace(bpy, window, **parameters)
    if query == "space":
        return switch_space(bpy, window, **parameters)
    if query in {"focus_object", "focus_data"}:
        return focus_object(bpy, window, **parameters, by_data=query == "focus_data")
    if query == "screenshot":
        return screenshot(bpy, window, **parameters)
    return failure("bad_request", "Unknown viewport operation.")
