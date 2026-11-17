"""Exercise phase rules, adaptive subframe review, comparison and native witness navigation."""

import argparse
import asyncio
from copy import deepcopy
import json
from math import dist
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import xml.etree.ElementTree as ET

from check_blender import ROOT, call, error, run_blender, wait_for_live
from check_parity import build_addon, file_hash, free_port, startup_environment, window
from check_spatial import client, state


PROFILE = {
    "version": 1,
    "name": "Calibrated handoff",
    "frames": [10, 11],
    "sampling": {"step": 1, "refine_step": .25, "near_gap": .55},
    "surfaces": {
        "anchor": {"object": "Anchor"},
        "moving": {"object": "Subframe motion"},
        "pad": {"object": "Exactly touching", "material": "Contact leather", "vertex_group": "Contact pad"},
        "gap": {"object": "Known gap"},
        "hardware": {"object": "Curved fitting"},
        "absent": {"object": "Absent mesh"},
        "open": {"object": "Open shell"},
        "nested": {"object": "Nested"},
    },
    "rules": [
        {"id": "sweep", "pair": ["moving", "anchor"], "mode": "clear"},
        {"id": "permitted", "pair": ["pad", "anchor"], "mode": "allowed"},
        {"id": "seated", "pair": ["pad", "anchor"], "mode": "required", "frames": [11, 11], "gap": .01},
        {"id": "missed", "pair": ["gap", "anchor"], "mode": "required", "frames": [11, 11], "gap": .01},
        {"id": "hardware", "pair": ["hardware", "anchor"], "mode": "clear", "gap": .1},
        {"id": "unresolved", "pair": ["absent", "anchor"], "mode": "clear"},
        {"id": "open volume", "pair": ["nested", "open"], "mode": "clear", "check_containment": True},
    ],
}


def check(report):
    assert report["range"] == [10, 11] and report["starting_frame"] == 7.25
    assert report["sampling_finished"] and report["limits"] == []
    index = {(s["rule"], s["frame"]): s for s in report["samples"]}
    assert index["sweep", 10]["status"] == index["sweep", 11]["status"] == "pass"
    assert index["sweep", 10.5]["code"] == "surface_crossing"
    assert index["permitted", 10]["status"] == "pass"
    assert index["permitted", 10]["surface_relation"] == "touching"
    assert index["seated", 11]["status"] == "pass"
    assert index["missed", 11]["code"] == "required_contact_missing"
    assert index["unresolved", 11]["status"] == "unresolved"
    assert index["hardware", 11]["status"] == "pass"
    assert index["hardware", 11]["triangle_counts"][0] > 0
    assert index["open volume", 10]["status"] == "inconclusive"
    rules = {rule["rule"]: rule for rule in report["rules"]}
    assert rules["missed"]["missed_all_required_samples"]
    assert rules["sweep"]["largest_sample_interval"] == .25
    assert rules["sweep"]["first_failure"] == 10.25


def retain(data, directory, name):
    report = deepcopy(data)
    for item in report["evidence"]:
        item["bookmark"]["session_id"] = None
        for view, svg in list(item["diagrams"].items()):
            assert ET.fromstring(svg).tag == "{http://www.w3.org/2000/svg}svg"
            filename = f"{name}-{item['frame']:g}-{view}.svg"
            (directory / filename).write_text(svg, encoding="utf-8")
            item["diagrams"][view] = filename
    report["identity"]["file"] = Path(report["identity"]["file"]).name
    for item in report["evidence"]:
        item["bookmark"]["identity"]["file"] = report["identity"]["file"]
    (directory / f"{name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


async def review(session, profile=PROFILE, **options):
    outcome = await call(session, "review_motion_contacts", profile=profile, **options)
    assert outcome["ok"], outcome
    return outcome["data"]


async def verify(args, root):
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    fixture = root / "contact motion.blend"
    env = startup_environment(root / "profile")
    env.update(STEREOGRAPH_PORT=str(free_port()), STEREOGRAPH_TOKEN=secrets.token_urlsafe(32),
               STEREOGRAPH_BLENDER=args.blender, STEREOGRAPH_ALLOW_PYTHON="0")
    setup = root / "setup_motion.py"
    setup.write_text(
        "import bpy, runpy, sys\n"
        "bpy.context.preferences.filepaths.save_version = 0\n"
        f"runpy.run_path({str(ROOT / 'tests/spatial_fixture.py')!r})\n"
        "obj = bpy.data.objects['Exactly touching']\n"
        "obj.data.materials.append(bpy.data.materials.new('Contact leather'))\n"
        "obj.vertex_groups.new(name='Contact pad').add(list(range(8)), 1, 'REPLACE')\n"
        "curve = bpy.data.curves.new('Curved fitting', 'CURVE')\n"
        "curve.dimensions = '3D'\n"
        "curve.bevel_depth = .15\n"
        "spline = curve.splines.new('POLY')\n"
        "spline.points.add(1)\n"
        "spline.points[0].co = (2, 0, -.5, 1)\n"
        "spline.points[1].co = (2, 0, .5, 1)\n"
        "fitting = bpy.data.objects.new('Curved fitting', curve)\n"
        "bpy.context.scene.collection.objects.link(fitting)\n"
        "bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])\n"
    )
    run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec",
                 "--python-exit-code", "3", "--python", str(setup), "--", str(fixture)],
                env, directory / "fixture.log")
    original = file_hash(fixture)
    options = dict(views=["front", "side"], evidence_rule="sweep")
    async with client(args.server, env) as session:
        begin = monotonic()
        background = await review(session, blend_file=str(fixture), **options)
        print(f"Background fixture elapsed {monotonic() - begin:.3f}s", flush=True)
        check(background)
        retain(background, directory, "headless")
    if args.live:
        archive = args.addon or root / "stereograph.zip"
        if not args.addon:
            build_addon(args.blender, ROOT / "src/stereograph_bridge", archive, env, directory / "build.log")
        env["STEREOGRAPH_ALLOW_PYTHON"] = "1"
        async with client(args.server, env) as session:
            with window(args.blender, fixture, archive, env, directory) as process:
                health = await wait_for_live(session, process, directory / "window.log")
                session_id = health["session_id"]
                start = await state(session, session_id)
                live = await review(session, **options)
                check(live)
                assert await state(session, session_id) == start
                assert live["samples"] == background["samples"]
                assert [item["diagrams"] for item in live["evidence"]] == [
                    item["diagrams"] for item in background["evidence"]
                ]
                bookmark = live["evidence"][0]["bookmark"]
                wrong = dict(bookmark, session_id="stale")
                error(await call(session, "jump_to_motion_witness", bookmark=wrong), "stale_motion_witness")
                assert await state(session, session_id) == start
                jumped = await call(session, "jump_to_motion_witness", bookmark=bookmark, view="side")
                assert jumped["ok"] and jumped["data"]["frame"] == bookmark["frame"]
                readback = await state(session, session_id)
                assert readback[:2] == [10, .25] and readback[2:] == start[2:], readback
                layout = await call(session, "get_screenshot_of_window_as_json")
                assert layout["ok"], layout
                view = next(area["space"] for area in layout["data"]["areas"] if area["type"] == "VIEW_3D")
                assert view["view_perspective"] == "ORTHO"
                assert dist(view["view_location"], bookmark["center"]) < 1e-6
                (directory / "jump-readback.json").write_text(json.dumps(
                    dict(frame=jumped["data"]["frame"], view=view["view_perspective"],
                         location=view["view_location"], selected_objects_unchanged=True), indent=2))
                retain(live, directory, "live")

                edited = await call(session, "execute_blender_code", expected_session=session_id, code=(
                    'import bpy\nobj = bpy.data.objects["Subframe motion"]\n'
                    'obj.delta_location.x = 2\n'
                    'bpy.data.objects["Exactly touching"].location = (1.6, .25, .125)\n'
                    'bpy.context.view_layer.update()'
                ))
                assert edited["ok"]
                replay = sorted({row["frame"] for row in live["samples"]})
                after = await review(session, views=["front", "side"], extra_frames=replay,
                                     match_evidence=[dict(rule=item["rule"], frame=item["frame"],
                                                          bookmark=item["bookmark"])
                                                     for item in live["evidence"]])
                for old, new in zip(live["evidence"], after["evidence"], strict=True):
                    assert old["bookmark"] == new["bookmark"]
                    assert new["relation"] == "separated"
                retain(after, directory, "after")
                live["evidence"] = after["evidence"] = []
                diff = await call(session, "compare_motion_reviews", before=live, after=after)
                assert diff["ok"], diff
                compared = diff["data"]
                assert compared["fixed"] and all(row["rule"] == "sweep" for row in compared["fixed"])
                assert compared["persistent"][0]["rule"] == "missed"
                assert {row["rule"] for row in compared["new"]} == {"permitted", "seated"}
                assert not compared["unverified"]
                for key in ("before", "after"):
                    compared[key]["file"] = fixture.name
                (directory / "comparison.json").write_text(json.dumps(compared, indent=2), encoding="utf-8")
    assert file_hash(fixture) == original
    print(f"PASS: motion review, subframe crossing, rule phases and source preservation. Evidence: {directory}", flush=True)


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
    with TemporaryDirectory(prefix="stereograph-motion-") as scratch:
        asyncio.run(verify(args, Path(scratch)))
