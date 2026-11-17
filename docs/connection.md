# Connecting to Blender

Use Python 3.11 or later on the MCP side and Blender 5.3 or later. The qualification record identifies the tested build.

## Build and configure

Download the Python wheel and Blender extension ZIP from the [release page](https://github.com/ion-cascade/stereograph/releases). Install the wheel in a virtual environment with `python -m pip install <wheel>` and use that environment's `stereograph` command in the MCP client.

To build from source, run these commands from the repository root:

```sh
uv sync --locked
mkdir -p dist
blender --background --factory-startup --disable-autoexec --command extension build \
  --source-dir src/stereograph_bridge --output-dir dist
```

1. Generate a token with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep it local to the Blender and MCP processes.
2. For a live connection, launch Blender with `STEREOGRAPH_TOKEN` in its environment. In Blender's Preferences, install the generated ZIP using **Install from Disk** and enable Ion Cascade Stereograph. Set `STEREOGRAPH_ALLOW_PYTHON=1` before starting Blender if the client needs live execution.
3. Register a local stdio MCP server in the client, using the absolute executable path and the same token. A typical client entry is shown below.

```json
{
  "mcpServers": {
    "stereograph": {
      "command": "/absolute/path/to/stereograph/.venv/bin/stereograph",
      "env": {
        "STEREOGRAPH_TOKEN": "<same token as Blender>",
        "STEREOGRAPH_ALLOW_PYTHON": "1",
        "STEREOGRAPH_BLENDER": "/absolute/path/to/blender"
      }
    }
  }
}
```

The bridge listens on `127.0.0.1:9877`. If that port is occupied, set the same `STEREOGRAPH_PORT` in both processes. For background workflows alone, use the server and a Blender executable. The connection token and extension are only needed for live calls. For live health alone, omit `STEREOGRAPH_ALLOW_PYTHON`.

## Tools

| Tool | Behaviour |
| --- | --- |
| `blender_health` | Reports the loaded file and scene, bridge protocol, Blender and embedded Python versions, and an ID for the active file session. |
| `execute_blender_code` | Runs Python on the live Blender main thread. Supply the `session_id` returned by health as `expected_session`. Check the file path before executing. A file reload changes the session ID. |
| `execute_blender_code_for_cli` | Opens the given `.blend` in a fresh background process using factory startup and with scene auto-execution disabled. Script changes stay in memory unless the script saves them. |

For an initial read, pass `result = bpy.context.scene.name`. Assign a finite JSON-compatible value to `result` and use standard output or error for short diagnostics. Both execution tools accept a timeout in seconds. A result with `error.execution` set to `unknown` or `may_have_changed` needs state inspection before retrying. The background deadline stops Blender. A live deadline can expire while already-running Python is still executing. Live requests are never replayed automatically.

Python execution runs with Blender's full local permissions. Enable it for trusted clients and scripts only, and keep the port local. See the [security policy](../SECURITY.md).

The 0.2 [compatibility matrix](parity.md) lists the additional tools and their supported modes. File summaries, navigation, capture and rendering don't require Python opt-in. The live tools use the already-connected session. For a background render, set `blend_file` on the render tool. Output paths can be absolute or relative to the loaded file using Blender's `//` prefix. Use an existing directory and the extension for the scene's configured still image format.

For evaluated surface inspection and planted-hand FK elbow edits, see the [spatial inspection guide](spatial-inspection.md) and the [elbow editing guide](elbow-editing.md). Use the [motion review guide](motion-review.md) for phase-specific contact across an animation. See the [rig inspection guide](rig-inspection.md) for joint and weight diagnostics and undoable knee repair. These tools work without live Python opt-in.

## Install searchable documentation

Run `stereograph install-docs` once with Git available to install the pinned Blender documentation snapshot. The tool verifies the source commit and stores only the Python API and manual data locally. Lookups run offline and report the snapshot label.

For a different documentation version, configure `STEREOGRAPH_DOCS_ROOT` to a local directory containing `api/` and `manual/` trees of RST or HTML pages, and set `STEREOGRAPH_DOCS_VERSION` to the version of those files. The documentation version can differ from the connected Blender build. [Blender Python API](https://docs.blender.org/api/current/) · [Blender Manual](https://docs.blender.org/manual/en/latest/)

## Reproduce the checks

```sh
uv run --locked python -m unittest discover -s tests
uv run --locked python tests/check_blender.py
uv run --locked python tests/check_blender.py --live
```

The last command also starts a temporary Blender window under an isolated test profile, installs the extension, exercises the live bridge and closes that window. Both native commands create their own disposable scene and verify its file hash at completion. Use `--blender /absolute/path/to/blender` to select a specific installation. To verify a release build, pass its installed MCP command with `--server` and its published ZIP with `--live --addon`.
