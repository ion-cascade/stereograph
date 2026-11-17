"""One-shot Blender executable entry point."""

from pathlib import Path
import sys

# Blender runs this as a script inside its own Python environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bpy
from stereograph_bridge.runtime import headless_request
from stereograph_bridge.wire import decode, encode

request_path, result_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
result_path.write_bytes(encode(headless_request(bpy, decode(request_path.read_bytes()))))
