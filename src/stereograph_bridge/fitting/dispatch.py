"""Native clothing review and authenticated, single-use corrective previews."""

from collections import OrderedDict
from time import monotonic
from uuid import uuid4

from ..posing.dispatch import _apply_native
from ..posing.profile import PoseProblem
from ..spatial.distance import InspectionLimit
from ..spatial.query import InvalidScene
from ..wire import failure, success
from . import corrective
from .inspect import inspect_fit

_previews = OrderedDict()


def unregister():
    _previews.clear()


def dispatch(bpy, operation, arguments, session, seconds):
    if bpy.context.scene is None or bpy.context.view_layer is None or bpy.context.mode != "OBJECT":
        return failure("object_mode_required", "Open a scene and view layer in Object Mode.")
    if bpy.app.autoexec_fail:
        return failure("drivers_not_evaluated", "Blender blocked authored drivers. Inspect a trusted evaluated session.")
    deadline = monotonic() + max(.01, seconds - .5)
    if operation != "fit_inspect":
        if session is not None and arguments.get("expected_session") != session:
            return failure("stale_session", "Use the current live Blender session ID.")
        if session is None and operation != "cloth_preview":
            return failure("live_session_required", "Apply or cancel in the live session that staged the preview.")
    try:
        if operation == "fit_inspect":
            return success(inspect_fit(bpy, arguments, deadline))
        if operation == "cloth_preview":
            report, candidate = corrective.preview(bpy, arguments, deadline)
            if candidate is not None and session is not None:
                token = uuid4().hex
                _previews[token] = (session, candidate)
                while len(_previews) > 8:
                    _previews.popitem(last=False)
                report["candidate_id"] = token
            return success(report)
        token = arguments.get("candidate_id")
        match = _previews.get(token) if isinstance(token, str) else None
        if not match or match[0] != session:
            return failure("cloth_preview_unavailable", "Preview the corrective again in this live session.")
        _previews.pop(token)
        if operation == "cloth_cancel":
            return success({"cancelled": True})
        return _apply_native(match[1], deadline, commit=corrective.apply)
    except (InvalidScene, PoseProblem) as exc:
        return failure(exc.code, str(exc))
    except InspectionLimit as exc:
        return failure("fit_deadline", str(exc), "completed")
    except (ValueError, TypeError, OverflowError) as exc:
        return failure("bad_fit_request", str(exc))
