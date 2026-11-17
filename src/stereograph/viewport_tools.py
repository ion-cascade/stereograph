"""MCP declarations for native Blender window and image operations."""

import json

from mcp.types import CallToolResult, ImageContent, TextContent

from .inspection_tools import READ_ONLY

NAVIGATION = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
RENDER = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}


def _as_png(reply):
    if not reply.get("ok"):
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(reply))],
                              structuredContent=reply, isError=True)
    image = ImageContent(type="image", mimeType="image/png", data=reply["data"]["image_base64"])
    return CallToolResult(content=[image])


def register_viewport_tools(mcp, live, background):
    def window_tool(query, timeout=15, **parameters):
        return live("viewport", timeout, query=query, parameters=parameters)

    @mcp.tool(annotations=READ_ONLY)
    def get_screenshot_of_window_as_json() -> dict[str, object]:
        """Describe the active Blender window, editor layout and selection."""
        return window_tool("layout")

    @mcp.tool(annotations=READ_ONLY)
    def get_screenshot_of_window_as_image(size_limit_in_bytes: int = 0) -> CallToolResult:
        """Capture the active Blender window as a PNG, with an optional byte limit."""
        return _as_png(window_tool("screenshot", size_limit_in_bytes=size_limit_in_bytes))

    @mcp.tool(annotations=READ_ONLY)
    def get_screenshot_of_area_as_image(area_ui_type: str, size_limit_in_bytes: int = 0) -> CallToolResult:
        """Capture the largest editor of the requested Blender area UI type as a PNG."""
        return _as_png(window_tool("screenshot", area_ui_type=area_ui_type, size_limit_in_bytes=size_limit_in_bytes))

    @mcp.tool(annotations=NAVIGATION)
    def jump_to_tab_by_name(name: str) -> dict[str, object]:
        """Activate the workspace with the given name."""
        return window_tool("workspace", name=name)

    @mcp.tool(annotations=NAVIGATION)
    def jump_to_tab_by_space_type(space_type: str, allow_edits: bool = False) -> dict[str, object]:
        """Activate a workspace with the requested main editor, creating one if allowed."""
        return window_tool("space", space_type=space_type, allow_edits=allow_edits)

    @mcp.tool(annotations=NAVIGATION)
    def jump_to_view3d_object_by_name(name: str, allow_edits: bool = False) -> dict[str, object]:
        """Frame the named object in the current 3D viewport, revealing it if allowed."""
        return window_tool("focus_object", name=name, allow_edits=allow_edits)

    @mcp.tool(annotations=NAVIGATION)
    def jump_to_view3d_object_data_by_name(name: str, allow_edits: bool = False) -> dict[str, object]:
        """Frame the object that uses a datablock with the given name."""
        return window_tool("focus_data", name=name, allow_edits=allow_edits)

    def render(query, output_path, blend_file, timeout):
        if blend_file is None:
            return window_tool(query, timeout, output_path=output_path)
        return background(blend_file, timeout, operation="viewport", query=query,
                          parameters={"output_path": output_path})

    @mcp.tool(annotations=RENDER)
    def render_thumbnail_to_path(output_path: str, blend_file: str | None = None,
                                 timeout: float = 120) -> dict[str, object]:
        """Render a small image from the active camera to output_path."""
        return render("thumbnail", output_path, blend_file, timeout)

    @mcp.tool(annotations=RENDER)
    def render_viewport_to_path(output_path: str, blend_file: str | None = None,
                                timeout: float = 120) -> dict[str, object]:
        """Render from the active camera to output_path using the scene's render settings."""
        return render("render", output_path, blend_file, timeout)
