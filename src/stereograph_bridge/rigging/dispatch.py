"""Native rig reads and authenticated one-use knee previews."""

from collections import OrderedDict
from time import monotonic
from uuid import uuid4

from ..posing.dispatch import _apply_native
from ..posing.profile import PoseProblem
from ..spatial.distance import InspectionLimit
from ..spatial.query import InvalidScene
from ..wire import failure, success
from . import knee
from .inspect import inspect

_previews = OrderedDict()


def unregister():
    _previews.clear()


def dispatch(bpy, operation, parameters, session, timeout):
    deadline = monotonic() + max(.01, timeout - .5)
    if operation == "rig_inspect":
        return inspect(bpy, parameters, deadline)
    live = session is not None
    if live and parameters.get("expected_session") != session:
        return failure("stale_session", "Use the current live Blender session ID.")
    if not live and operation != "knee_preview":
        return failure("live_session_required", "Apply or cancel in the live session that staged the preview.")
    try:
        if operation == "knee_preview":
            result, candidate = knee.preview(bpy, parameters, deadline, live)
            if candidate is not None:
                token = uuid4().hex
                _previews[token] = (session, candidate)
                while len(_previews) > 8:
                    _previews.popitem(last=False)
                result["candidate_id"] = token
            return success(result)
        token = parameters.get("candidate_id")
        match = _previews.get(token) if isinstance(token, str) else None
        if not match or match[0] != session:
            return failure("knee_preview_unavailable", "Preview the knee again in this live session.")
        _previews.pop(token)
        if operation == "knee_cancel":
            return success({"cancelled": True})
        return _apply_native(match[1], deadline, commit=knee.apply)
    except (PoseProblem, InvalidScene) as exc:
        return failure(exc.code, str(exc))
    except InspectionLimit as exc:
        return failure("knee_deadline", str(exc), "completed")
    except (ValueError, TypeError, OverflowError) as exc:
        return failure("bad_knee_request", str(exc))
