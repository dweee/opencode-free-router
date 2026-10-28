import { test } from "node:test"
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import { percentiles, screeningScore } from "../src/lib.mts"
import type { ModelInfo } from "../src/lib.mts"

const RANKING = fileURLToPath(new URL("../scripts/nixbench-ranking.py", import.meta.url))
const NOW = Date.parse("2026-10-25T00:00:00Z")

const flint: ModelInfo = {
  providerID: "openrouter",
  id: "recursive/flint-v1.0:free",
  name: "Flint (free)",
  release_date: "2026-10-21T00:00:00.000Z",
}
const ultra: ModelInfo = {
  providerID: "openrouter",
  id: "nvidia/nemotron-3-ultra-550b-a55b:free",
  name: "Nemotron 3 Ultra (free)",
  release_date: "2026-06-04T00:00:00.000Z",
}
const web: ModelInfo = {
  providerID: "openrouter",
  id: "example/web-only:free",
  name: "Web Only",
  release_date: "2026-10-22T00:00:00.000Z",
}

test("models with no leaderboard match are screened before scored ones", () => {
  const ranked = screeningScore(
    [flint, ultra, web],
    {
      arena_agent: percentiles([
        { name: "Nemotron 3 Ultra", value: 1400 },
        { name: "Other Agent", value: 1000 },
        { name: "Low Agent", value: 100 },
      ]),
      arena_webdev: percentiles([
        { name: "Nemotron 3 Ultra", value: 1500 },
        { name: "Web Only", value: 1200 },
        { name: "Low Web", value: 100 },
      ]),
      arena_text: percentiles([
        { name: "Nemotron 3 Ultra", value: 1400 },
        { name: "Other Text", value: 1000 },
        { name: "Low Text", value: 100 },
      ]),
    },
    NOW,
  )
  assert.deepEqual(
    ranked.map((row) => row.id),
    [
      "openrouter/recursive/flint-v1.0:free",
      "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
      "openrouter/example/web-only:free",
    ],
  )
  const missed = ranked[0]
  const best = ranked[1]
  const webOnly = ranked[2]
  assert.ok(missed)
  assert.ok(best)
  assert.ok(webOnly)
  assert.equal(missed.reason.startsWith("unscored"), true)
  assert.equal(best.reason, "benchmarks (arena_agent, arena_webdev, arena_text)")
  assert.equal(webOnly.reason, "benchmarks (arena_webdev)")
})

test("OpenRouter's free router is not a screening candidate", () => {
  const run = spawnSync(
    "python3",
    [
      "-c",
      `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ranking", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
def row(model_id, name):
    return {
        "id": model_id,
        "name": name,
        "cost": {"input": 0, "output": 0},
        "modalities": {"input": ["text"]},
        "tool_call": True,
        "limit": {"context": 128000},
        "release_date": "2026-01-01",
    }
catalog = {"openrouter": {"models": {
    "free": row("openrouter/free", "Free Models Router"),
    "auto": row("openrouter/auto", "Auto Router"),
    "flint": row("recursive/flint-v1.0:free", "Flint"),
}}}
print(json.dumps([item["id"] for item in mod.discover(catalog)]))
`,
      RANKING,
    ],
    { encoding: "utf8" },
  )
  assert.equal(run.status, 0, run.stderr)
  assert.deepEqual(JSON.parse(run.stdout), ["openrouter/recursive/flint-v1.0:free"])
})

test("deprecated catalog models are not screening candidates", () => {
  const run = spawnSync(
    "python3",
    [
      "-c",
      `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ranking", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
def row(model_id, **extra):
    return {
        "id": model_id,
        "cost": {"input": 0, "output": 0},
        "modalities": {"input": ["text"]},
        "tool_call": True,
        "limit": {"context": 128000},
        "release_date": "2026-01-01",
        **extra,
    }
catalog = {"opencode": {"models": {
    "old": row("old-free", status="deprecated"),
    "live": row("live-free"),
}}}
print(json.dumps([item["id"] for item in mod.discover(catalog, "opencode")]))
`,
      RANKING,
    ],
    { encoding: "utf8" },
  )
  assert.equal(run.status, 0, run.stderr)
  assert.deepEqual(JSON.parse(run.stdout), ["opencode/live-free"])
})

test("the queue keeps a higher screening score ahead of a newer release", () => {
  const run = spawnSync(
    "python3",
    [
      "-c",
      `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ranking", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
available = [
  {"id": "openrouter/recursive/flint-v1.0:free", "releaseDate": "2026-10-21"},
  {"id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", "releaseDate": "2026-06-04"},
]
priority = [
  {"id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", "score": 0.9, "reason": "benchmarks (arena_agent)", "sources": {"arena_agent": {"pct": 0.9}}},
  {"id": "openrouter/recursive/flint-v1.0:free", "score": 0.2, "reason": "benchmarks (arena_agent)", "sources": {"arena_agent": {"pct": 0.2}}},
]
print(json.dumps(mod.order_available(available, priority)))
`,
      RANKING,
    ],
    { encoding: "utf8" },
  )
  assert.equal(run.status, 0, run.stderr)
  const ordered = JSON.parse(run.stdout) as Array<{ id: string; priority: { score: number } }>
  assert.deepEqual(ordered.map((row) => row.id), [
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter/recursive/flint-v1.0:free",
  ])
  const top = ordered[0]
  assert.ok(top)
  assert.equal(top.priority.score, 0.9)
})

function ranking(script: string, ...args: string[]) {
  return spawnSync(
    "python3",
    [
      "-c",
      `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ranking", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
${script}
`,
      RANKING,
      ...args,
    ],
    { encoding: "utf8" },
  )
}

test("OpenCode Go screens only the allowlisted models, paid or not", () => {
  const run = ranking(`
def row(model_id, cost, **extra):
    return {"id": model_id, "cost": cost, "modalities": {"input": ["text"]}, "tool_call": True,
            "limit": {"context": 1000000}, "release_date": "2026-01-01", **extra}
catalog = {"opencode-go": {"models": {
    "beam-free": row("beam-free", {"input": 0, "output": 0}),
    "glm-5.3-flash": row("glm-5.3-flash", {"input": 0.15, "output": 0.5}),
    "kimi-k3": row("kimi-k3", {"input": 3, "output": 15}),
    "mimo-v2.6-flash": row("mimo-v2.6-flash", {"input": 0.14, "output": 0.28}, status="deprecated"),
}}}
print(json.dumps(sorted(item["id"] for item in mod.discover(catalog, "opencode-go"))))
`)
  assert.equal(run.status, 0, run.stderr)
  assert.deepEqual(JSON.parse(run.stdout), ["opencode-go/beam-free", "opencode-go/glm-5.3-flash"])
})

test("a partial model resumes ahead of new candidates", () => {
  const run = ranking(`
new = [{"id": "opencode/beam-free"}, {"id": "opencode/flint-40b-free"}]
print(json.dumps(mod.choose_candidate(new, "opencode", "", {"opencode/flint-40b-free"})["id"]))
`)
  assert.equal(run.status, 0, run.stderr)
  assert.equal(JSON.parse(run.stdout), "opencode/flint-40b-free")
})

test("saved task results survive a quota stop and complete a later run", () => {
  const run = ranking(
    `
import argparse, pathlib, tempfile
tmp = pathlib.Path(tempfile.mkdtemp())
corpus = tmp / "corpus.json"
corpus.write_text(json.dumps({"task_ids": ["a", "b", "c"], "task_digests": {"a": "da", "b": "db", "c": "dc"}}))
model = "opencode/beam-free"
def task(tid, status="valid", score=1.0):
    return {"task_id": tid, "passed": score > 0, "score": score, "max_score": 1.0, "measurement_status": status}
def summary(name, tasks):
    path = tmp / name
    path.write_text(json.dumps({"run_id": name, "tasks": tasks}))
    return path
def ns(**kw):
    return argparse.Namespace(progress_dir=tmp / "p", model=model, corpus_json=corpus, revision="r1", **kw)
# Day 1: a is valid, b is cut off by the quota.
mod.progress_save(ns(summary=summary("d1.json", [task("a"), task("b", "invalid", 0)])))
todo = mod.fresh_progress(tmp / "p", model, mod.load_corpus(corpus), "r1")
assert list(todo) == ["a"], todo
# A changed task digest invalidates only that task.
corpus.write_text(json.dumps({"task_ids": ["a", "b", "c"], "task_digests": {"a": "NEW", "b": "db", "c": "dc"}}))
assert mod.fresh_progress(tmp / "p", model, mod.load_corpus(corpus), "r1") == {}
corpus.write_text(json.dumps({"task_ids": ["a", "b", "c"], "task_digests": {"a": "da", "b": "db", "c": "dc"}}))
# A different NixBench revision invalidates everything.
assert mod.fresh_progress(tmp / "p", model, mod.load_corpus(corpus), "r2") == {}
# Day 2: b and c finish; record merges all three.
mod.progress_save(ns(summary=summary("d2.json", [task("b", score=0.5), task("c")])))
out = tmp / "ranking.json"
mod.record(argparse.Namespace(file=out, model=model, summary=summary("d2.json", [task("b", score=0.5), task("c")]),
    expected_tasks=3, agent_version="x", progress_dir=tmp / "p", corpus_json=corpus, revision="r1"))
entry = json.loads(out.read_text())["models"][0]
print(json.dumps([entry["score"], entry["maxScore"], entry["taskCount"], entry["passedTasks"]]))
`,
  )
  assert.equal(run.status, 0, run.stderr)
  assert.deepEqual(JSON.parse(run.stdout.trim().split("\n").pop() ?? ""), [2.5, 3, 3, 3])
})
