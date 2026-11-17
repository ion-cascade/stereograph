"""MCP schema for read-only spatial inspection."""


def register_spatial_tools(mcp, live, background):
    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def inspect_spatial_pairs(
        pairs: list[tuple[str, str]],
        frames: list[float] | None = None,
        frame_range: tuple[float, float, float] | None = None,
        tolerance: float = .001,
        views: list[str] | None = None,
        evidence_frame: float | None = None,
        blend_file: str | None = None,
        timeout: float = 90,
    ) -> dict[str, object]:
        """Measure evaluated mesh pairs in world space, optionally over frames and with witness diagrams.

        Use current-frame live inspection by default or open a specified blend file in background
        Blender. Supply frames or frame_range (start, end, step) for subframes. Distances and
        tolerance are in Blender units. Request front, side, top or isometric SVG evidence at
        one sampled frame. The query restores the original frame.
        """
        parameters = dict(
            pairs=[list(pair) for pair in pairs], frames=frames,
            frame_range=list(frame_range) if frame_range is not None else None,
            tolerance=tolerance, views=views or [], evidence_frame=evidence_frame, seconds=timeout,
        )
        if blend_file is not None:
            return background(blend_file, timeout, operation="spatial", parameters=parameters)
        return live("spatial", timeout, parameters=parameters)
