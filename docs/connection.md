# Connecting to Blender

Use Python 3.11 or later on the MCP side and Blender 5.3 or later. The qualification record identifies the tested build.

## Build and configure

From the repository root:

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

The bridge listens on `127.0.0.1:9877`. If that port is occupied, set the same `STEREOGRAPH_PORT` in both processes. For background execution alone, use the server and a Blender executable. The connection token and extension are only needed for live calls. For live health alone, omit `STEREOGRAPH_ALLOW_PYTHON`.

## Tools

| Tool | Behaviour |
| --- | --- |
| `blender_health` | Reports the loaded file and scene, bridge protocol, Blender and embedded Python versions, and an ID for the active file session. |
| `execute_blender_code` | Runs Python on the live Blender main thread. Supply the `session_id` returned by health as `expected_session`. Check the file path before executing. A file reload changes the session ID. |
| `execute_blender_code_for_cli` | Opens the given `.blend` in a fresh background process using factory startup and with scene auto-execution disabled. Script changes stay in memory unless the script saves them. |

For an initial read, pass `result = bpy.context.scene.name`. Assign a finite JSON-compatible value to `result` and use standard output or error for short diagnostics. Both execution tools accept a timeout in seconds. A result with `error.execution` set to `unknown` or `may_have_changed` needs state inspection before retrying. The background deadline stops Blender. A live deadline can expire while already-running Python is still executing. Live requests are never replayed automatically.

Python execution runs with Blender's full local permissions. Enable it for trusted clients and scripts only, and keep the port local. See the [security policy](../SECURITY.md).

## Reproduce the checks

```sh
uv run --locked python -m unittest discover -s tests
uv run --locked python tests/check_blender.py
uv run --locked python tests/check_blender.py --live
```

The last command also starts a temporary Blender window under an isolated test profile, installs the extension, exercises the live bridge and closes that window. Both native commands create their own disposable scene and verify its file hash at completion. Use `--blender /absolute/path/to/blender` to select a specific installation.
