"""Blender-side operations. The caller owns the Blender main thread."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import traceback

from .inspection import inspect
from .viewport import dispatch as window_operation
from .wire import MAX_CODE, MAX_MESSAGE, VERSION, InvalidMessage, encode, failure, success, valid_timeout

OUTPUT_LIMIT = 8192


class Output(StringIO):
    def write(self, value):
        remaining = OUTPUT_LIMIT - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        return len(value)


def scene_state(bpy, session_id=None):
    scene = bpy.context.scene
    return {
        "protocol_version": VERSION,
        "session_id": session_id,
        "blender_version": bpy.app.version_string,
        "python_version": sys.version.split()[0],
        "background": bpy.app.background,
        "file": bpy.data.filepath or None,
        "scene": scene.name if scene else None,
    }


def run_python(bpy, code):
    if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE:
        return failure("invalid_code", "Provide a non-empty script within the code size limit.")

    out, err = Output(), Output()
    scope = {"__name__": "__main__", "bpy": bpy}
    try:
        compiled = compile(code, "<stereograph>", "exec")
    except (SyntaxError, ValueError) as exc:
        return failure("python_syntax_error", str(exc)[:1000])

    try:
        with redirect_stdout(out), redirect_stderr(err):
            exec(compiled, scope)
    except (Exception, SystemExit, KeyboardInterrupt):
        outcome = failure("python_error", traceback.format_exc(limit=8)[-6000:], "may_have_changed")
    else:
        outcome = success(scope.get("result"))
    outcome.update(stdout=out.getvalue(), stderr=err.getvalue())
    return bounded_result(outcome)


def bounded_result(outcome):
    try:
        size = len(encode(outcome))
    except InvalidMessage:
        return failure("invalid_result", "Assign finite JSON-compatible data to result.", "completed")
    if size > MAX_MESSAGE:
        return failure("result_too_large", "Return a smaller result.", "completed")
    return outcome


POSE_OPERATIONS = {"pose_preview", "pose_apply", "pose_cancel"}
REVIEW_OPERATIONS = {"motion_review", "motion_jump"}
RIG_OPERATIONS = {"rig_inspect", "knee_preview", "knee_apply", "knee_cancel"}


def dispatch(bpy, request, session_id, allow_python):
    version = request.get("version")
    if type(version) is not int or version != VERSION:
        return failure("protocol_mismatch", f"This bridge supports protocol {VERSION}.")
    operation = request.get("operation")
    if operation == "health":
        return success({**scene_state(bpy, session_id), "python_enabled": allow_python})
    if operation in POSE_OPERATIONS:
        return bounded_result(run_pose(request, session_id))
    if operation in REVIEW_OPERATIONS:
        return bounded_result(run_review(bpy, request, session_id))
    if operation in RIG_OPERATIONS:
        return bounded_result(run_rig(bpy, request, session_id))
    if operation in {"inspect", "viewport", "spatial"}:
        return bounded_result(run_native(bpy, request))
    if operation != "execute":
        return failure("unknown_operation", "Unknown Blender operation.")
    if not allow_python:
        return failure("python_disabled", "Python execution is disabled for this Blender session.")
    if not session_id or request.get("expected_session") != session_id:
        return failure("stale_session", "Read health again and confirm the intended Blender file.")
    return run_python(bpy, request.get("code"))


def run_pose(request, session_id=None):
    parameters = request.get("parameters")
    if not isinstance(parameters, dict) or not valid_timeout(parameters.get("seconds")):
        return failure("bad_pose_request", "Invalid pose parameters or timeout.")
    from .posing.dispatch import dispatch as pose_dispatch

    return pose_dispatch(request["operation"], parameters, session_id, parameters["seconds"])


def run_rig(bpy, request, session_id=None):
    arguments = request.get("parameters")
    if not isinstance(arguments, dict) or not valid_timeout(arguments.get("seconds"), request.get("operation")):
        return failure("bad_rig_request", "Invalid rig parameters or timeout.")
    from .rigging.dispatch import dispatch as rig_dispatch

    return rig_dispatch(bpy, request["operation"], arguments, session_id, arguments["seconds"])


def run_review(bpy, request, session_id=None):
    parameters = request.get("parameters")
    if not isinstance(parameters, dict) or not valid_timeout(parameters.get("seconds")):
        return failure("bad_motion_profile", "Invalid motion review parameters or timeout.")
    from .review.native import inspect_motion, jump

    if request["operation"] == "motion_jump":
        return jump(bpy, parameters, session_id)
    return inspect_motion(bpy, parameters, parameters["seconds"], session_id)


def run_native(bpy, request):
    operation = request.get("operation")
    parameters = request.get("parameters", {})
    if operation == "spatial":
        if not isinstance(parameters, dict) or not valid_timeout(parameters.get("seconds")):
            return failure("bad_spatial_request", "Invalid spatial parameters or timeout.")
        from .spatial.query import inspect as spatial_inspect

        return spatial_inspect(bpy, parameters, parameters["seconds"])
    query = request.get("query")
    if not isinstance(query, str) or not isinstance(parameters, dict):
        return failure("bad_request", "Invalid native request.")
    if request["operation"] == "viewport":
        return window_operation(bpy, query, parameters)
    return inspect(bpy, query, parameters)


def headless_request(bpy, request):
    target = request.get("file")
    if not isinstance(target, str) or not bpy.data.filepath or Path(bpy.data.filepath).resolve() != Path(target):
        return failure("wrong_file", "Background Blender did not open the requested file.")
    operation = request.get("operation")
    if operation in POSE_OPERATIONS:
        outcome = run_pose(request)
    elif operation in REVIEW_OPERATIONS:
        outcome = run_review(bpy, request)
    elif operation in RIG_OPERATIONS:
        outcome = run_rig(bpy, request)
    elif operation in {"inspect", "viewport", "spatial"}:
        outcome = run_native(bpy, request)
    else:
        outcome = run_python(bpy, request.get("code"))
    return bounded_result({"state": scene_state(bpy), **outcome})
