"""Qualify Stereograph and optionally pristine Blender Lab on the same native fixture."""

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from check_blender import INSTALL, ROOT, run_blender

SUMMARIES = ("datablocks", "missing_files", "of_linked_libraries", "path_info", "usage_guess")
TOOL_NAMES = {
    *(f"get_blendfile_summary_{name}{suffix}" for name in SUMMARIES for suffix in ("", "_for_cli")),
    "execute_blender_code", "execute_blender_code_for_cli", "get_objects_summary", "get_object_detail_summary",
    "get_python_api_docs", "search_api_docs", "search_manual_docs", "get_screenshot_of_area_as_image",
    "get_screenshot_of_window_as_image", "get_screenshot_of_window_as_json", "jump_to_tab_by_name",
    "jump_to_tab_by_space_type", "jump_to_view3d_object_by_name", "jump_to_view3d_object_data_by_name",
    "render_thumbnail_to_path", "render_viewport_to_path",
}

REF_INSTALL = """
import bpy
import sys

assert 'FINISHED' in bpy.ops.extensions.package_install_files(
    filepath=sys.argv[-2], repo='user_default', enable_on_install=True)
prefs = bpy.context.preferences.addons['bl_ext.user_default.mcp'].preferences
prefs.host = "127.0.0.1"
prefs.port = int(sys.argv[-1])
prefs.use_autostart = True
bpy.ops.wm.save_userpref()
"""


def free_port():
    with socket.socket() as peer:
        peer.bind(("127.0.0.1", 0))
        return peer.getsockname()[1]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[-12:] == bytes.fromhex("0000000049454e44ae426082")
    return list(struct.unpack("!II", data[16:24]))


class Probe:
    def __init__(self, session, store, reference=False):
        self.session = session
        self.store = store
        self.reference = reference
        self.calls = []

    async def request(self, name, arguments=None, picture=None, expect_error=False):
        args = arguments or {}
        result = await self.session.call_tool(name, args)
        data = result.structuredContent
        if picture and not result.isError:
            images = [part for part in result.content if part.type == "image"]
            if images:
                decoded = base64.b64decode(images[0].data, validate=True)
                size = png_size(decoded)
                path = self.store / picture
                path.write_bytes(decoded)
                data = dict(size=size, bytes=len(decoded), sha256=file_hash(path))
        if data is None:
            raw = next((part.text for part in result.content if part.type == "text"), "")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"message": raw}
        record = dict(tool=name, args=args, result=data, mcp_error=result.isError)
        self.calls.append(record)
        failure = result.isError or data.get("ok") is False or data.get("status") == "error"
        if not self.reference:
            assert failure == expect_error, record
        if data.get("ok") and "data" in data:
            return data["data"]
        if self.reference and data.get("status") == "ok" and "result" in data:
            return data.get("result")
        return data

    def save(self):
        (self.store / "calls.json").write_text(json.dumps(self.calls, indent=2, default=str), encoding="utf-8")


@asynccontextmanager
async def connect(command, env, store, reference=False, args=()):
    with (store / "mcp.log").open("w", encoding="utf-8") as log:
        async with stdio_client(StdioServerParameters(command=command, args=list(args), env=env),
                                errlog=log) as (read, write):
            async with ClientSession(read, write) as session:
                handshake = await session.initialize()
                tools = (await session.list_tools()).tools
                expected = TOOL_NAMES if reference else TOOL_NAMES | {"blender_health", "inspect_spatial_pairs"}
                assert {tool.name for tool in tools} == expected
                (store / "schemas.json").write_text(json.dumps({
                    "server": handshake.serverInfo.model_dump(mode="json"),
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                }, indent=2), encoding="utf-8")
                probe = Probe(session, store, reference)
                try:
                    yield probe
                finally:
                    probe.save()


def startup_environment(root):
    root.mkdir()
    env = os.environ.copy()
    for resource in ("CONFIG", "EXTENSIONS", "SCRIPTS", "AUTOSAVE"):
        path = root / resource.lower()
        path.mkdir()
        env[f"BLENDER_USER_{resource}"] = str(path)
    return env


def install_extension(blender, archive, env, store, reference_port=None):
    script = store / "install.py"
    script.write_text(INSTALL if reference_port is None else REF_INSTALL, encoding="utf-8")
    install = [blender, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
               "--python-exit-code", "3", "--python", str(script), "--", str(archive)]
    if reference_port is not None:
        install.append(str(reference_port))
    run_blender(install, env, store / "install.log")


@contextmanager
def launch_window(blender, fixture, env, log, reference=False):
    with log.open("wb") as output:
        network_mode = "--online-mode" if reference else "--offline-mode"
        process = subprocess.Popen([blender, network_mode, "--disable-autoexec", "--no-window-focus",
                                    str(fixture)], env=env, stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
    try:
        yield process
    except BaseException:
        print(log.read_text(errors="replace")[-5000:], file=sys.stderr)
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@contextmanager
def window(blender, fixture, archive, env, store, reference_port=None):
    install_extension(blender, archive, env, store, reference_port=reference_port)
    with launch_window(blender, fixture, env, store / "window.log", reference_port is not None) as process:
        yield process


async def wait_for_port(process, port):
    async with asyncio.timeout(40):
        while process.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    return
            except OSError:
                await asyncio.sleep(.2)
    raise RuntimeError("The isolated Blender window stopped before the bridge opened.")


def check_summaries(summaries, fixture):
    assert summaries["datablocks"]["datablock_counts"]["objects"] >= 5
    assert summaries["datablocks"]["scene_name"] == "StereographFixture"
    assert any(item["path"].endswith("missing-texture.png") for item in summaries["missing_files"]["missing_files"]), summaries["missing_files"]
    assert summaries["of_linked_libraries"]["total_library_count"] == 1
    actual = summaries["path_info"]
    assert actual["filepath"] == str(fixture) and actual["file_size_bytes"] == fixture.stat().st_size
    assert actual["backups"][0]["path"] == str(fixture) + "1"
    assert summaries["usage_guess"]["usage_guesses"]["Animation"]["score"] > 0


async def headless(probe, fixture):
    summaries = {}
    for kind in SUMMARIES:
        name = f"get_blendfile_summary_{kind}_for_cli"
        summaries[kind] = await probe.request(name, {"blend_file": str(fixture)})
    check_summaries(summaries, fixture)
    code = "import bpy\nresult = {'scene': bpy.context.scene.name, 'background': bpy.app.background}"
    value = await probe.request("execute_blender_code_for_cli", dict(blend_file=str(fixture), code=code))
    assert value == {"scene": "StereographFixture", "background": True}, value
    await probe.request("get_blendfile_summary_path_info_for_cli",
                        dict(blend_file=str(fixture.with_name("absent.blend"))), expect_error=True)
    return summaries


async def docs(probe):
    entry = await probe.request("get_python_api_docs", {"identifier": "bpy.types.Object.location"})
    wildcard = await probe.request("get_python_api_docs", {"identifier": "bpy.types.*"})
    api = await probe.request("search_api_docs", {"query": "matrix_world", "max_results": 3, "context": 1})
    manual = await probe.request("search_manual_docs", {"query": "local view", "max_results": 3})
    if probe.reference:
        assert "location" in json.dumps(entry).casefold()
        assert "bpy.types.Object" in json.dumps(wildcard)
        assert "matrix_world" in json.dumps(api).casefold()
        assert "local view" in json.dumps(manual).casefold()
    else:
        assert "location" in entry["content"] and entry["version"]
        assert wildcard["total"] >= len(wildcard["identifiers"]) and wildcard["identifiers"]
        assert api["results"] and "local_view" in manual["results"][0]["path"]
    await probe.request("get_python_api_docs", {"identifier": "../unrelated"}, expect_error=True)
    for name in ("search_api_docs", "search_manual_docs"):
        if not probe.reference:
            await probe.request(name, {"query": ""}, expect_error=True)


async def live(probe, fixture):
    health = None if probe.reference else await probe.request("blender_health")
    code = "import bpy\nresult = {'scene': bpy.context.scene.name, 'background': bpy.app.background}"
    args = {"code": code}
    if health:
        args["expected_session"] = health["session_id"]
        assert health["file"] == str(fixture)
    value = await probe.request("execute_blender_code", args)
    assert value == {"scene": "StereographFixture", "background": False}, value

    summaries = {}
    for kind in SUMMARIES:
        summaries[kind] = await probe.request(f"get_blendfile_summary_{kind}")
    check_summaries(summaries, fixture)
    hierarchy = await probe.request("get_objects_summary")
    assert "Inspection Objects" in json.dumps(hierarchy) and "Inspection Cube" in json.dumps(hierarchy)
    detail = await probe.request("get_object_detail_summary", {"name": "Inspection Cube"})
    assert detail["location"] == [1, .25, 0] and detail["data_name"] == "Fixture Geometry", detail
    await probe.request("get_object_detail_summary", {"name": "Absent"}, expect_error=True)
    await probe.request("jump_to_tab_by_name", {"name": "Modeling"})
    assert await workspace_is(probe, "Modeling")
    await probe.request("jump_to_tab_by_name", {"name": "Layout"})
    assert await workspace_is(probe, "Layout")
    workspace = await probe.request("jump_to_tab_by_space_type", {"space_type": "VIEW_3D"})
    assert await workspace_is(probe, workspace["workspace"])
    await probe.request("jump_to_view3d_object_by_name", {"name": "Inspection Cube"})
    await probe.request("jump_to_view3d_object_data_by_name", {"name": "Fixture Geometry"})
    window_state = await probe.request("get_screenshot_of_window_as_json")
    assert window_state["active_object"]["name"] == "Inspection Cube", window_state
    assert any(a["type"] == "VIEW_3D" for a in window_state["areas"])
    framed = [a["space"]["view_location"] for a in window_state["areas"] if a["type"] == "VIEW_3D"]
    assert framed and any(sum((actual - expected) ** 2 for actual, expected in zip(point, (1, .25, 0))) < .1
                          for point in framed), framed
    window_args = {"size_limit_in_bytes": 600000}
    area_args = {"area_ui_type": "VIEW_3D", "size_limit_in_bytes": 300000}
    for tool, params, filename, limit in (
        ("get_screenshot_of_window_as_image", window_args, "window.png", 600000),
        ("get_screenshot_of_area_as_image", area_args, "area.png", 300000),
    ):
        shot = await probe.request(tool, params, picture=filename)
        if probe.reference and "size" not in shot:
            print(f"REFERENCE {tool} did not capture: {shot}", flush=True)
            continue
        assert all(axis > 32 for axis in shot["size"]) and shot["bytes"] <= limit, shot
    await probe.request("get_screenshot_of_area_as_image", {"area_ui_type": "PREFERENCES"}, expect_error=True)
    await probe.request("jump_to_tab_by_name", {"name": "Absent"}, expect_error=True)
    await probe.request("jump_to_view3d_object_by_name", {"name": "Hidden Marker"}, expect_error=not probe.reference)
    await probe.request("jump_to_view3d_object_by_name", {"name": "Hidden Marker", "allow_edits": True})
    assert (await probe.request("get_screenshot_of_window_as_json"))["active_object"]["name"] == "Hidden Marker"
    if not probe.reference:
        await probe.request("jump_to_view3d_object_data_by_name", {"name": "Absent Mesh"}, expect_error=True)
        await probe.request("jump_to_tab_by_space_type", {"space_type": "INVALID"}, expect_error=True)
        await probe.request("get_screenshot_of_window_as_image", {"size_limit_in_bytes": -1}, expect_error=True)
        await probe.request("render_thumbnail_to_path", {"output_path": str(fixture)}, expect_error=True)

    for tool, title in (("render_thumbnail_to_path", "thumbnail"), ("render_viewport_to_path", "render")):
        image = probe.store / f"{title}.png"
        reply = await probe.request(tool, {"output_path": str(image)})
        if probe.reference and reply.get("filepath") and Path(reply["filepath"]).is_file():
            source = Path(reply["filepath"])
            if source != image:
                shutil.copyfile(source, image)
        assert image.is_file(), reply
        dimensions = png_size(image.read_bytes())
        if title == "render":
            assert dimensions == [480, 320], dimensions
        elif probe.reference:
            # The pinned upstream live render can restore its size before this job starts.
            assert dimensions[0] in (320, 480) and dimensions[1] in (213, 320), dimensions
        else:
            assert dimensions == [320, 213], dimensions
        probe.calls[-1]["output"] = {"size": dimensions, "sha256": file_hash(image)}
    settings = "import bpy\nr = bpy.context.scene.render\nresult = {'size': r.resolution_percentage, 'path': r.filepath}"
    restored = await probe.request("execute_blender_code", dict(args, code=settings))
    assert restored == {"size": 100, "path": "//previous-render"}, restored
    if not probe.reference:
        created = await probe.request("jump_to_tab_by_space_type", {"space_type": "CONSOLE", "allow_edits": True})
        assert created["created"] and await workspace_is(probe, created["workspace"]), created
        state = await probe.request("get_screenshot_of_window_as_json")
        assert any(area["type"] == "CONSOLE" for area in state["areas"]), state
    return summaries


async def workspace_is(probe, expected):
    for _ in range(12):
        result = await probe.request("get_screenshot_of_window_as_json")
        if result["workspace"] == expected:
            return True
        await asyncio.sleep(.1)
    return False


def compare_summaries(candidate, reference):
    for name in ("objects", "meshes", "scenes", "libraries"):
        assert candidate["datablocks"]["datablock_counts"].get(name) == reference["datablocks"]["datablock_counts"].get(name)
    for kind, key in (("path_info", "file_size_bytes"), ("of_linked_libraries", "total_library_count")):
        assert candidate[kind][key] == reference[kind][key], (kind, candidate[kind], reference[kind])
    assert {item["path"] for item in candidate["missing_files"]["missing_files"]} == {
        item["path"] for item in reference["missing_files"]["missing_files"]
    }
    assert candidate["usage_guess"]["usage_guesses"].keys() == reference["usage_guess"]["usage_guesses"].keys()


def build_addon(blender, source, path, env, log):
    run_blender([blender, "--background", "--factory-startup", "--command", "extension", "build",
                 "--source-dir", str(source), "--output-filepath", str(path)], env, log)


async def verify(args, base):
    fixture = base / "parity fixture.blend"
    store = args.evidence.resolve()
    store.mkdir(parents=True, exist_ok=False)
    candidate = store / "stereograph"
    candidate.mkdir()
    env = startup_environment(base / "candidate-profile")
    env.update(STEREOGRAPH_PORT=str(free_port()), STEREOGRAPH_TOKEN=secrets.token_urlsafe(32),
               STEREOGRAPH_ALLOW_PYTHON="1", STEREOGRAPH_BLENDER=args.blender,
               STEREOGRAPH_DOCS_ROOT=str(args.docs_root), STEREOGRAPH_DOCS_VERSION=args.docs_version)
    run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
                 "--python-exit-code", "3", "--python", str(ROOT / "tests/parity_fixture.py"),
                 "--", str(fixture)], env, store / "fixture.log")
    before = file_hash(fixture)
    (store / "fixture.json").write_text(json.dumps({
        "sha256": before,
        "library_sha256": file_hash(fixture.with_name("fixture library.blend")),
        "blender": subprocess.check_output([args.blender, "--version"], text=True).splitlines()[:4],
        "reference": "Blender Lab MCP 1.0.3 (2cea8d566dde07fbac28a61d698909d69724e853)"
        if args.reference_source else None,
        "docs_version": args.docs_version,
    }, indent=2), encoding="utf-8")

    cand_live = None
    async with connect(args.server, env, candidate) as probe:
        cand_headless = await headless(probe, fixture)
        await docs(probe)
        if args.render_headless:
            for tool, title in (("render_thumbnail_to_path", "thumbnail"), ("render_viewport_to_path", "render")):
                image = candidate / f"{title}-headless.png"
                result = await probe.request(tool, {"blend_file": str(fixture), "output_path": str(image)})
                assert result["filepath"] == str(image) and png_size(image.read_bytes())[0] > 50
        if not args.headless_only:
            addon = args.addon
            if addon is None:
                addon = base / "stereograph.zip"
                build_addon(args.blender, ROOT / "src/stereograph_bridge", addon, env, candidate / "build.log")
            with window(args.blender, fixture, addon, env, candidate) as process:
                await wait_for_port(process, int(env["STEREOGRAPH_PORT"]))
                cand_live = await live(probe, fixture)
        await probe.request("blender_health", expect_error=True)

    if args.reference_source:
        reference = store / "blender-lab"
        reference.mkdir()
        ref_env = startup_environment(base / "reference-profile")
        ref_port = free_port()
        ref_env.update(PYTHONPATH=str(args.reference_source / "mcp"), BLENDER_PATH=args.blender,
                       BLENDER_MCP_HOST="127.0.0.1", BLENDER_MCP_PORT=str(ref_port))
        async with connect(args.reference_python, ref_env, reference, reference=True, args=("-m", "blmcp")) as probe:
            ref_headless = await headless(probe, fixture)
            compare_summaries(cand_headless, ref_headless)
            await docs(probe)
            if not args.headless_only:
                addon = base / "blender-lab.zip"
                build_addon(args.blender, args.reference_source / "addon/blender_mcp_addon",
                            addon, ref_env, reference / "build.log")
                with window(args.blender, fixture, addon, ref_env, reference, reference_port=ref_port) as process:
                    await wait_for_port(process, ref_port)
                    ref_live = await live(probe, fixture)
                compare_summaries(cand_live, ref_live)
    assert file_hash(fixture) == before
    print(f"PASS: fixture unchanged, qualification records in {store}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--server", default=str(Path(sys.executable).with_name("stereograph")))
    parser.add_argument("--addon", type=Path)
    parser.add_argument("--reference-source", type=Path)
    parser.add_argument("--reference-python")
    parser.add_argument("--docs-root", type=Path)
    parser.add_argument("--docs-version", default="Blender Lab MCP 1.0.3 reference snapshot")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--headless-only", action="store_true")
    parser.add_argument("--render-headless", action="store_true")
    args = parser.parse_args()
    if not args.blender:
        parser.error("Blender executable not found")
    if bool(args.reference_source) != bool(args.reference_python):
        parser.error("--reference-source and --reference-python must be supplied together")
    for key in ("reference_source", "docs_root", "addon"):
        if value := getattr(args, key):
            setattr(args, key, value.resolve(strict=True))
    if not args.docs_root and args.reference_source:
        args.docs_root = args.reference_source / "mcp/blmcp/data"
    if not args.docs_root:
        parser.error("Supply --docs-root or --reference-source")
    with TemporaryDirectory(prefix="stereograph-parity-") as scratch:
        asyncio.run(verify(args, Path(scratch)))
