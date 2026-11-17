# Comparative qualification

The benchmark compares Stereograph with a pristine checkout of Blender Lab MCP 1.0.3. It verifies the pinned commit and rejects tracked reference edits. It uses isolated Blender profiles, temporary scenes and loopback ports. It never loads a production scene. [Blender Lab reference](https://projects.blender.org/lab/blender_mcp)

## Run

Use the same Blender executable for both servers. Create a clean Blender Lab checkout at tag `v1.0.3` and a Python environment with its server dependencies. The harness invokes that interpreter with the pinned source on its import path. Start from the Stereograph checkout and choose an output directory that does not exist yet.

```sh
uv sync --locked
uv run --locked python tests/benchmark_blender.py \
  --reference-source /path/to/blender-lab-1.0.3 \
  --reference-python /path/to/reference-venv/bin/python \
  --evidence build/benchmark
uv run --locked python tests/report_benchmark.py build/benchmark
uv run --locked python tests/benchmark_process_wait.py \
  --output build/benchmark/process-wait-ab.json
```

Set `--blender` to select another Blender executable. Use `--server` and `--addon` to qualify an installed wheel and a prebuilt extension ZIP, and `--candidate-label` to label the release. Quick development runs can lower `--rounds`, `--live-repeats` and `--headless-repeats`.

## Method

- The small fixture contains an animated beveled cube, a linked library and a missing image. The heavier fixture adds 24 linked mesh objects with evaluated geometry and 512 local markers. Both servers open the same files. The harness checks fixture hashes before and after.
- Each tool has a normalized semantic check. It compares collection trees, counts, viewport state, evaluated face counts and payload hashes as appropriate. Both implementations must pass preflight on both fixtures before any timing is reported.
- The default run records four independent rounds in alternating server and fixture order. The live cases each have 40 timed samples per server per fixture. Headless cases have 20. Each round also records a fresh MCP initialization and a fresh Blender process opening the fixture and responding to a verified read. OS disk caches stay in their normal state.
- Request timing spans the MCP call from the client. Evaluated-mesh requests also time their work inside Blender. The difference combines dispatch, serialization, transfer, client processing and any headless startup. Response-size tests measure end-to-end cost at 1 KiB and 256 KiB and record a re-serialized MCP size as a payload proxy.
- A shared client deadline tests the same slow operation against both implementations. Live recovery verifies the slow script ran once, then checks reload, old-session handling where supported, window disconnect and reconnect. Headless recovery verifies a new call after a shared deadline. Native Stereograph timeouts are also covered by the [0.1 regression checks](connection-checks.md).
- The report lists medians, nearest-rank p95 and failure counts. It labels a result faster or slower only when every paired round clears a 10% median difference in the same direction, with at least four rounds and no failures. Other comparisons are inconclusive. Startup p95 with four observations is descriptive.

The published qualification covers one machine and Blender build. Whole-suite speed, GPU rendering performance and cross-platform behaviour are outside the benchmark's claims. [Release results](https://github.com/ion-cascade/stereograph/releases/tag/v0.3.0)
