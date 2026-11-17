"""MCP schemas for the planted-hand elbow workflow."""

POSE_READ = {"readOnlyHint": True, "openWorldHint": False}
POSE_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}


def register_pose_tools(mcp, live, background):
    @mcp.tool(annotations=POSE_READ)
    def preview_elbow_edit(
        profile: dict[str, object],
        expected_session: str | None = None,
        frame: float | None = None,
        swivel_degrees: float | None = None,
        elbow_target: tuple[float, float, float] | None = None,
        views: list[str] | None = None,
        blend_file: str | None = None,
        timeout: float = 110,
    ) -> dict[str, object]:
        """Preview an FK elbow move while preserving the planted hand's pose.

        Pass a v1 rig profile and the current session ID for a live candidate. Headless previews
        are available with blend_file. Supply a signed swivel or world elbow target, or omit
        both to improve wrist alignment within the profile's limit. The preview stages the
        intended keys and checks the surrounding motion before restoring the original Action.
        """
        parameters = dict(profile=profile, expected_session=expected_session, frame=frame,
                          swivel_degrees=swivel_degrees, elbow_target=list(elbow_target) if elbow_target else None,
                          views=views or [], seconds=timeout)
        if blend_file is not None:
            return background(blend_file, timeout, operation="pose_preview", parameters=parameters)
        return live("pose_preview", timeout, parameters=parameters)

    @mcp.tool(annotations=POSE_WRITE)
    def apply_elbow_edit(candidate_id: str, expected_session: str, timeout: float = 110) -> dict[str, object]:
        """Apply a verified live preview as an undoable Action variant at its original frame."""
        return live("pose_apply", timeout, parameters=dict(candidate_id=candidate_id,
                    expected_session=expected_session, seconds=timeout))

    @mcp.tool(annotations=POSE_WRITE)
    def cancel_elbow_edit(candidate_id: str, expected_session: str) -> dict[str, object]:
        """Discard an unused live preview without editing the pose or the Action."""
        return live("pose_cancel", 5, parameters=dict(candidate_id=candidate_id,
                    expected_session=expected_session, seconds=5))
