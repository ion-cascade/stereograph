"""Measure equivalent local MCP workflows against pristine Blender Lab MCP 1.0.3."""

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from importlib.metadata import version
from pathlib import Path
import platform
import random
import secrets
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from benchmark_cases import BACKGROUND_CASES, BACKGROUND_EXECUTE, LIVE_CASES, LIVE_EXECUTE, PING, normalize
from check_blender import ROOT, run_blender
from check_parity import (TOOL_NAMES, build_addon, file_hash, free_port, install_extension,
                          launch_window, startup_environment, wait_for_port)

REFERENCE_COMMIT = "2cea8d566dde07fbac28a61d698909d69724e853"
CLIENT_DEADLINE = 0.35
SCRIPT_DELAY = 0.75


@dataclass
class Server:
    name: str
    command: str
    arguments: tuple
    environment: dict
    port: int
    store: Path

    @property
    def reference(self):
        return self.name == "blender-lab"


class Records:
    def __init__(self, directory):
        self.stream = directory / "samples.jsonl"
        self.truth = {}

    def record(self, **fields):
        with self.stream.open("a", encoding="utf-8") as target:
            target.write(json.dumps(fields, sort_keys=True) + "\n")


def reply_value(reply):
    if reply.structuredContent is not None:
        return reply.structuredContent
    message = next((part.text for part in reply.content if part.type == "text"), "")
    try:
        return json.loads(message)
    except json.JSONDecodeError:
        return {"message": message}


def failed(reply, data):
    return reply.isError or data.get("ok") is False or data.get("status") == "error"


@asynccontextmanager
async def connect(server, filename):
    with (server.store / filename).open("w", encoding="utf-8") as logs:
        parameters = StdioServerParameters(command=server.command, args=list(server.arguments),
                                           env=server.environment)
        started = perf_counter_ns()
        async with stdio_client(parameters, errlog=logs) as (receive, send):
            async with ClientSession(receive, send, read_timeout_seconds=timedelta(seconds=120)) as session:
                handshake = await session.initialize()
                yield session, perf_counter_ns() - started, handshake.serverInfo.name


@contextmanager
def display(blender, server, fixture, filename):
    started = perf_counter_ns()
    with launch_window(blender, fixture, server.environment, server.store / filename,
                       reference=server.reference) as process:
        yield process, started


def execute_args(server, session_id, code, **additional):
    return {"code": code, **({} if server.reference else {"expected_session": session_id}), **additional}


async def read_session(session, server, *, expected_scene):
    async with asyncio.timeout(30):
        while True:
            if server.reference:
                reply = await session.call_tool(LIVE_EXECUTE, {"code": PING})
                data = reply_value(reply)
                if not failed(reply, data) and normalize(data).get("scene") == expected_scene:
                    return None
            else:
                reply = await session.call_tool("blender_health", {"timeout": 2})
                data = reply_value(reply)
                if not failed(reply, data) and normalize(data).get("scene") == expected_scene:
                    current = normalize(data)["session_id"]
                    ping = await session.call_tool(LIVE_EXECUTE, execute_args(server, current, PING))
                    response = reply_value(ping)
                    if not failed(ping, response) and normalize(response).get("scene") == expected_scene:
                        return current
            await asyncio.sleep(.05)


async def sample(session, server, case, fixture, session_id):
    start = perf_counter_ns()
    try:
        reply = await session.call_tool(case.tool, case.arguments(server.reference, fixture, session_id))
    except McpError as exc:
        return dict(elapsed_ns=perf_counter_ns() - start, ok=False, error=str(exc.error.code))
    elapsed = perf_counter_ns() - start
    data = reply_value(reply)
    if failed(reply, data):
        detail = data.get("error", {})
        return dict(elapsed_ns=elapsed, ok=False, error=detail.get("code", "tool_error"))
    result = normalize(data)
    try:
        semantic_result = case.normalize(result)
    except (KeyError, TypeError, ValueError) as exc:
        return dict(elapsed_ns=elapsed, ok=False, error=f"invalid_result:{type(exc).__name__}")
    rendered_mcp_bytes = len(reply.model_dump_json(exclude_none=True).encode("utf-8"))
    return dict(elapsed_ns=elapsed, ok=True, semantic=semantic_result,
                reported_mcp_reply_bytes=rendered_mcp_bytes, work_ns=result.get("work_ns"))


async def suite(session, server, cases, fixture, session_id, records, fixture_name, round_number, repeats, rng):
    cases = list(cases)
    for case in cases:
        outcome = await sample(session, server, case, fixture, session_id)
        records.record(kind="preflight" if round_number is None else "warmup", server=server.name,
                       case=case.name, fixture=fixture_name, round=round_number, **outcome)
        key = fixture_name, case.name
        if key not in records.truth:
            assert outcome["ok"], (server.name, case.name, outcome)
            records.truth[key] = outcome["semantic"]
        assert outcome["ok"] and outcome["semantic"] == records.truth[key], (server.name, case.name, outcome)
    for iteration in range(repeats):
        rng.shuffle(cases)
        for case in cases:
            outcome = await sample(session, server, case, fixture, session_id)
            if outcome["ok"] and outcome["semantic"] != records.truth[fixture_name, case.name]:
                outcome.update(ok=False, error="semantic_mismatch")
            records.record(kind="request", server=server.name, case=case.name, fixture=fixture_name,
                           round=round_number, iteration=iteration, **outcome)


async def recovery(session, server, fixture, session_id, records, fixture_name, round_number):
    fields = dict(kind="recovery", server=server.name, fixture=fixture_name, round=round_number)
    hang = (
        "import bpy, time\n"
        "scene = bpy.context.scene\n"
        "scene['benchmark_attempts'] = scene.get('benchmark_attempts', 0) + 1\n"
        f"time.sleep({SCRIPT_DELAY})\n"
        "result = {'completed': True}"
    )
    begin = perf_counter_ns()
    timeout_error = None
    try:
        await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, hang),
                                read_timeout_seconds=timedelta(seconds=CLIENT_DEADLINE))
    except McpError as exc:
        timeout_error = exc.error.code
    timed_out = perf_counter_ns()
    check = "import bpy\nresult = {'attempts': bpy.context.scene.get('benchmark_attempts', 0)}"
    reply = await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, check))
    value = normalize(reply_value(reply))
    assert timeout_error is not None and value == {"attempts": 1}, (timeout_error, value)
    records.record(**fields, case="shared-client-timeout", elapsed_ns=timed_out - begin,
                   recovered_ns=perf_counter_ns() - begin, attempts=value["attempts"], client_error=timeout_error,
                   ok=True)
    if not server.reference:
        begin = perf_counter_ns()
        native = await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, hang, timeout=.25))
        expired = perf_counter_ns()
        response = reply_value(native)
        assert response["error"]["code"] == "timeout" and response["error"]["execution"] == "unknown", response
        reply = await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, check))
        attempts = normalize(reply_value(reply))["attempts"]
        assert attempts == 2, attempts
        records.record(**fields, case="native-live-deadline", elapsed_ns=expired - begin,
                       recovered_ns=perf_counter_ns() - begin, attempts=attempts, ok=True)

    code = f"import bpy\nbpy.ops.wm.open_mainfile(filepath={str(fixture)!r}, load_ui=False, use_scripts=False)"
    start = perf_counter_ns()
    await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, code))
    new_id = await read_session(session, server, expected_scene="StereographFixture")
    records.record(**fields, case="file-reload", elapsed_ns=perf_counter_ns() - start, ok=True)
    if not server.reference:
        assert new_id != session_id
        reply = await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, PING))
        assert reply_value(reply)["error"]["code"] == "stale_session"
        records.record(**fields, case="stale-session-rejected", ok=True)
    reply = await session.call_tool(LIVE_EXECUTE, execute_args(server, new_id, check))
    assert normalize(reply_value(reply)) == {"attempts": 0}
    return new_id


async def background_recovery(session, server, fixture, records, fixture_name, round_number):
    fields = dict(kind="recovery", server=server.name, fixture=fixture_name, round=round_number)
    code = f"import time\ntime.sleep({SCRIPT_DELAY})\nresult = {{'completed': True}}"
    args = dict(blend_file=str(fixture), code=code)
    start = perf_counter_ns()
    timeout = None
    try:
        await session.call_tool(BACKGROUND_EXECUTE, args, read_timeout_seconds=timedelta(seconds=CLIENT_DEADLINE))
    except McpError as exc:
        timeout = exc.error.code
    expired = perf_counter_ns()
    reply = await session.call_tool(BACKGROUND_EXECUTE, dict(args, code=PING))
    assert timeout is not None and normalize(reply_value(reply)) == {"scene": "StereographFixture", "background": True}
    records.record(**fields, case="shared-client-background-timeout", elapsed_ns=expired - start,
                   recovered_ns=perf_counter_ns() - start, client_error=timeout, ok=True)


async def trial(args, server, fixture, fixture_name, records, round_number, rng):
    live_repeats = args.live_repeats if round_number is not None else 0
    background_repeats = args.headless_repeats if round_number is not None else 0
    fields = dict(server=server.name, fixture=fixture_name, round=round_number)
    label = f"{fixture_name}-{round_number if round_number is not None else 'preflight'}"
    print(f"{label} / {server.name}", flush=True)
    async with connect(server, f"{label}-mcp.log") as (session, initialized_ns, _name):
        expected = TOOL_NAMES if server.reference else TOOL_NAMES | {"blender_health", "inspect_spatial_pairs", "preview_elbow_edit",
                                               "apply_elbow_edit", "cancel_elbow_edit", "review_motion_contacts",
                                               "compare_motion_reviews", "jump_to_motion_witness"}
        assert {tool.name for tool in (await session.list_tools()).tools} == expected
        if round_number is not None:
            records.record(**fields, kind="cold", case="mcp-initialize", elapsed_ns=initialized_ns, ok=True)
        with display(args.blender, server, fixture, f"{label}-blender.log") as (process, started):
            await wait_for_port(process, server.port)
            session_id = await read_session(session, server, expected_scene="StereographFixture")
            if round_number is not None:
                records.record(**fields, kind="cold", case="blender-open-and-first-execute",
                               elapsed_ns=perf_counter_ns() - started, ok=True)
            await suite(session, server, LIVE_CASES, fixture, session_id, records, fixture_name,
                        round_number, live_repeats, rng)
            if round_number in (0, 1):
                session_id = await recovery(session, server, fixture, session_id, records, fixture_name, round_number)
        if round_number in (0, 1):
            disconnected = perf_counter_ns()
            reply = await session.call_tool(LIVE_EXECUTE, execute_args(server, session_id, PING))
            assert failed(reply, reply_value(reply))
            records.record(**fields, kind="recovery", case="closed-window-rejected",
                           elapsed_ns=perf_counter_ns() - disconnected, ok=True)
            with display(args.blender, server, fixture, f"{label}-reopen.log") as (process, started):
                await wait_for_port(process, server.port)
                await read_session(session, server, expected_scene="StereographFixture")
                records.record(**fields, kind="recovery", case="window-reconnect",
                               elapsed_ns=perf_counter_ns() - started, ok=True)
        await suite(session, server, BACKGROUND_CASES, fixture, None, records, fixture_name,
                    round_number, background_repeats, rng)
        if round_number in (0, 1):
            await background_recovery(session, server, fixture, records, fixture_name, round_number)


def prepare(args, scratch, store):
    profile = startup_environment(scratch / "candidate-profile")
    candidate_path = args.addon
    if candidate_path is None:
        candidate_path = scratch / "candidate.zip"
        build_addon(args.blender, ROOT / "src/stereograph_bridge", candidate_path, profile, store / "candidate-build.log")
    port = free_port()
    profile.update(STEREOGRAPH_TOKEN=secrets.token_urlsafe(32), STEREOGRAPH_PORT=str(port),
                   STEREOGRAPH_ALLOW_PYTHON="1", STEREOGRAPH_BLENDER=args.blender)
    candidate = store / "stereograph"
    candidate.mkdir()
    install_extension(args.blender, candidate_path, profile, candidate)
    server = Server("stereograph", args.server, (), profile, port, candidate)

    ref_profile = startup_environment(scratch / "reference-profile")
    ref_port = free_port()
    ref_profile.update(PYTHONPATH=str(args.reference_source / "mcp"), BLENDER_PATH=args.blender,
                       BLENDER_MCP_HOST="127.0.0.1", BLENDER_MCP_PORT=str(ref_port))
    reference = store / "blender-lab"
    reference.mkdir()
    package = scratch / "reference.zip"
    build_addon(args.blender, args.reference_source / "addon/blender_mcp_addon", package,
                ref_profile, reference / "reference-build.log")
    install_extension(args.blender, package, ref_profile, reference, reference_port=ref_port)
    return server, Server("blender-lab", args.reference_python, ("-m", "blmcp"), ref_profile, ref_port, reference)


async def benchmark(args, scratch):
    store = args.evidence.resolve()
    store.mkdir(parents=True, exist_ok=False)
    records = Records(store)
    servers = prepare(args, scratch, store)
    fixtures = {}
    hashes = {}
    for name in ("small", "linked"):
        directory = scratch / name
        directory.mkdir()
        fixture = directory / "fixture.blend"
        extra = ["--heavy"] if name == "linked" else []
        run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
                     "--python-exit-code", "3", "--python", str(ROOT / "tests/parity_fixture.py"),
                     "--", *extra, str(fixture)], servers[0].environment, store / f"{name}-fixture.log")
        fixtures[name] = fixture
        hashes[name] = {path.name: {"sha256": file_hash(path), "size_bytes": path.stat().st_size}
                        for path in sorted(directory.iterdir()) if path.is_file()}

    versions = subprocess.check_output([args.blender, "--version"], text=True).splitlines()[:8]
    reference_sdk = subprocess.check_output(
        [args.reference_python, "-c", "from importlib.metadata import version; print(version('mcp'))"],
        text=True).strip()
    metadata = dict(blender=versions, host=platform.platform(), cpu_count=os.cpu_count(),
                    python=platform.python_version(), candidate_sdk=version("mcp"), reference_sdk=reference_sdk, reference_commit=REFERENCE_COMMIT,
                    candidate=args.candidate_label, rounds=args.rounds, live_repeats=args.live_repeats,
                    headless_repeats=args.headless_repeats, seed=args.seed, fixtures=hashes,
                    shared_client_deadline_s=CLIENT_DEADLINE, timeout_script_delay_s=SCRIPT_DELAY,
                    session_type=os.environ.get("XDG_SESSION_TYPE"))
    (store / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    rng = random.Random(args.seed)
    fixture_order = tuple(fixtures)
    for name in fixture_order:
        for server in servers:
            await trial(args, server, fixtures[name], name, records, None, rng)
    for round_number in range(args.rounds):
        ordering = servers if round_number % 2 == 0 else servers[::-1]
        for name in fixture_order if round_number % 2 == 0 else fixture_order[::-1]:
            for server in ordering:
                await trial(args, server, fixtures[name], name, records, round_number, rng)
    for name, fixture in fixtures.items():
        for filename, record in hashes[name].items():
            assert record["sha256"] == file_hash(fixture.parent / filename), filename

    print(f"Benchmark complete; all fixture hashes unchanged. Records: {store}", flush=True)


def verify_reference(source):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

    if git("rev-parse", "--show-toplevel") != str(source) or git("rev-parse", "HEAD") != REFERENCE_COMMIT:
        raise ValueError("Use a pristine Blender Lab MCP v1.0.3 Git checkout.")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("The Blender Lab reference checkout has tracked edits.")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--server", default=str(Path(sys.executable).with_name("stereograph")))
    parser.add_argument("--addon", type=Path)
    parser.add_argument("--reference-source", type=Path, required=True)
    parser.add_argument("--reference-python", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--candidate-label", default="working tree")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--live-repeats", type=int, default=10)
    parser.add_argument("--headless-repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    if not args.blender or args.rounds < 1 or args.live_repeats < 1 or args.headless_repeats < 1:
        parser.error("A Blender executable and positive round and repeat counts are required.")
    args.reference_source = args.reference_source.resolve(strict=True)
    if args.addon:
        args.addon = args.addon.resolve(strict=True)
    verify_reference(args.reference_source)
    return args


if __name__ == "__main__":
    with TemporaryDirectory(prefix="stereograph-benchmark-") as work:
        asyncio.run(benchmark(arguments(), Path(work)))
