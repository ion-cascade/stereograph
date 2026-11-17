"""Matched benchmark requests and semantic checks for their replies."""

from dataclasses import dataclass
import hashlib
import json

LIVE_EXECUTE = "execute_blender_code"
BACKGROUND_EXECUTE = "execute_blender_code_for_cli"
PING = (
    "import bpy\n"
    "result = {'scene': bpy.context.scene.name, 'background': bpy.app.background}"
)
MESH_WORK = """
import bpy
from time import perf_counter_ns
started = perf_counter_ns()
graph = bpy.context.evaluated_depsgraph_get()
meshes = [obj.evaluated_get(graph).data for obj in bpy.context.scene.objects if obj.type == 'MESH']
result = {'meshes': len(meshes), 'evaluated_faces': sum(len(mesh.polygons) for mesh in meshes),
          'work_ns': perf_counter_ns() - started}
"""


def identity(data):
    return data


def execution(data):
    if "result" in data and data.get("status") == "ok":
        return data["result"]
    return data


def counts(data):
    source = data["datablock_counts"]
    keys = ("objects", "meshes", "scenes", "libraries", "actions")
    return {"counts": {key: source.get(key, 0) for key in keys}, "scene": data["scene_name"],
            "engine": data["render_engine"]}


def collection_names(data):
    def visit(node):
        siblings = sorted((obj["name"], obj["type"], obj["parent"]) for obj in node["objects"])
        return node["name"], siblings, sorted(visit(child) for child in node["children"])

    collections = data["collections"]
    roots = collections if isinstance(collections, list) else [collections]
    tree = sorted(visit(node) for node in roots)
    return {"scene": data["scene_name"], "tree_sha256": digest(tree)}


def layout(data):
    return {"workspace": data["workspace"], "scene": data["scene"],
            "area_types": sorted(area["type"] for area in data["areas"]),
            "active": data["active_object"]["name"] if data["active_object"] else None}


def measured_work(data):
    return {name: data[name] for name in ("meshes", "evaluated_faces")}


def payload(data):
    blob = data["blob"]
    return dict(length=len(blob), sha256=hashlib.sha256(blob.encode("ascii")).hexdigest())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Case:
    name: str
    tool: str
    normalize: object = identity
    code: str | None = None

    def arguments(self, reference, fixture, session_id):
        args = {}
        if self.code is not None:
            args["code"] = self.code
            if self.tool == LIVE_EXECUTE and not reference:
                args["expected_session"] = session_id
        if self.tool.endswith("_for_cli"):
            args["blend_file"] = str(fixture)
        return args


LIVE_CASES = (
    Case("live-ping", LIVE_EXECUTE, code=PING),
    Case("live-datablocks", "get_blendfile_summary_datablocks", counts),
    Case("live-object-tree", "get_objects_summary", collection_names),
    Case("live-window-layout", "get_screenshot_of_window_as_json", layout),
    Case("live-evaluated-meshes", LIVE_EXECUTE, measured_work, MESH_WORK),
    Case("live-output-1KiB", LIVE_EXECUTE, payload, "result = {'blob': 'x' * 1024}"),
    Case("live-output-256KiB", LIVE_EXECUTE, payload, "result = {'blob': 'x' * 262144}"),
)

BACKGROUND_CASES = (
    Case("background-ping", BACKGROUND_EXECUTE, code=PING),
    Case("background-datablocks", "get_blendfile_summary_datablocks_for_cli", counts),
    Case("background-evaluated-meshes", BACKGROUND_EXECUTE, measured_work, MESH_WORK),
)


def normalize(reply):
    if reply.get("ok") is True and "data" in reply:
        return reply["data"]
    return execution(reply)
