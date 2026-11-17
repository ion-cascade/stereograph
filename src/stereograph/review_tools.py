"""MCP entry points for contact review, comparison, and witness navigation."""

from stereograph_bridge.review.results import compare
from stereograph_bridge.wire import failure, success


def register_review_tools(mcp, live, background):
    read_only = {"readOnlyHint": True, "openWorldHint": False}

    @mcp.tool(annotations=read_only)
    def review_motion_contacts(
        profile: dict[str, object],
        views: list[str] | None = None,
        evidence_rule: str | None = None,
        extra_frames: list[float] | None = None,
        match_evidence: list[dict[str, object]] | None = None,
        blend_file: str | None = None,
        timeout: float = 105,
    ) -> dict[str, object]:
        """Check phase-specific contact rules across an animation and restore the starting frame.

        Supply a version 1 profile with named surfaces and rule intervals. Optional extra_frames
        replay sample times from a previous review. Request two views for matched evidence at
        representative samples. Use match_evidence to reuse prior frames and framing. Use blend_file for an isolated background review.
        """
        parameters = dict(profile=profile, views=views or [], evidence_rule=evidence_rule,
                          extra_frames=extra_frames or [], match_evidence=match_evidence or [], seconds=timeout)
        if blend_file is not None:
            return background(blend_file, timeout, operation="motion_review", parameters=parameters)
        return live("motion_review", timeout, parameters=parameters)

    @mcp.tool(annotations=read_only)
    def compare_motion_reviews(
        before: dict[str, object], after: dict[str, object],
    ) -> dict[str, object]:
        """Compare two motion review data reports from the same profile.

        The delivered comparison counts only matching rule and frame samples as verified.
        Replay missing sample times with extra_frames. Drop evidence from the input if needed.
        """
        try:
            return success(compare(before, after))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            return failure("incomparable_motion_reviews", str(exc))

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                           "idempotentHint": True, "openWorldHint": False})
    def jump_to_motion_witness(bookmark: dict[str, object], view: str = "front") -> dict[str, object]:
        """Jump to an evidence bookmark from the current live Blender scene and frame the witness."""
        return live("motion_jump", 15, parameters=dict(bookmark=bookmark, view=view, seconds=15))
