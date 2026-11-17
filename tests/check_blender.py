"""Exercise installed Stereograph components with disposable native Blender sessions."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from stereograph_bridge import wire

ROOT = Path(__file__).resolve().parents[1]
CREATE_FIXTURE = """
import bpy
import sys

bpy.context.scene.name = 'StereographFixture'
bpy.context.scene['fixture_value'] = 10
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
"""
INSTALL = """
import bpy
import sys

result = bpy.ops.extensions.package_install_files(
    filepath=sys.argv[-1], repo='user_default', enable_on_install=True
)
assert 'FINISHED' in result, result
assert 'bl_ext.user_default.stereograph_bridge' in bpy.context.preferences.addons
bpy.ops.wm.save_userpref()
print('Stereograph extension installed', flush=True)
"""


async def call(session, name, **arguments):
    response = await session.call_tool(name, arguments)
    assert not response.isError, response.content
    assert response.structuredContent is not None, response
    return response.structuredContent


def error(response, code, execution=None):
    assert not response["ok"] and response["error"]["code"] == code, response
    if execution:
        assert response["error"]["execution"] == execution, response


async def wait_for_live(session, process, log_path, previous_id=None):
    async with asyncio.timeout(60):
        while process.poll() is None:
            response = await call(session, "blender_health", timeout=1)
            if response["ok"] and response["data"]["session_id"] != previous_id:
                return response["data"]
            await asyncio.sleep(0.25)
    raise RuntimeError(log_path.read_text(errors="replace")[-4000:])


def check_wire(port, token):
    for request, expected in (
        ({"version": 1, "operation": "health", "token": secrets.token_urlsafe(32), "timeout": 2}, "unauthorized"),
        ({"version": 2, "operation": "health", "token": token, "timeout": 2}, "protocol_mismatch"),
    ):
        with socket.create_connection((wire.HOST, port), timeout=5) as peer:
            wire.send(peer, request)
            error(wire.receive(peer), expected, "not_started")
    with socket.create_connection((wire.HOST, port), timeout=5) as peer:
        peer.sendall(wire.HEADER.pack(1) + b"{")
        error(wire.receive(peer), "bad_request", "not_started")
    with socket.create_connection((wire.HOST, port), timeout=5) as peer:
        peer.sendall(wire.HEADER.pack(wire.MAX_MESSAGE + 1))
        error(wire.receive(peer), "bad_request", "not_started")


async def check_background(session, fixture):
    async def execute(code, file=fixture, timeout=30):
        return await call(session, "execute_blender_code_for_cli", blend_file=str(file), code=code, timeout=timeout)

    error(await execute("pass", fixture.with_name("absent.blend")), "file_not_found", "not_started")
    outcome = await execute(
        "import os, threading\n"
        "bpy.context.scene['fixture_value'] += 2\n"
        "print('background stdout')\n"
        "result = [bpy.context.scene['fixture_value'], threading.current_thread() is threading.main_thread(), "
        "'STEREOGRAPH_TOKEN' in os.environ, bpy.app.autoexec_fail]"
    )
    assert outcome["data"] == [12, True, False, False], outcome
    assert outcome["state"]["background"] and outcome["state"]["file"] == str(fixture), outcome
    assert outcome["stdout"] == "background stdout\n", outcome
    error(await execute("raise ValueError('fixture failure')"), "python_error", "may_have_changed")
    error(await execute("import time; time.sleep(30)", timeout=2), "timeout", "unknown")
    recovery = await execute("result = bpy.context.scene['fixture_value']")
    assert recovery["data"] == 10, recovery
    print("Background Blender:", json.dumps(outcome["state"]), flush=True)


async def check_live(session, process, log_path, fixture, port, token):
    health = await wait_for_live(session, process, log_path)
    assert not health["background"] and health["file"] == str(fixture) and health["python_enabled"], health
    assert health["protocol_version"] == 1 and health["scene"] == "StereographFixture", health
    await asyncio.to_thread(check_wire, port, token)
    session_id = health["session_id"]

    async def execute(code, expected_session=session_id, timeout=10):
        return await call(session, "execute_blender_code", code=code, expected_session=expected_session, timeout=timeout)

    error(await execute("raise AssertionError", "stale"), "stale_session", "not_started")
    error(await execute("result ="), "python_syntax_error", "not_started")
    error(await execute("result = object()"), "invalid_result", "completed")
    result = await execute(
        "import threading\n"
        "bpy.context.scene['fixture_value'] += 1\n"
        "print('live stdout')\n"
        "result = [bpy.context.scene['fixture_value'], threading.current_thread() is threading.main_thread()]"
    )
    assert result["data"] == [11, True] and result["stdout"] == "live stdout\n", result
    error(await execute("raise ValueError('fixture failure')"), "python_error", "may_have_changed")
    error(await execute("import time; time.sleep(.45)", timeout=.25), "timeout", "unknown")
    result = await execute("result = bpy.context.scene['fixture_value']")
    assert result["data"] == 11, result
    await execute(f"bpy.ops.wm.open_mainfile(filepath={str(fixture)!r}, load_ui=False, use_scripts=False)")
    reloaded = await wait_for_live(session, process, log_path, previous_id=session_id)
    error(await execute("raise AssertionError"), "stale_session", "not_started")
    result = await execute("result = bpy.context.scene['fixture_value']", reloaded["session_id"])
    assert result["data"] == 10 and reloaded["file"] == str(fixture), (result, reloaded)
    print("Live Blender:", json.dumps({**health, "session_id": "<verified>"}), flush=True)


def run_blender(arguments, environment, log):
    with log.open("wb") as output:
        completed = subprocess.run(arguments, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                   timeout=60)
    assert completed.returncode == 0, log.read_text(errors="replace")[-4000:]


async def verify(args, directory):
    fixture = directory / "disposable scene.blend"
    creator = directory / "create_fixture.py"
    creator.write_text(CREATE_FIXTURE)
    env = dict(os.environ)
    for name in ("CONFIG", "EXTENSIONS", "SCRIPTS", "AUTOSAVE"):
        folder = directory / name.lower()
        folder.mkdir()
        env[f"BLENDER_USER_{name}"] = str(folder)
    port = None
    with socket.socket() as peer:
        peer.bind((wire.HOST, 0))
        port = peer.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env.update(STEREOGRAPH_PORT=str(port), STEREOGRAPH_TOKEN=token, STEREOGRAPH_ALLOW_PYTHON="1",
               STEREOGRAPH_BLENDER=args.blender)

    blender = [args.blender, "--factory-startup", "--disable-autoexec", "--offline-mode"]
    background = [*blender, "--background"]
    run_blender([*background, "--python-exit-code", "3", "--python", str(creator), "--", str(fixture)],
                env, directory / "fixture.log")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    server = StdioServerParameters(command=args.server, env=env)
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} >= {
                "blender_health", "execute_blender_code", "execute_blender_code_for_cli"
            }, tools
            error(await call(session, "blender_health"), "blender_unavailable", "not_started")
            await check_background(session, fixture)
            if args.live:
                archive = args.addon
                if archive is None:
                    archive = directory / "stereograph_bridge.zip"
                    run_blender([*background, "--command", "extension", "build",
                                 "--source-dir", str(ROOT / "src/stereograph_bridge"),
                                 "--output-filepath", str(archive)], env, directory / "build.log")
                install = directory / "install.py"
                install.write_text(INSTALL)
                run_blender([*background, "--python-exit-code", "3",
                             "--python", str(install), "--", str(archive)], env, directory / "install.log")
                log_path = directory / "gui.log"
                with log_path.open("wb") as output:
                    process = subprocess.Popen(
                        [args.blender, "--disable-autoexec", "--offline-mode", "--no-window-focus", str(fixture)],
                        env=env, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    )
                try:
                    await check_live(session, process, log_path, fixture, port, token)
                except BaseException:
                    print(log_path.read_text(errors="replace")[-4000:], file=sys.stderr)
                    raise
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                error(await call(session, "blender_health"), "blender_unavailable", "not_started")
    assert digest == hashlib.sha256(fixture.read_bytes()).hexdigest(), "Disposable file was saved unexpectedly"
    print("PASS: Blender checks completed and disposable file was unchanged.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also start and close a temporary Blender window")
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--server", default=str(Path(sys.executable).with_name("stereograph")))
    parser.add_argument("--addon", type=lambda path: Path(path).resolve(),
                        help="Install this prebuilt Blender extension ZIP during the live check")
    args = parser.parse_args()
    if args.addon is not None and (not args.live or not args.addon.is_file()):
        parser.error("--addon requires --live and an existing extension archive")
    if not args.blender:
        parser.error("Blender executable not found")
    with TemporaryDirectory(prefix="stereograph-test-") as scratch:
        asyncio.run(verify(args, Path(scratch)))
