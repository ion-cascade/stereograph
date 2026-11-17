"""One-shot Blender executable entry point."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import bpy

# Import our package without adding the host virtual environment's site-packages
# to Blender's Python path. Native extensions must come from Blender's runtime.
package = Path(__file__).resolve().parent
spec = spec_from_file_location("stereograph_bridge", package / "__init__.py",
                               submodule_search_locations=[str(package)])
bridge = module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

from stereograph_bridge.runtime import headless_request
from stereograph_bridge.wire import decode, encode

request_path, result_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
result_path.write_bytes(encode(headless_request(bpy, decode(request_path.read_bytes()))))
