"""Verify planted-hand preview, cancellation, stale rejection and native apply/undo."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import shutil
from tempfile import TemporaryDirectory
import sys
import xml.etree.ElementTree as ET

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from check_blender import ROOT, error, run_blender, wait_for_live
from check_parity import build_addon, file_hash, free_port, startup_environment, window


PROFILE = {
    "version": 1,
    "rig": "FK arm",
    "controls": {"upper": "Upper", "forearm": "Forearm", "hand": "Palm"},
    "palm_axis": [0, 1, 0],
    "anchor_object": "Moving prop",
    "hand_witness_object": "Hand witness",
    "clearance": [{"pair": ["Leather glove", "Forearm guard"], "minimum_gap": .04}],
    "limits": {
        "max_swivel": 25, "min_improvement": 3, "elbow_bend": [20, 100], "wrist_bend": 70,
        "position_tolerance": .00001, "rotation_tolerance": .01,
        "neighbour_position": .003, "neighbour_rotation": .5, "neighbour_step": 40,
    },
}
BLOCKED = dict(PROFILE, clearance=[{"pair": ["Forearm guard", "Cuff obstacle"], "minimum_gap": .02}])


@asynccontextmanager
async def client(server, env):
    with open(os.devnull, "w") as log:
        async with stdio_client(StdioServerParameters(command=server, env=env),
                                errlog=log) as (receive, send):
            async with ClientSession(receive, send, read_timeout_seconds=timedelta(seconds=130)) as session:
                await session.initialize()
                yield session


async def tool(session, name, **kwargs):
    outcome = await session.call_tool(name, kwargs)
    assert not outcome.isError and outcome.structuredContent is not None, outcome
    return outcome.structuredContent


async def preview(session, profile=PROFILE, **options):
    outcome = await tool(session, "preview_elbow_edit", profile=profile, **options)
    assert outcome["ok"], outcome
    return outcome["data"]


def retain(result, directory, name):
    data = dict(result)
    images = data.pop("evidence", [])
    for image in images:
        filename = f'{name}-{image["kind"]}-{image.get("phase", "comparison")}-{image["view"]}.svg'
        svg = image["svg"]
        assert ET.fromstring(svg).tag == "{http://www.w3.org/2000/svg}svg"
        (directory / filename).write_text(svg, encoding="utf-8")
    data.pop("candidate_id", None)
    (directory / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def check_feasible(result):
    assert result["feasible"] and result["anchor"] == "Moving prop", result.get("conflicts")
    sample = result["sample"]
    assert sample["before"]["wrist_bend_degrees"] - sample["after"]["wrist_bend_degrees"] > 3
    for label in ("hand_drift", "control_drift", "prop_drift"):
        assert sample[label]["position"] < .00001 and sample[label]["rotation_degrees"] < .01, sample[label]
    assert len(result["planned_keys"]) == 8 and {key["control"] for key in result["planned_keys"]} == {"Upper", "Palm"}
    assert all(key["frame"] == 37 for key in result["planned_keys"])
    assert [sample["frame"] for sample in result["neighbours"]] == [36, 36.5, 37.5, 38]


async def execute(session, session_id, code):
    result = await tool(session, "execute_blender_code", code=code, expected_session=session_id)
    assert result["ok"], result
    return result["data"]


SNAPSHOT = """
import bpy
from bpy_extras.anim_utils import action_get_channelbag_for_slot
scene = bpy.context.scene
rig = bpy.data.objects["FK arm"]
anim = rig.animation_data
bag = action_get_channelbag_for_slot(anim.action, anim.action_slot)
result = dict(
    action=anim.action.name, actions=sorted(item.name for item in bpy.data.actions),
    frame=scene.frame_current_final, dirty=bpy.data.is_dirty,
    keys={f"{f.data_path}:{f.array_index}":[list(k.co) for k in f.keyframe_points] for f in bag.fcurves},
    pose={b.name:[list(row) for row in b.matrix] for b in rig.pose.bones},
)
"""


async def live_cases(session, session_id, directory, background):
    original = await execute(session, session_id, SNAPSHOT)
    accepted = await preview(session, expected_session=session_id, views=["front", "side"])
    check_feasible(accepted)
    retain(accepted, directory, "live-preview")
    assert await execute(session, session_id, SNAPSHOT) == original
    assert accepted["sample"] == background["sample"], (accepted["sample"], background["sample"])
    assert accepted["evidence"] == background["evidence"]
    token = accepted["candidate_id"]
    assert (await tool(session, "cancel_elbow_edit", candidate_id=token, expected_session=session_id))["data"]["cancelled"]
    error(await tool(session, "apply_elbow_edit", candidate_id=token, expected_session=session_id),
          "pose_preview_unavailable")
    assert await execute(session, session_id, SNAPSHOT) == original

    token = (await preview(session, expected_session=session_id))["candidate_id"]
    await execute(session, session_id, "import bpy\nbpy.context.scene.frame_set(38)")
    state = await execute(session, session_id, SNAPSHOT)
    error(await tool(session, "apply_elbow_edit", candidate_id=token, expected_session=session_id),
          "stale_pose_preview")
    assert await execute(session, session_id, SNAPSHOT) == state
    await execute(session, session_id, "import bpy\nbpy.context.scene.frame_set(37)")

    token = (await preview(session, expected_session=session_id))["candidate_id"]
    await execute(session, session_id, 'import bpy\nbpy.data.objects["FK arm"].animation_data.action.name = "Retitled Action"')
    state = await execute(session, session_id, SNAPSHOT)
    error(await tool(session, "apply_elbow_edit", candidate_id=token, expected_session=session_id),
          "stale_pose_preview")
    assert await execute(session, session_id, SNAPSHOT) == state
    await execute(session, session_id, 'import bpy\nbpy.data.objects["FK arm"].animation_data.action.name = "Calibrated FK pose"')

    shift = '''import bpy
mesh = bpy.data.objects["Cuff obstacle"].data
for vertex in mesh.vertices:
    vertex.co.x += {amount}
mesh.update()
bpy.context.view_layer.update()
'''
    await execute(session, session_id, shift.format(amount=3))
    token = (await preview(session, BLOCKED, expected_session=session_id))["candidate_id"]
    await execute(session, session_id, shift.format(amount=-3))
    state = await execute(session, session_id, SNAPSHOT)
    blocked = await tool(session, "apply_elbow_edit", candidate_id=token, expected_session=session_id)
    assert blocked["ok"] and not blocked["data"]["applied"], blocked
    assert any(x["code"] == "clearance_conflict" for x in blocked["data"]["conflicts"]), blocked
    assert await execute(session, session_id, SNAPSHOT) == state
    retain(blocked["data"], directory, "failed-apply")

    token = (await preview(session, expected_session=session_id))["candidate_id"]
    state = await execute(session, session_id, SNAPSHOT)
    applied = await tool(session, "apply_elbow_edit", candidate_id=token, expected_session=session_id)
    assert applied["ok"] and applied["data"]["applied"] and applied["data"]["undoable"], applied
    after = await execute(session, session_id, SNAPSHOT)
    assert after["frame"] == 37 and after["action"] == applied["data"]["action"] and after["action"] != state["action"]
    changed = {key for key in after["keys"] if after["keys"][key] != state["keys"][key]}
    assert changed and all('"Upper"' in key or '"Palm"' in key for key in changed), changed
    for key in changed:
        before_values = {frame: value for frame, value in state["keys"][key] if frame != 37}
        assert before_values == {frame: value for frame, value in after["keys"][key] if frame != 37}, key
    retain(applied["data"], directory, "applied")

    undo = await execute(session, session_id, """
import bpy
window = bpy.context.window
area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
with bpy.context.temp_override(window=window, area=area, region=region):
    result = sorted(bpy.ops.ed.undo()) if bpy.ops.ed.undo.poll() else ['NOT_AVAILABLE']
""")
    assert undo == ["FINISHED"], undo
    health = await tool(session, "blender_health")
    assert health["ok"], health
    undone = await execute(session, health["data"]["session_id"], SNAPSHOT)
    assert {k: undone[k] for k in ("frame", "action", "keys", "pose")} == {
        k: state[k] for k in ("frame", "action", "keys", "pose")
    }, (state["action"], undone["action"])
    (directory / "native-undo.json").write_text(json.dumps(dict(result=undo, action=undone["action"]), indent=2))


async def verify(config, scratch):
    directory = config.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    env = startup_environment(scratch / "profile")
    env.update(STEREOGRAPH_PORT=str(free_port()), STEREOGRAPH_TOKEN=secrets.token_urlsafe(32),
               STEREOGRAPH_BLENDER=config.blender, STEREOGRAPH_ALLOW_PYTHON="0")
    fixture = scratch / "elbow fixture.blend"
    run_blender([config.blender, "--background", "--factory-startup", "--disable-autoexec",
                 "--python-exit-code", "3", "--python", str(ROOT / "tests/elbow_fixture.py"), "--", str(fixture)],
                env, directory / "fixture.log")
    before = file_hash(fixture)
    async with client(config.server, env) as session:
        kwargs = dict(blend_file=str(fixture))
        background = await preview(session, views=["front", "side"], **kwargs)
        check_feasible(background)
        assert "candidate_id" not in background
        retain(background, directory, "headless-preview")
        world_profile = {key: value for key, value in PROFILE.items() if key != "anchor_object"}
        world = await preview(session, world_profile, elbow_target=background["sample"]["after"]["elbow"], **kwargs)
        assert world["feasible"] and world["anchor"] == "world"
        assert world["sample"]["hand_drift"]["position"] < .00001
        retain(world, directory, "world-target")
        unreachable = await preview(session, elbow_target=[99, 12, 1], **kwargs)
        assert not unreachable["feasible"] and unreachable["conflicts"][0]["code"] == "elbow_target_unreachable"
        retain(unreachable, directory, "unreachable")
        blocked = await preview(session, BLOCKED, swivel_degrees=25, **kwargs)
        assert not blocked["feasible"] and any(x["code"] == "clearance_conflict" for x in blocked["conflicts"])
        retain(blocked, directory, "cuff-blocked")
    if config.live:
        env["STEREOGRAPH_ALLOW_PYTHON"] = "1"
        addon = config.addon or scratch / "stereograph.zip"
        if config.addon is None:
            build_addon(config.blender, ROOT / "src/stereograph_bridge", addon, env, directory / "build.log")
        async with client(config.server, env) as session:
            with window(config.blender, fixture, addon, env, directory) as process:
                health = await wait_for_live(session, process, directory / "window.log")
                await live_cases(session, health["session_id"], directory, background)
    assert file_hash(fixture) == before
    (directory / "fixture.json").write_text(json.dumps(dict(sha256=before, live=config.live), indent=2))
    print(f"PASS: planted-hand elbow preview and fixture preservation. Evidence: {directory}", flush=True)


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
    with TemporaryDirectory(prefix="stereograph-elbow-") as scratch:
        asyncio.run(verify(args, Path(scratch)))
