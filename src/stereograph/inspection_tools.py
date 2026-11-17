"""Tool declarations for read-only Blender file and scene queries."""

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}

FILE_TOOLS = {
    "datablocks": "Summarize datablock counts, scene, workspace and render engine.",
    "missing_files": "Find missing external file references used by the blend file.",
    "of_linked_libraries": "List the file's direct and indirect Blender library dependencies.",
    "path_info": "Report save status, path, size, age and numbered backups for the blend file.",
    "usage_guess": "Estimate Blender workflows from evidence in the blend file.",
}


def _register_file_summary(mcp, live, background, query, description):
    name = f"get_blendfile_summary_{query}"

    @mcp.tool(name=name, description=description, annotations=READ_ONLY)
    def live_summary() -> dict[str, object]:
        return live("inspect", 15, query=query)

    @mcp.tool(name=f"{name}_for_cli", description=f"{description} Open blend_file in background Blender.",
              annotations=READ_ONLY)
    def background_summary(blend_file: str) -> dict[str, object]:
        return background(blend_file, 30, operation="inspect", query=query)


def register_inspection_tools(mcp, live, background):
    for query, description in FILE_TOOLS.items():
        _register_file_summary(mcp, live, background, query, description)

    @mcp.tool(annotations=READ_ONLY)
    def get_objects_summary() -> dict[str, object]:
        """Report the active view layer, object selection and recursive collection hierarchy."""
        return live("inspect", 15, query="objects")

    @mcp.tool(annotations=READ_ONLY)
    def get_object_detail_summary(name: str) -> dict[str, object]:
        """Inspect an object by exact name, including transforms, materials and visibility."""
        return live("inspect", 15, query="object", parameters={"name": name})
