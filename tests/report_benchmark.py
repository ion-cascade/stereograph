"""Turn the raw comparison samples into a report without hiding failed calls."""

import argparse
from collections import defaultdict
import json
from math import ceil
from pathlib import Path
from statistics import median

CANDIDATE = "stereograph"
REFERENCE = "blender-lab"


def p95(values):
    ordered = sorted(values)
    return ordered[ceil(.95 * len(ordered)) - 1]


def summary(rows, field="elapsed_ns"):
    measured = [row[field] / 1_000_000 for row in rows if row["ok"] and row.get(field) is not None]
    return {"attempts": len(rows), "failures": sum(not row["ok"] for row in rows),
            "p50_ms": median(measured) if measured else None, "p95_ms": p95(measured) if measured else None}


def classify(candidate, reference):
    if not candidate or not reference:
        return "Inconclusive"
    if any(not row["ok"] for row in candidate + reference):
        return "Inconclusive · failures"
    rounds = {row["round"] for row in candidate}
    if len(rounds) < 4 or rounds != {row["round"] for row in reference}:
        return "Inconclusive · few rounds"
    ratios = []
    for number in sorted(rounds):
        ours = median(row["elapsed_ns"] for row in candidate if row["round"] == number)
        theirs = median(row["elapsed_ns"] for row in reference if row["round"] == number)
        ratios.append(ours / theirs)
    if max(ratios) <= .9:
        return "Stereograph faster"
    if min(ratios) >= 1.1:
        return "Stereograph slower"
    return "Inconclusive"


def display(value):
    return "—" if value is None else f"{value:.2f}"


def comparisons(rows):
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["kind"] in {"request", "cold"}:
            key = (row["fixture"], row["kind"], row["case"])
            groups[key][row["server"]].append(row)
    result = []
    for (fixture, kind, case), group in sorted(groups.items()):
        item = dict(fixture=fixture, kind=kind, case=case,
                    classification=classify(group[CANDIDATE], group[REFERENCE]))
        for server in (CANDIDATE, REFERENCE):
            item[server] = summary(group[server])
        result.append(item)
    return result


def work_timings(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["kind"] == "request" and row["ok"] and row.get("work_ns") is not None:
            groups[row["fixture"], row["case"], row["server"]].append(row)
    return [
        dict(fixture=fixture, case=case, server=server, count=len(samples),
             work_ms=median(row["work_ns"] for row in samples) / 1_000_000,
             other_ms=median(row["elapsed_ns"] - row["work_ns"] for row in samples) / 1_000_000)
        for (fixture, case, server), samples in sorted(groups.items())
    ]


def recovery_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["kind"] == "recovery":
            groups[row["fixture"], row["case"], row["server"]].append(row)
    return [
        dict(fixture=fixture, case=case, server=server,
             elapsed=summary(samples), recovered=summary(samples, "recovered_ns"))
        for (fixture, case, server), samples in sorted(groups.items())
    ]


def render(meta, comparisons_result, timing, recovery, rows):
    table = [
        "# Stereograph 0.3 benchmark report", "",
        f"Candidate **{meta['candidate']}** · Blender Lab MCP 1.0.3 · **{meta['blender'][0]}**",
        f"{meta['host']} · {meta['cpu_count']} logical CPUs · Python {meta['python']}", "",
        "## Fixtures and method", "",
    ]
    for fixture in sorted(meta["fixtures"]):
        primary = meta["fixtures"][fixture]["fixture.blend"]
        library = meta["fixtures"][fixture]["fixture library.blend"]
        observed = next(row["semantic"] for row in rows if row["kind"] == "preflight"
                        and row["case"] == "live-evaluated-meshes" and row["fixture"] == fixture)
        table.append(
            f"- **{fixture}** · {primary['size_bytes']:,} byte scene and {library['size_bytes']:,} byte "
            f"library. {observed['meshes']} mesh objects and {observed['evaluated_faces']:,} evaluated faces."
        )
    table.extend([
        "",
        f"Each side ran {meta['rounds']} rounds per fixture, with {meta['live_repeats']} timed repeats per live "
        f"operation and {meta['headless_repeats']} per headless operation in each round. The harness alternated "
        "server and fixture order, randomized requests within each round, and checked semantic equality before "
        "timing. Startup measures fresh processes with OS caches left alone. Blender startup ends with a "
        "successful read from the opened scene and uses each bridge's default readiness behaviour.",
        "",
        "A workflow earns a faster or slower label only if the median differs by at least 10% in the same "
        "direction in every paired round, with at least four rounds and no failures. The p95 uses nearest rank "
        "over the observed successful samples. Startup p95 is descriptive with only a few observations. "
        "Inconclusive results mean the run did not clear that threshold.",
        "",
        "## Latency and failures",
        "",
        "| Fixture | Workflow | Stereograph p50 / p95 ms | Blender Lab p50 / p95 ms | Failed calls S / Lab | Result |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ])
    for item in comparisons_result:
        s, r = item[CANDIDATE], item[REFERENCE]
        table.append(f"| {item['fixture']} | {item['case']} | "
                     f"{display(s['p50_ms'])} / {display(s['p95_ms'])} | "
                     f"{display(r['p50_ms'])} / {display(r['p95_ms'])} | "
                     f"{s['failures']}/{s['attempts']} · {r['failures']}/{r['attempts']} | "
                     f"{item['classification']} |")
    table.extend([
        "", "## Time inside Blender", "",
        "The timed script measures the evaluated-mesh query inside Blender. The remainder includes dispatch, "
        "serialization, transfer, client processing and, for headless calls, startup and file load. It is a "
        "combined remainder. The 1 KiB and 256 KiB cases show how response size changes end-to-end latency. "
        "The raw records include re-serialized MCP reply sizes as a proxy for protocol payload.",
        "",
        "| Fixture | Workflow | Server | Median in-script ms | Median remainder ms |",
        "| --- | --- | --- | ---: | ---: |",
    ])
    for item in timing:
        table.append(f"| {item['fixture']} | {item['case']} | {item['server']} | "
                     f"{display(item['work_ms'])} | {display(item['other_ms'])} |")
    table.extend([
        "", "## Recovery", "",
        f"The shared client deadline was {meta['shared_client_deadline_s']:.2f} s on both implementations. "
        f"The disposable Blender script took at least {meta['timeout_script_delay_s']:.2f} s. "
        "The next read verified a single execution after the timeout. Stereograph's native deadline and stale "
        "session check are reported separately because the tools expose different controls.",
        "",
        "| Fixture | Scenario | Server | Passed | Detection or reload ms | Recovered from request start ms |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ])
    for item in recovery:
        observed, recovered = item["elapsed"], item["recovered"]
        table.append(f"| {item['fixture']} | {item['case']} | {item['server']} | "
                     f"{observed['attempts'] - observed['failures']}/{observed['attempts']} | "
                     f"{display(observed['p50_ms'])} | {display(recovered['p50_ms'])} |")
    table.extend([
        "", "The raw records contain per-call results and fixture SHA-256 hashes. Findings apply to this "
        "machine, Blender build and workload. The suite reproduces the comparison without using authored "
        "production scenes.", "",
    ])
    return "\n".join(table)


def main(directory):
    meta = json.loads((directory / "metadata.json").read_text())
    rows = [json.loads(line) for line in (directory / "samples.jsonl").read_text().splitlines()]
    report = comparisons(rows)
    timing = work_timings(rows)
    recovery = recovery_summary(rows)
    (directory / "comparison.json").write_text(json.dumps(
        dict(comparisons=report, timing=timing, recovery=recovery), indent=2
    ), encoding="utf-8")
    (directory / "BENCHMARK.md").write_text(render(meta, report, timing, recovery, rows), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record_directory", type=Path)
    main(parser.parse_args().record_directory)
