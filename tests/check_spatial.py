"""Verify calibrated distances, exact witnesses and frame restoration through MCP."""

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
from math import dist, isclose
import os
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from check_blender import ROOT, call, error, run_blender, wait_for_live
from check_parity import build_addon, file_hash, free_port, png_size, startup_environment, window

CALIBRATION = {
    ("Anchor", "Known gap"): ("separated", .25),
    ("Known gap", "Anchor"): ("separated", .25),
    ("Anchor", "Exactly touching"): ("touching", 0),
    ("Anchor", "Crossing"): ("crossing", 0),
    ("Nested", "Anchor"): ("separated", .5),
    ("Nested", "Open shell"): ("separated", .5),
    ("Nested", "Self-intersecting shell"): ("separated", .3),
    ("Rotated anchor", "Rotated gap"): ("separated", .25),
    ("Anchor", "Mirrored and stretched"): ("separated", .5),
    ("Edge triangle A", "Edge triangle B"): ("separated", 1),
}


async def query(session, pairs, **options):
    return await call(session, "inspect_spatial_pairs", pairs=pairs, **options)


def check_sample(sample, relation, distance):
    assert sample["surface_relation"] == relation, sample
    assert isclose(sample["distance"], distance, abs_tol=1e-6), sample
    witness = sample["witness"]
    assert isclose(dist(witness["a"], witness["b"]), distance, abs_tol=1e-6), witness
    assert sample["surfaces_intersect"] == (relation in {"touching", "crossing"}), sample
    assert sample["transverse_crossing"] == (relation == "crossing"), sample
    assert witness["a_triangle"] >= 0 and witness["b_triangle"] >= 0, witness


def save_evidence(outcome, root):
    for image in outcome["evidence"]:
        xml = ET.fromstring(image["svg"])
        assert xml.tag == "{http://www.w3.org/2000/svg}svg"
        context = xml.find(".//{http://www.w3.org/2000/svg}image")
        prefix, message = context.attrib["{http://www.w3.org/1999/xlink}href"].split(",", 1)
        assert prefix == "data:image/png;base64" and png_size(base64.b64decode(message, validate=True)) == [600, 306]
        name = f"{image['pair'][0]}--{image['pair'][1]}--{image['frame']:g}--{image['view']}.svg"
        (root / name).write_text(image["svg"], encoding="utf-8")
    return [dict((key, value) for key, value in item.items() if key != "svg") for item in outcome["evidence"]]


async def calibration(session, store, **options):
    verified = []
    result = await query(session, list(CALIBRATION), **options)
    assert result["ok"], result
    data = result["data"]
    assert data["units"] == "blender_units" and data["unit_system"] == "METRIC"
    assert isclose(data["meters_per_unit"], .1, rel_tol=1e-6) and data["starting_frame"] == 7.25, data
    measured = data["samples"]
    for sample in measured:
        check_sample(sample, *CALIBRATION[tuple(sample["pair"])])
    assert measured[4]["containment"]["a_in_b"]["state"] == "contained", measured[4]
    assert measured[5]["containment"]["a_in_b"]["reason"] == "open_or_nonmanifold_edges", measured[5]
    assert measured[6]["containment"]["a_in_b"]["reason"] == "self_intersection", measured[6]
    assert measured[9]["witness"]["a"] == [0, 0, 0] and measured[9]["witness"]["b"] == [0, 0, 1], measured[9]
    verified.extend(measured)

    result = await query(session, [("Anchor", "Known gap")], frames=[7.25], tolerance=.26, **options)
    check_sample(result["data"]["samples"][0], "within_tolerance", .25)
    verified.extend(result["data"]["samples"])
    result = await query(session, [("Anchor", "Subframe motion")], frame_range=[10, 11, .5],
                         evidence_frame=10.5, views=["front", "side"], **options)
    frames = result["data"]["samples"]
    assert [item["frame"] for item in frames] == [10, 10.5, 11], result
    for sample, expected in zip(frames, (("separated", .5), ("crossing", 0), ("separated", .5))):
        check_sample(sample, *expected)
    assert len(result["data"]["evidence"]) == 2
    assert all(item["frame"] == 10.5 for item in result["data"]["evidence"])
    save_evidence(result["data"], store)
    verified.extend(frames)

    result = await query(session, [("Anchor", "Armature-deformed")], frames=[1, 2], **options)
    assert result["ok"], result
    for sample, expected in zip(result["data"]["samples"], (("separated", 1), ("crossing", 0))):
        check_sample(sample, *expected)
    verified.extend(result["data"]["samples"])
    result = await query(session, [("Synthetic glove", "Synthetic handle")], frames=[7.25],
                         views=["front", "side", "isometric"], **options)
    assert result["ok"], result
    check_sample(result["data"]["samples"][0], "crossing", 0)
    save_evidence(result["data"], store)
    verified.extend(result["data"]["samples"])
    error(await query(session, [("Anchor", "Absent")], **options), "object_not_evaluated")
    error(await query(session, [("Anchor", "Invalid degenerate")], frames=[11], **options), "no_surface")
    error(await query(session, [("Anchor", "Known gap")], frames=[7], frame_range=[7, 8, .5], **options),
          "bad_spatial_request")
    (store / "samples.json").write_text(json.dumps(verified, indent=2), encoding="utf-8")
    return verified


@asynccontextmanager
async def client(server, env):
    params = StdioServerParameters(command=server, env=env)
    with open(os.devnull, "w") as log:
        async with stdio_client(params, errlog=log) as (receive, send):
            async with ClientSession(receive, send) as session:
                await session.initialize()
                yield session


async def state(session, session_id):
    code = ("import bpy\ns = bpy.context.scene\n"
            "result = [s.frame_current, s.frame_subframe, bpy.data.is_dirty, "
            "sorted(o.name for o in bpy.context.selected_objects), len(bpy.data.objects), len(bpy.data.meshes)]")
    return (await call(session, "execute_blender_code", expected_session=session_id, code=code))["data"]


async def verify(args, scratch):
    work = args.evidence.resolve()
    work.mkdir(parents=True, exist_ok=False)
    headless, live = work / "headless", work / "live"
    headless.mkdir()
    env = startup_environment(scratch / "profile")
    env.update(STEREOGRAPH_PORT=str(free_port()), STEREOGRAPH_TOKEN=secrets.token_urlsafe(32),
               STEREOGRAPH_BLENDER=args.blender)
    fixture = scratch / "spatial calibration.blend"
    run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
                 "--python-exit-code", "3", "--python", str(ROOT / "tests/spatial_fixture.py"), "--", str(fixture)],
                env, work / "fixture.log")
    before = file_hash(fixture)
    env["STEREOGRAPH_ALLOW_PYTHON"] = "0"
    async with client(args.server, env) as session:
        background = await calibration(session, headless, blend_file=str(fixture))
    if args.live:
        live.mkdir()
        addon = args.addon
        if addon is None:
            addon = scratch / "stereograph.zip"
            build_addon(args.blender, ROOT / "src/stereograph_bridge", addon, env, live / "build.log")
        env["STEREOGRAPH_ALLOW_PYTHON"] = "1"
        async with client(args.server, env) as session:
            with window(args.blender, fixture, addon, env, live) as process:
                health = await wait_for_live(session, process, live / "window.log")
                start = await state(session, health["session_id"])
                measured = await calibration(session, live)
                assert await state(session, health["session_id"]) == start, start
                assert measured == background
                for image in live.glob("*.svg"):
                    assert hashlib.sha256(image.read_bytes()).digest() == hashlib.sha256(
                        (headless / image.name).read_bytes()).digest(), image
    assert file_hash(fixture) == before
    (work / "fixture.json").write_text(json.dumps(dict(sha256=before, live=args.live,
        blender=shutil.which(args.blender)), indent=2), encoding="utf-8")
    print(f"PASS: spatial calibration, frame preservation and matching views. Evidence: {work}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--server", default=str(Path(sys.executable).with_name("stereograph")))
    parser.add_argument("--addon", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.addon:
        args.addon = args.addon.resolve(strict=True)
    if not args.blender:
        parser.error("Blender executable required")
    with TemporaryDirectory(prefix="stereograph-spatial-") as work:
        asyncio.run(verify(args, Path(work)))
