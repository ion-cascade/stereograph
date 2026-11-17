# Ion Cascade Stereograph

An independently maintained MCP server and Blender add-on for scene inspection, editing, and spatial verification.

Stereograph is a new Ion Cascade project. The repository currently contains the design milestones and acceptance checklist. The first installable release is planned for v0.1.0.

## Roadmap

| Milestone | Deliverable |
| --- | --- |
| 0.1 · Connection and execution | Connect a local MCP client to live and background Blender. Verify version reporting, structured errors, timeouts and recovery on disposable scenes. |
| 0.2 · Blender Lab parity | Qualify the standard tool set against Blender Lab MCP 1.0.3. Record the live and headless support for each tool. |
| 0.3 · Comparative qualification | Run both servers against the same fixtures and compare correctness, reliability, latency and recovery. |
| 0.4 · Spatial inspection | Add read-only evaluated-geometry queries, contact and containment checks, frame sampling, and matched viewport evidence. |

[GitHub milestones](https://github.com/ion-cascade/stereograph/milestones) · [Tool parity checklist](docs/parity.md) · [Architecture and acceptance gates](docs/roadmap.md)

## Development and releases

The server and Blender add-on are independently written. Blender Lab is the behavioural reference for the standard tool set, and Blender supplies native scene evaluation, geometry, animation and rendering.

[Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Release process](docs/releases.md) · [GitHub releases](https://github.com/ion-cascade/stereograph/releases)

Maintained by Ion Cascade, the team behind [Conductor for Apple Logic Pro](https://github.com/ion-cascade/conductor). [Attribution](NOTICE.md) · [MIT licence](LICENSE)
