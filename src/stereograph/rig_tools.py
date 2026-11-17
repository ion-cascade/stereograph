"""Typed MCP access to rig inspection and planted-foot knee repair."""


def register_rig_tools(mcp, live, background):
    read = {"readOnlyHint": True, "openWorldHint": False}
    write = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

    @mcp.tool(annotations=read)
    def inspect_rig(
        profile: dict[str, object],
        frames: list[float] | None = None,
        views: list[str] | None = None,
        evidence_frame: float | None = None,
        contact_report: dict[str, object] | None = None,
        blend_file: str | None = None,
        timeout: float = 90,
    ) -> dict[str, object]:
        """Inspect resolved bone axes, controls, authored weights and sampled motion using a version 1 profile.

        Request front and side evidence at a sampled frame. Link an existing motion review report
        from the same scene for attached geometry findings. The operation restores the original frame.
        """
        arguments = dict(profile=profile, frames=frames, views=views or [], evidence_frame=evidence_frame,
                         contact_report=contact_report, seconds=timeout)
        if blend_file is not None:
            return background(blend_file, timeout, operation="rig_inspect", parameters=arguments)
        return live("rig_inspect", timeout, parameters=arguments)

    @mcp.tool(annotations=read)
    def preview_knee_repair(
        profile: dict[str, object],
        expected_session: str | None = None,
        frame: float | None = None,
        tracking_degrees: float | None = None,
        knee_target: tuple[float, float, float] | None = None,
        views: list[str] | None = None,
        blend_file: str | None = None,
        timeout: float = 110,
    ) -> dict[str, object]:
        """Preview a two-bone IK pole edit with planted-foot, segment length and mesh clearance checks.

        The default target tracks the profile's forward direction. Alternatively supply a signed
        angle or world knee target. Live preview is bound to the current session and frame.
        """
        arguments = dict(profile=profile, expected_session=expected_session, frame=frame,
                         tracking_degrees=tracking_degrees, knee_target=list(knee_target) if knee_target else None,
                         views=views or [], seconds=timeout)
        if blend_file is not None:
            return background(blend_file, timeout, operation="knee_preview", parameters=arguments)
        return live("knee_preview", timeout, parameters=arguments)

    @mcp.tool(annotations=write)
    def apply_knee_repair(candidate_id: str, expected_session: str, timeout: float = 110) -> dict[str, object]:
        """Apply a still-current preview in an undoable Action variant after rechecking its constraints."""
        return live("knee_apply", timeout, parameters=dict(candidate_id=candidate_id,
                    expected_session=expected_session, seconds=timeout))

    @mcp.tool(annotations=write)
    def cancel_knee_repair(candidate_id: str, expected_session: str) -> dict[str, object]:
        """Discard a live knee preview."""
        return live("knee_cancel", 5, parameters=dict(candidate_id=candidate_id,
                    expected_session=expected_session, seconds=5))
