"""Typed MCP tools for evaluated clothing and armour fit."""


def register_fit_tools(mcp, live, background):
    read = {"readOnlyHint": True, "openWorldHint": False}
    write = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

    @mcp.tool(annotations=read)
    def inspect_clothing_fit(
        profile: dict[str, object],
        frames: list[float] | None = None,
        views: list[str] | None = None,
        blend_file: str | None = None,
        timeout: float = 120,
    ) -> dict[str, object]:
        """Review evaluated garment layers, dependency stacks, seams, attachments and local edge strain.

        Optionally include a motion contact profile. Matched front and side evidence uses the
        same projection and scale in both panels. The operation restores the original frame.
        """
        args = dict(profile=profile, frames=frames, views=views or [], seconds=timeout)
        if blend_file is not None:
            return background(blend_file, timeout, operation="fit_inspect", parameters=args)
        return live("fit_inspect", timeout, parameters=args)

    @mcp.tool(annotations=read)
    def preview_cloth_corrective(
        profile: dict[str, object],
        expected_session: str | None = None,
        views: list[str] | None = None,
        blend_file: str | None = None,
        timeout: float = 120,
    ) -> dict[str, object]:
        """Stage a local, pose-dependent displacement and check clearance, trim and deformation.

        Requires an authored mask and protected vertex groups. A feasible live preview returns
        a one-use candidate ID. A file preview returns assessment only. Neither saves the file.
        """
        args = dict(profile=profile, views=views or [], expected_session=expected_session, seconds=timeout)
        if blend_file is not None:
            return background(blend_file, timeout, operation="cloth_preview", parameters=args)
        return live("cloth_preview", timeout, parameters=args)

    @mcp.tool(annotations=write)
    def apply_cloth_corrective(candidate_id: str, expected_session: str, timeout: float = 120) -> dict[str, object]:
        """Recheck and apply a still-current live corrective with native Blender Undo."""
        return live("cloth_apply", timeout, parameters=dict(candidate_id=candidate_id,
                    expected_session=expected_session, seconds=timeout))

    @mcp.tool(annotations=write)
    def cancel_cloth_corrective(candidate_id: str, expected_session: str) -> dict[str, object]:
        """Discard a live cloth corrective preview."""
        return live("cloth_cancel", 5, parameters=dict(candidate_id=candidate_id,
                    expected_session=expected_session, seconds=5))
