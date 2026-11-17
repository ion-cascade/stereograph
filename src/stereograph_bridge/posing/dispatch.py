"""Authenticated one-use pose previews and Blender undo history."""

from collections import OrderedDict
from time import monotonic
from uuid import uuid4

import bpy

from ..spatial.distance import InspectionLimit
from ..wire import failure, success
from .preview import apply, preview
from .profile import PoseProblem

_previews = OrderedDict()


def unregister():
    _previews.clear()


def _error(exc):
    if isinstance(exc, InspectionLimit):
        return failure("pose_deadline", str(exc), "completed")
    return failure(exc.code, str(exc))


def _apply_native(candidate, deadline):
    context = bpy.context
    window = context.window
    if window is None or not context.preferences.edit.use_global_undo:
        return failure("native_undo_required", "Apply from a live Blender window with Global Undo enabled.")
    area = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
    region = next((region for region in area.regions if region.type == "WINDOW"), None) if area else None
    if region is None:
        return failure("native_undo_required", "Keep a 3D viewport open for the native undo checkpoints.")

    def checkpoint(message):
        if "FINISHED" not in bpy.ops.ed.undo_push(message=message):
            raise PoseProblem("native_undo_failed", "Blender did not record its undo checkpoint.")

    with context.temp_override(window=window, area=area, region=region):
        if not bpy.ops.ed.undo_push.poll():
            return failure("native_undo_required", "Blender undo is unavailable in the current mode.")
        return success(apply(bpy, candidate, deadline, checkpoint))


def dispatch(operation, parameters, session_id, timeout):
    live = session_id is not None
    if live and parameters.get("expected_session") != session_id:
        return failure("stale_session", "Read blender_health and use the current Blender session ID.")
    if not live and operation != "pose_preview":
        return failure("live_session_required", "Apply or cancel in the live Blender session that created the preview.")
    deadline = monotonic() + max(.01, timeout - .5)
    try:
        if operation == "pose_preview":
            result, candidate = preview(bpy, parameters, deadline, live)
            if candidate is not None:
                identifier = uuid4().hex
                _previews[identifier] = (session_id, candidate)
                while len(_previews) > 8:
                    _previews.popitem(last=False)
                result["candidate_id"] = identifier
            return success(result)
        identifier = parameters.get("candidate_id")
        found = _previews.get(identifier) if isinstance(identifier, str) else None
        if not found or found[0] != session_id:
            return failure("pose_preview_unavailable", "Preview the pose again in this Blender session.")
        _previews.pop(identifier)
        if operation == "pose_cancel":
            return success({"cancelled": True})
        return _apply_native(found[1], deadline)
    except (PoseProblem, InspectionLimit) as exc:
        return _error(exc)
