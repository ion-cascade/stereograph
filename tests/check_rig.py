"""Verify rig diagnosis and transactional planted-foot repair through the MCP server."""

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


PROFILE = {
    "version": 1, "name": "Rig diagnostics calibration",
    "roles": {
        "bad axis": {"object": "Test rig", "bone": "Bad axis", "expected_axis": [0, 1, 0], "landmark": [0, 0, 2.1]},
        "missing": {"object": "Test rig", "bone": "Missing"},
        "hip": {"object": "Test rig", "bone": "Upper"},
        "knee": {"object": "Test rig", "bone": "Lower", "control": "Knee pole"},
        "foot": {"object": "Test rig", "bone": "Foot", "control": "Foot target"},
        **{bone: {"object": "Test rig", "bone": bone} for bone in ("Shoulder", "Elbow", "Palm", "Thumb base",
             "Thumb middle", "Thumb tip", "Finger base", "Finger middle", "Finger tip")},
    },
    "switches": {"leg": {"object": "Test rig", "bone": "Upper", "property": "IK_FK",
                         "ik_controls": ["Foot target", "Knee pole"]}},
    "bindings": {"knee weights": {"mesh": "Calibrated skin", "rig": "Test rig",
                                 "region": "Knee patch", "expected_bones": ["Lower"]}},
    "checks": {
        "leg": {"kind": "leg", "roles": {"hip": "hip", "knee": "knee", "foot": "foot"},
                "limits": {"tracking": [-3, 3]}, "contact_rule": "knee clearance"},
        "plant": {"kind": "plant", "roles": {"foot": "foot"},
                  "limits": {"position_drift": [0, .0001], "rotation_drift": [0, .1]}},
        "reach": {"kind": "arm", "roles": {"shoulder": "Shoulder", "elbow": "Elbow", "wrist": "Palm"}},
        "thumb": {"kind": "thumb", "roles": {"palm": "Palm", "cmc": "Thumb base", "mcp": "Thumb middle",
                                              "ip": "Thumb tip"}},
        "finger": {"kind": "finger", "roles": {"base": "Finger base", "middle": "Finger middle",
                                                "tip": "Finger tip"}},
    },
}
KNEE = {
    "version": 1, "rig": "Test rig",
    "controls": {"upper": "Upper", "lower": "Lower", "foot": "Foot", "ik_owner": "Lower", "pole": "Knee pole"},
    "forward": [0, 1, 0],
    "clearance": [{"pair": ["Knee guard", "Side obstacle"], "minimum_gap": .01}],
    "limits": {"max_swivel": 25, "minimum_improvement": 5, "neighbour_step": 20},
}
CONTACT = {
    "version": 1, "name": "Knee armour", "frames": [9, 11],
    "action": {"object": "Test rig", "name": "Calibration walk"},
    "surfaces": {"guard": {"object": "Knee guard"}, "obstacle": {"object": "Side obstacle"}},
    "rules": [{"id": "knee clearance", "pair": ["guard", "obstacle"], "mode": "clear"}],
}

STATE = """
import bpy
from bpy_extras.anim_utils import action_get_channelbag_for_slot
r=bpy.data.objects['Test rig']
a=r.animation_data
s=bpy.context.scene
bag=action_get_channelbag_for_slot(a.action,a.action_slot)
result=dict(action=a.action.name, frame=s.frame_current_final, dirty=bpy.data.is_dirty,
            selected=sorted(b.name for b in bpy.context.selected_objects),
            keys={f'{c.data_path}:{c.array_index}':[list(key.co) for key in c.keyframe_points] for c in bag.fcurves},
            pose={name:[list(row) for row in r.pose.bones[name].matrix] for name in ('Lower','Foot','Knee pole')})
"""


async def data(session, name, **arguments):
    result = await tool(session, name, **arguments)
    assert result["ok"], result
    return result["data"]


def retain(report, directory, name):
    revised = deepcopy(report)
    revised.pop("candidate_id", None)
    if "identity" in revised and revised["identity"]["file"]:
        revised["identity"]["file"] = Path(revised["identity"]["file"]).name
    for view in revised.get("evidence", []):
        assert ET.fromstring(view["svg"]).tag == "{http://www.w3.org/2000/svg}svg"
        filename = f"{name}-{view.get('phase', 'rig')}-{view['view']}.svg"
        (directory / filename).write_text(view["svg"])
        view["svg"] = filename
    (directory / f"{name}.json").write_text(json.dumps(revised, indent=2))


async def inspection(session, **options):
    contact = await data(session, "review_motion_contacts", profile=CONTACT, **options)
    result = await data(session, "inspect_rig", profile=PROFILE, frames=[9, 10, 10.5, 11],
                        views=["front", "side"], evidence_frame=10, contact_report=contact, **options)
    assert result["skeletal_fit"]["bad axis"]["findings"] == ["rest_landmark_offset", "rest_axis_mismatch"]
    assert result["skeletal_fit"]["missing"]["status"] == "unresolved"
    assert result["binding"]["knee weights"]["counts"] == dict(vertices=8, unweighted=1, non_normalized=1, unexpected=1)
    assert result["attached_geometry"]["rules"] == {"knee clearance": "pass"}
    assert result["attached_geometry"]["coverage"]["leg"]["unmeasured"] == [10.5]
    assert result["samples"][1]["switches"]["leg"]["mode"] == "IK"
    assert result["samples"][1]["checks"]["leg"]["status"] == "finding"
    assert abs(result["samples"][1]["checks"]["leg"]["values"]["tracking"] - 17) < .5
    assert result["samples"][-1]["checks"]["thumb"]["values"]["basal_rotation"] > 25
    assert result["samples"][-1]["checks"]["finger"]["values"]["curl"] > 60
    assert all(sample["checks"]["plant"]["status"] == "pass" for sample in result["samples"])
    return result


async def preview(session, **options):
    result = await data(session, "preview_knee_repair", profile=KNEE, **options)
    assert result["feasible"], result.get("conflicts")
    assert abs(result["sample"]["after"]["tracking"]) < .75
    assert result["sample"]["foot_drift"]["position"] < .00001
    assert all(isinstance(key["value"], float) for key in result["planned_keys"])
    assert [item["index"] for item in result["planned_keys"]] == [0, 1, 2]
    assert {item["path"] for item in result["planned_keys"]} == {'pose.bones["Knee pole"].location'}
    assert [item["frame"] for item in result["neighbours"]] == [9, 9.5, 10.5, 11]
    return result


async def native(session, health, directory, expected):
    sid = health["session_id"]
    auth = dict(expected_session=sid)
    source = await execute(session, sid, STATE)
    read = await inspection(session)
    retain(read, directory, "inspection-live")
    assert read["samples"] == expected["samples"] and read["evidence"] == expected["evidence"]
    result = await preview(session, views=["front", "side"], **auth)
    retain(result, directory, "preview-live")
    assert source == await execute(session, sid, STATE)
    candidate = result["candidate_id"]
    assert (await data(session, "cancel_knee_repair", candidate_id=candidate, **auth))["cancelled"]
    error(await tool(session, "apply_knee_repair", candidate_id=candidate, **auth), "knee_preview_unavailable")
    candidate = (await preview(session, **auth))["candidate_id"]
    await execute(session, sid, "import bpy\nbpy.context.scene.frame_set(11)")
    error(await tool(session, "apply_knee_repair", candidate_id=candidate, **auth), "stale_knee_preview")
    await execute(session, sid, "import bpy\nbpy.context.scene.frame_set(10)")
    stale_action = (await preview(session, **auth))["candidate_id"]
    await execute(session, sid, 'import bpy\nbpy.data.objects["Test rig"].animation_data.action.name = "Other Action"')
    error(await tool(session, "apply_knee_repair", candidate_id=stale_action, **auth), "stale_knee_preview")
    await execute(session, sid, 'import bpy\nbpy.data.objects["Test rig"].animation_data.action.name = "Calibration walk"')
    safe = await preview(session, **auth)
    obstacle = await execute(session, sid, """
import bpy
result = [list(vertex.co) for vertex in bpy.data.objects["Side obstacle"].data.vertices]
""")
    before, after = (safe["sample"][name]["knee"] for name in ("before", "after"))
    shift = [end - start for start, end in zip(before, after)]
    await execute(session, sid, f"""
import bpy
from mathutils import Vector
obj = bpy.data.objects["Side obstacle"]
guard = bpy.data.objects["Knee guard"]
target = guard.matrix_world.translation + Vector({shift!r}) + Vector((0, 0, .065))
local = obj.matrix_world.inverted() @ target
for vertex in obj.data.vertices:
    vertex.co += local
obj.data.update()
bpy.context.view_layer.update()
""")
    staged = await execute(session, sid, STATE)
    rejected = await data(session, "apply_knee_repair", candidate_id=safe["candidate_id"], **auth)
    assert not rejected["applied"] and any(row["code"] == "knee_clearance_conflict" for row in rejected["conflicts"])
    assert staged == await execute(session, sid, STATE)
    retain(rejected, directory, "rejected-clearance")
    await execute(session, sid, f"""
import bpy
obj = bpy.data.objects["Side obstacle"]
for vertex, original in zip(obj.data.vertices, {obstacle!r}):
    vertex.co = original
obj.data.update()
bpy.context.view_layer.update()
""")
    original = await execute(session, sid, STATE)
    candidate = (await preview(session, **auth))["candidate_id"]
    applied = await data(session, "apply_knee_repair", candidate_id=candidate, **auth)
    assert applied["applied"] and applied["undoable"]
    after = await execute(session, sid, STATE)
    assert after["action"] == applied["action"] and after["pose"]["Foot"] == original["pose"]["Foot"]
    for channel, keys in original["keys"].items():
        if 'Knee pole' not in channel:
            assert after["keys"][channel] == keys
        else:
            assert [key for key in after["keys"][channel] if key[0] != 10] == [key for key in keys if key[0] != 10]
    retain(applied, directory, "applied")
    undo = await execute(session, sid, """
import bpy
w = bpy.context.window
a = next(a for a in w.screen.areas if a.type == 'VIEW_3D')
r = next(r for r in a.regions if r.type == 'WINDOW')
with bpy.context.temp_override(window=w, area=a, region=r):
    result = sorted(bpy.ops.ed.undo())
""")
    assert undo == ["FINISHED"]
    updated = (await data(session, "blender_health"))["session_id"]
    restored = await execute(session, updated, STATE)
    for key in ("frame", "action", "pose", "keys"):
        assert restored[key] == original[key], key
    (directory / "native-undo.json").write_text(json.dumps(dict(result=undo, action=restored["action"]), indent=2))


async def verify(args, work):
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    env = startup_environment(work / "profile")
    env.update(STEREOGRAPH_PORT=str(free_port()), STEREOGRAPH_TOKEN=secrets.token_urlsafe(32),
               STEREOGRAPH_BLENDER=args.blender, STEREOGRAPH_ALLOW_PYTHON="0")
    source = work / "rig calibration.blend"
    run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code",
                 "3", "--python", str(ROOT / "tests/rig_fixture.py"), "--", str(source)],
                env, directory / "fixture.log")
    digest = file_hash(source)
    async with client(args.server, env) as session:
        options = dict(blend_file=str(source))
        baseline = await inspection(session, **options)
        retain(baseline, directory, "inspection-headless")
        result = await preview(session, views=["front", "side"], **options)
        retain(result, directory, "preview-headless")
        unreachable = await data(session, "preview_knee_repair", profile=KNEE, knee_target=[9, 4, 8], **options)
        assert not unreachable["feasible"] and unreachable["conflicts"][0]["code"] == "knee_target_unreachable"
        retain(unreachable, directory, "unreachable")
    if args.live:
        addon = args.addon or work / "bridge.zip"
        if not args.addon:
            build_addon(args.blender, ROOT / "src/stereograph_bridge", addon, env, directory / "build.log")
        env["STEREOGRAPH_ALLOW_PYTHON"] = "1"
        async with client(args.server, env) as session:
            with window(args.blender, source, addon, env, directory) as process:
                health = await wait_for_live(session, process, directory / "window.log")
                await native(session, health, directory, baseline)
    assert file_hash(source) == digest
    print(f"PASS: rig diagnostics and planted-foot knee repair. Evidence: {directory}", flush=True)


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
        parser.error("Blender executable required.")
    with TemporaryDirectory(prefix="stereograph-rig-") as work:
        asyncio.run(verify(args, Path(work)))
