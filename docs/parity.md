# Blender Lab compatibility

Stereograph implements the 26 workflows exposed by Blender Lab MCP **1.0.3** at commit `2cea8d566dde07fbac28a61d698909d69724e853`. The public tool names are the same. Qualification uses Blender **5.3.0** on Linux and opens the same disposable scene in both servers. [Blender Lab MCP](https://www.blender.org/lab/mcp-server/) · [Reference source](https://projects.blender.org/lab/blender_mcp)

| Tool | Stereograph mode | Acceptance |
| --- | --- | --- |
| `execute_blender_code` | Live | The script identifies the active scene. The connection regression verifies edits, timeout recovery, and stale-session rejection. |
| `execute_blender_code_for_cli` | Headless | A fresh process identifies the specified scene. The connection regression checks errors, timeouts, and fixture preservation. |
| `get_blendfile_summary_datablocks` | Live | Counts for objects, meshes, scenes, and libraries, with workspace and render engine. |
| `get_blendfile_summary_datablocks_for_cli` | Headless | Same file summary from a fresh process. |
| `get_blendfile_summary_missing_files` | Live | Reports the missing image path and excludes the existing reference. |
| `get_blendfile_summary_missing_files_for_cli` | Headless | Resolves the same missing image relative to the file opened in the process. |
| `get_blendfile_summary_of_linked_libraries` | Live | Reports the directly linked library and its count. |
| `get_blendfile_summary_of_linked_libraries_for_cli` | Headless | Reports dependencies from the specified file. |
| `get_blendfile_summary_path_info` | Live | Reports the file path, size and numbered backup. |
| `get_blendfile_summary_path_info_for_cli` | Headless | Reports the same path metadata and rejects a missing file. |
| `get_blendfile_summary_usage_guess` | Live | Returns all reference categories and detects the fixture's keyed animation. |
| `get_blendfile_summary_usage_guess_for_cli` | Headless | Returns the same categories and detects keyed animation from disk. |
| `get_object_detail_summary` | Live | Reports known location and mesh name. Rejects an absent object. |
| `get_objects_summary` | Live | Lists the fixture's nested collection and object. |
| `get_python_api_docs` | Host | Resolves a qualified RNA member and lists namespace entries for a wildcard. |
| `search_api_docs` | Host | Finds API references to `matrix_world` in the same corpus. |
| `search_manual_docs` | Host | Finds the Local View section in the same manual. |
| `get_screenshot_of_area_as_image` | Live | Returns a valid PNG of the 3D area within the requested byte limit. Rejects an absent editor. |
| `get_screenshot_of_window_as_image` | Live | Returns a valid PNG of the whole window within the requested byte limit. |
| `get_screenshot_of_window_as_json` | Live | Reports active workspace, view, areas and selection. |
| `jump_to_tab_by_name` | Live | Switches to the requested existing workspace and rejects an absent name. |
| `jump_to_tab_by_space_type` | Live | Selects a workspace whose main editor matches the requested type. |
| `jump_to_view3d_object_by_name` | Live | Frames the named object and verifies selection in window state. |
| `jump_to_view3d_object_data_by_name` | Live | Selects and frames the object that uses the named mesh. |
| `render_thumbnail_to_path` | Live or headless | Writes a smaller camera image and restores scene settings. Blender Lab exposes this through its live tool. |
| `render_viewport_to_path` | Live or headless | Writes a full-size camera image using scene settings. Blender Lab exposes this through its live tool. |

## Compatibility differences

- Stereograph uses the 0.1 structured response envelope, with successful tool data under `data` and stable error codes under `error`. Image calls produce MCP image content. Local Python execution keeps the 0.1 opt-in and live session ID check. `blender_health` remains available alongside the 26 reference tools.
- Live tools require an interactive Blender connection. With no window connected they return `blender_unavailable`. Explicit headless file operations use `blend_file`. Documentation runs in the host process without starting Blender.
- Documentation is installed separately with `stereograph install-docs`. A custom corpus can supply its own version label. Results are ranked per page, with source locations and optional context. Wildcard API discovery returns pages of up to 500 identifiers.
- Usage scores are heuristic, with independently chosen signals and confidence values. Linked libraries include parent names for indirect entries so a caller can reconstruct the tree. Object details include the world matrix and rotation representation.
- Framing operates in Object Mode. The current viewport must have a 3D editor. Placement and selection are changed through Blender, and visibility is only adjusted when `allow_edits` is supplied.
- Renders write to the requested file and stage the image before replacing the destination. The suffix must match the scene's still image format. Blender Lab returns the file it created in its temporary directory. Stereograph thumbnails cap the longest edge at 320 pixels and limit samples where supported. In the qualified Blender build, Blender Lab's live thumbnail sometimes used one or both full-size dimensions.

## Reproduce the comparison

Check out the [pinned Blender Lab source](https://projects.blender.org/lab/blender_mcp) at tag `v1.0.3` and install its MCP server dependencies in a separate Python environment. Then, from the Stereograph checkout, use the following command with an unused output directory.

```sh
uv run --locked python tests/check_parity.py \
  --reference-source /path/to/blender-lab-1.0.3 \
  --reference-python /path/to/reference-environment/bin/python \
  --render-headless --evidence build/parity/reproduction
```

The harness generates and hashes a shared disposable file and linked library. It installs each add-on in an isolated profile, uses an ephemeral loopback port and closes each test window. The records include tool schemas, arguments, results, resulting images, and fixture hashes. Omit the reference arguments and set `--docs-root` to run the Stereograph checks alone. Use `--server` and `--addon` to test the release wheel's command and release ZIP. See the [connection guide](connection.md) for normal installation.
