"""Qualify clothing review and corrective transactions against a native Blender fixture."""

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from check_blender import ROOT, error, run_blender, wait_for_live
from check_elbow import client, execute, tool
from check_parity import build_addon, file_hash, free_port, startup_environment, window
from check_rig import data, retain

FIT = {
    "version": 1, "name": "Skirt layers and hanger",
    "layers": {"skirt": {"object": "Front skirt", "kind": "cloth", "representation": "editable"},
               "trim": {"object": "Embroidered edge", "kind": "trim"},
               "hanger": {"object": "Hanger", "kind": "support"},
               "plate": {"object": "Knee plate", "kind": "plate"}},
    "attachments": {"sewn-edge": {"a": {"layer": "skirt", "vertex": 280},
                                  "b": {"layer": "trim", "point": [0, 0, 0]},
                                  "kind": "seam", "max_gap": .015, "max_drift": .001},
                    "hanger-drift": {"a": {"layer": "trim", "point": [.135, 0, 0]},
                                     "b": {"layer": "hanger", "point": [0, 0, 0]},
                                     "kind": "attachment", "max_gap": .1, "max_drift": .01}},
    "strain": {"panel": {"layer": "skirt", "vertex_group": "Feathered knee patch"}},
    "contacts": {"version": 1, "name": "Knee plate against skirt", "frames": [8, 12],
                 "surfaces": {"skirt": {"object": "Front skirt"}, "plate": {"object": "Knee plate"}},
                 "action": {"object": "Knee plate", "name": "Knee approaches skirt"},
                 "rules": [{"id": "knee", "pair": ["skirt", "plate"], "mode": "clear"}]},
}
PLAN = {
    "version": 1, "garment": "Front skirt", "mask": "Feathered knee patch",
    "protected_groups": ["Waist and sewn edges"], "protected_objects": ["Embroidered edge"],
    "direction": [0, 1, 0], "distance": .016, "frame": 10, "span": 2,
    "pairs": [{"obstacle": {"object": "Knee plate"}, "minimum_gap": .004},
              {"obstacle": {"object": "Clearance guard"}, "minimum_gap": .006}],
}
STATE = """
import bpy
o=bpy.data.objects["Front skirt"]
e=o.evaluated_get(bpy.context.evaluated_depsgraph_get())
result=dict(frame=bpy.context.scene.frame_current_final,
            modifiers=[m.name for m in o.modifiers], original=[list(v.co) for v in o.data.vertices],
            point=list(e.data.vertices[144].co),
            groups=sorted(g.name for g in bpy.data.node_groups),
            actions=sorted(a.name for a in bpy.data.actions),
            selection=[o.name for o in bpy.context.selected_objects])
"""
UNDO = """
import bpy
w=bpy.context.window
a=next(a for a in w.screen.areas if a.type=='VIEW_3D')
r=next(r for r in a.regions if r.type=='WINDOW')
with bpy.context.temp_override(window=w, area=a, region=r):
    result=sorted(bpy.ops.ed.undo())
"""


def retain_inspection(report, directory):
    report = deepcopy(report)
    contact = report["contact_review"]["data"]
    contact["identity"]["file"] = Path(contact["identity"]["file"]).name
    for source in (report["evidence"], contact["evidence"]):
        for entry in source:
            for view, svg in entry["diagrams"].items():
                assert ET.fromstring(svg).tag == "{http://www.w3.org/2000/svg}svg"
                path = directory / f"{entry.get('check', entry.get('rule', 'contact'))}-{view}.svg"
                path.write_text(svg)
                entry["diagrams"][view] = path.name
    (directory / "inspection.json").write_text(json.dumps(report, indent=2))


async def preview(session, **options):
    report = await data(session, "preview_cloth_corrective", profile=PLAN, **options)
    assert report["feasible"], report["conflicts"]
    target = next(row for row in report["samples"] if row["frame"] == PLAN["frame"])
    assert target["before"][0]["transverse_crossing"]
    assert target["after"][0]["distance"] >= .004
    assert all(max(row["detail_displacement"].values()) < 1e-6 for row in report["samples"])
    return report


async def native(context, health, directory):
    sid = health["session_id"]
    auth = dict(expected_session=sid)
    source = await execute(context, sid, STATE)
    report = await preview(context, views=["front", "side"], **auth)
    assert source == await execute(context, sid, STATE)
    retain(report, directory, "preview-live")
    candidate = report["candidate_id"]
    assert (await data(context, "cancel_cloth_corrective", candidate_id=candidate, **auth))["cancelled"]
    error(await tool(context, "apply_cloth_corrective", candidate_id=candidate, **auth), "cloth_preview_unavailable")
    candidate = (await preview(context, **auth))["candidate_id"]
    await execute(context, sid, "bpy.context.scene.frame_set(11)")
    error(await tool(context, "apply_cloth_corrective", candidate_id=candidate, **auth), "stale_corrective")
    await execute(context, sid, "bpy.context.scene.frame_set(10)")
    candidate = (await preview(context, **auth))["candidate_id"]
    await execute(context, sid, 'bpy.data.objects["Clearance guard"].location.y += .003')
    error(await tool(context, "apply_cloth_corrective", candidate_id=candidate, **auth), "stale_corrective")
    await execute(context, sid, 'bpy.data.objects["Clearance guard"].location.y -= .003')
    await execute(context, sid, 'bpy.data.objects["Front skirt"].modifiers.new("Unbaked cloth", "CLOTH")')
    for policy in ("current", "frozen_bake"):
        error(await tool(context, "preview_cloth_corrective", profile=dict(PLAN, cache_policy=policy), **auth),
              "corrective_cache_unresolved")
    await execute(context, sid, 'o=bpy.data.objects["Front skirt"]; o.modifiers.remove(o.modifiers["Unbaked cloth"])')
    candidate = (await preview(context, **auth))["candidate_id"]
    applied = await data(context, "apply_cloth_corrective", candidate_id=candidate, **auth)
    assert applied["applied"] and applied["undoable"]
    after = await execute(context, sid, STATE)
    assert after["modifiers"] == [*source["modifiers"], applied["modifier"]]
    assert abs(after["point"][1] - source["point"][1] - PLAN["distance"]) < 1e-6
    assert after["original"] == source["original"] and after["selection"] == source["selection"]
    retain(applied, directory, "applied")
    assert await execute(context, sid, UNDO) == ["FINISHED"]
    current = (await data(context, "blender_health"))["session_id"]
    assert await execute(context, current, STATE) == source
    (directory / "native-undo.json").write_text(json.dumps({"result": "FINISHED", "restored": True}))


async def qualify(args, work):
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    env = startup_environment(work / "profile")
    env.update(STEREOGRAPH_PORT=str(free_port()), STEREOGRAPH_TOKEN=secrets.token_urlsafe(32),
               STEREOGRAPH_BLENDER=args.blender, STEREOGRAPH_ALLOW_PYTHON="0")
    source = work / "clothing fit.blend"
    run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code",
                 "3", "--python", str(ROOT / "tests/fit_fixture.py"), "--", str(source)],
                env, directory / "fixture.log")
    digest = file_hash(source)
    options = dict(blend_file=str(source))
    async with client(args.server, env) as context:
        inspection = await data(context, "inspect_clothing_fit", profile=FIT, frames=[8, 10, 12],
                                views=["front", "side"], **options)
        assert inspection["layers"]["skirt"]["modifiers"][0]["type"] == "SOLIDIFY"
        assert inspection["layers"]["trim"]["parent"]["object"] == "Front skirt"
        assert any(r["status"] == "outside_limits" and r["check"] == "hanger-drift" for r in inspection["measurements"])
        assert all(r["status"] == "within_limits" for r in inspection["measurements"] if r["kind"] == "strain")
        assert inspection["contact_review"]["data"]["rules"][0]["status"] == "fail"
        retain_inspection(inspection, directory)
        result = await preview(context, views=["front", "side"], **options)
        assert "candidate_id" not in result
        retain(result, directory, "preview-headless")
        excessive = await data(context, "preview_cloth_corrective", profile=dict(PLAN, distance=.028), **options)
        assert not excessive["feasible"] and "candidate_id" not in excessive
        assert any(row["code"] == "pair_2_clearance" for row in excessive["conflicts"])
        retain(excessive, directory, "rejected-new-crossing")
    if args.live:
        addon = args.addon or work / "bridge.zip"
        if not args.addon:
            build_addon(args.blender, ROOT / "src/stereograph_bridge", addon, env, directory / "build.log")
        env["STEREOGRAPH_ALLOW_PYTHON"] = "1"
        async with client(args.server, env) as context:
            with window(args.blender, source, addon, env, directory) as process:
                health = await wait_for_live(context, process, directory / "window.log")
                await native(context, health, directory)
    assert file_hash(source) == digest
    print(f"PASS: clothing fit and corrective workflow. Evidence: {directory}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--server", default=str(Path(sys.executable).with_name("stereograph")))
    parser.add_argument("--addon", type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.addon:
        args.addon = args.addon.resolve(strict=True)
    with TemporaryDirectory(prefix="stereograph-fit-") as work:
        asyncio.run(qualify(args, Path(work)))
