"""Model Context Protocol server for local Blender sessions."""

import argparse
from importlib.metadata import version
from importlib.resources import files
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory

from mcp.server.fastmcp import FastMCP

from stereograph_bridge import wire

from .doc_install import default_directory, install
from .documentation import register_documentation_tools
from .inspection_tools import register_inspection_tools
from .pose_tools import register_pose_tools
from .review_tools import register_review_tools
from .rig_tools import register_rig_tools
from .process import run_background
from .spatial_tools import register_spatial_tools
from .viewport_tools import register_viewport_tools

mcp = FastMCP("Stereograph")


def _live(operation, timeout, **parameters):
    if not wire.valid_timeout(timeout, operation):
        return wire.failure("bad_request", f"Timeout must be between 0.05 and {wire.timeout_limit(operation):g} seconds.")
    token = os.environ.get("STEREOGRAPH_TOKEN", "")
    if len(token) < 32:
        return wire.failure("unconfigured", "Set the same STEREOGRAPH_TOKEN for Blender and Stereograph.")
    try:
        port = wire.port_number(os.environ.get("STEREOGRAPH_PORT", wire.DEFAULT_PORT))
    except ValueError as exc:
        return wire.failure("bad_configuration", str(exc))
    outgoing = {
        "version": wire.VERSION,
        "operation": operation,
        "token": token,
        "timeout": timeout,
        **parameters,
    }
    attempted = False
    try:
        with socket.create_connection((wire.HOST, port), timeout=min(timeout, 2)) as peer:
            peer.settimeout(timeout + 1)
            attempted = True
            wire.send(peer, outgoing)
            return wire.receive(peer)
    except wire.InvalidMessage as exc:
        return wire.failure("protocol_error", str(exc), "unknown" if attempted else "not_started")
    except (OSError, EOFError) as exc:
        return wire.failure("blender_unavailable", str(exc), "unknown" if attempted else "not_started")


def _check_execution(code, timeout):
    if os.environ.get("STEREOGRAPH_ALLOW_PYTHON") != "1":
        return wire.failure("python_disabled", "Enable trusted Blender Python execution in Stereograph.")
    if not code.strip() or len(code) > wire.MAX_CODE:
        return wire.failure("invalid_code", "Provide a non-empty script within the code size limit.")
    if not wire.valid_timeout(timeout):
        return wire.failure("bad_request", "Timeout must be between 0.05 and 120 seconds.")
    return None


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def blender_health(timeout: float = 3) -> dict[str, object]:
    """Read Blender's live session ID, file, scene, version and Python availability."""
    return _live("health", timeout)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False})
def execute_blender_code(code: str, expected_session: str, timeout: float = 15) -> dict[str, object]:
    """Run trusted Python on Blender's main thread for the session confirmed by blender_health."""
    issue = _check_execution(code, timeout)
    return issue if issue is not None else _live("execute", timeout, code=code, expected_session=expected_session)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False})
def execute_blender_code_for_cli(blend_file: str, code: str, timeout: float = 30) -> dict[str, object]:
    """Run trusted Python in a fresh background Blender process on the specified blend file."""
    issue = _check_execution(code, timeout)
    if issue is not None:
        return issue
    return _background(blend_file, timeout, operation="execute", code=code)


def _background(blend_file, timeout, **request_data):
    operation = request_data.get("operation")
    if not wire.valid_timeout(timeout, operation):
        return wire.failure("bad_request", f"Timeout must be between 0.05 and {wire.timeout_limit(operation):g} seconds.")
    try:
        filename = Path(blend_file).expanduser().resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return wire.failure("file_not_found", "The requested blend file could not be resolved.")
    if filename.suffix.lower() != ".blend" or not filename.is_file():
        return wire.failure("invalid_file", "Select an existing .blend file.")

    worker = files("stereograph_bridge").joinpath("headless.py")
    blender = os.environ.get("STEREOGRAPH_BLENDER", "blender")
    with TemporaryDirectory(prefix="stereograph-") as scratch:
        workdir = Path(scratch)
        request, response, log = (workdir / name for name in ("request.json", "response.json", "blender.log"))
        request.write_bytes(wire.encode({"file": str(filename), **request_data}))
        args = [
            blender, "--background", "--factory-startup", "--disable-autoexec", str(filename),
            "--python-exit-code", "3", "--python", str(worker), "--", str(request), str(response),
        ]
        environment = {k: v for k, v in os.environ.items() if k != "STEREOGRAPH_TOKEN"}
        try:
            with log.open("wb") as output:
                returncode = run_background(args, output, environment, timeout)
        except TimeoutError:
            return wire.failure("timeout", "Background Blender was stopped at the deadline.", "unknown")
        except OSError as exc:
            return wire.failure("blender_unavailable", str(exc))
        if returncode or not response.is_file():
            with log.open("rb") as output:
                output.seek(max(0, log.stat().st_size - 3000))
                message = output.read().decode(errors="replace")
            return wire.failure("background_failed", message or "No response from Blender.", "unknown")
        try:
            with response.open("rb") as output:
                payload = output.read(wire.MAX_MESSAGE + 1)
            if len(payload) > wire.MAX_MESSAGE:
                raise wire.InvalidMessage("Background response exceeded the protocol limit")
            return wire.decode(payload)
        except (OSError, wire.InvalidMessage) as exc:
            return wire.failure("background_failed", str(exc), "unknown")


register_inspection_tools(mcp, _live, _background)
register_documentation_tools(mcp)
register_viewport_tools(mcp, _live, _background)
register_spatial_tools(mcp, _live, _background)
register_pose_tools(mcp, _live, _background)
register_review_tools(mcp, _live, _background)
register_rig_tools(mcp, _live, _background)


def main():
    parser = argparse.ArgumentParser(description="Stereograph MCP server for Blender")
    parser.add_argument("--version", action="version", version=version("ion-cascade-stereograph"))
    subcommands = parser.add_subparsers(dest="command")
    docs = subcommands.add_parser("install-docs", help="Install the pinned Blender documentation for offline lookup")
    docs.add_argument("--destination", type=Path, default=default_directory())
    args = parser.parse_args()
    if args.command == "install-docs":
        try:
            location = install(args.destination)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            parser.exit(1, f"Documentation installation failed: {exc}\n")
        print(location)
        return
    mcp.run(transport="stdio")
