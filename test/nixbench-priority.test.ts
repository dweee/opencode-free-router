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

test("screening order uses the agent, webdev, and text boards ahead of release date", () => {
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
      "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
      "openrouter/example/web-only:free",
      "openrouter/recursive/flint-v1.0:free",
    ],
  )
  const best = ranked[0]
  const webOnly = ranked[1]
  const missed = ranked[2]
  assert.ok(best)
  assert.ok(webOnly)
  assert.ok(missed)
  assert.equal(best.reason, "benchmarks (arena_agent, arena_webdev, arena_text)")
  assert.equal(webOnly.reason, "benchmarks (arena_webdev)")
  assert.equal(missed.reason.startsWith("unscored"), true)
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
