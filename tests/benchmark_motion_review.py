"""Measure release review latency and correctness on two dense animated meshes."""

import argparse
import asyncio
import json
from pathlib import Path
import shutil
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from check_blender import run_blender
from check_motion_review import review
from check_spatial import client
from check_parity import file_hash, startup_environment


FIXTURE = """
import bpy
from math import sin
import sys

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "DenseMotionReview"
side = 129
vertices = [(i / side, j / side, .01 * sin(6 * i / side) * sin(6 * j / side))
            for j in range(side) for i in range(side)]
faces = [(j * side + i, j * side + i + 1, (j + 1) * side + i + 1, (j + 1) * side + i)
         for j in range(side - 1) for i in range(side - 1)]
for name in ("Panel A", "Panel B"):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)

obj.location.x = .031
obj.rotation_euler.y = .15
for frame, z in ((10, .20), (11, .15), (11.5, .065), (12, .20)):
    obj.location.z = z
    obj.keyframe_insert(data_path="location", frame=frame)
scene.frame_set(10)
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
"""

PROFILE = {
    "version": 1,
    "name": "Dense panel clearance",
    "frames": [10, 12],
    "sampling": {"step": 1, "refine_step": .5, "near_gap": .08},
    "surfaces": {"a": {"object": "Panel A"}, "b": {"object": "Panel B"}},
    "rules": [{"id": "clearance", "pair": ["a", "b"], "mode": "clear"}],
}


async def benchmark(args, scratch):
    env = startup_environment(scratch / "profile")
    env.update(STEREOGRAPH_BLENDER=args.blender, STEREOGRAPH_ALLOW_PYTHON="0")
    fixture_script = scratch / "dense_fixture.py"
    fixture_script.write_text(FIXTURE)
    fixture = scratch / "dense motion.blend"
    run_blender([args.blender, "--background", "--factory-startup", "--disable-autoexec",
                 "--python-exit-code", "3", "--python", str(fixture_script), "--", str(fixture)],
                env, scratch / "fixture.log")
    digest = file_hash(fixture)
    seconds, samples = [], None
    async with client(args.server, env) as session:
        for _ in range(args.repeats):
            begin = monotonic()
            report = await review(session, PROFILE, blend_file=str(fixture))
            seconds.append(monotonic() - begin)
            assert report["sampling_finished"], report["limits"]
            assert all(row["triangle_counts"] == [32768, 32768] for row in report["samples"])
            assert any(row["frame"] == 11.5 and row["code"] == "surface_crossing" for row in report["samples"])
            current = report["samples"]
            if samples is not None:
                assert current == samples
            samples = current
    assert file_hash(fixture) == digest
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    result = dict(repeats=args.repeats, fixture_sha256=digest, triangle_counts=[32768, 32768],
                  seconds=seconds, median_seconds=median(seconds), profile=PROFILE, samples=samples)
    args.evidence.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Dense motion review median {result['median_seconds']:.3f}s over {args.repeats} runs.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--server", default=str(Path(sys.executable).with_name("stereograph")))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("At least one repeat is required.")
    with TemporaryDirectory(prefix="stereograph-dense-") as root:
        asyncio.run(benchmark(args, Path(root)))
