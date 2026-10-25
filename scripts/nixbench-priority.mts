// Order a NixBench candidate list by the agent and coding leaderboards.
// Reads the models.dev rows on stdin and writes Scored rows, best first, on stdout.
import {
  asObject,
  entries,
  list,
  optNumber,
  optString,
  percentiles,
  screeningScore,
  errorText,
} from "../src/lib.mts"
import type { ModelInfo, RankedRow, Row } from "../src/lib.mts"

const HF = "https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset&split=latest"
const AA_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
const ARENA: Record<string, [config: string, field: string]> = {
  arena_agent: ["agent", "score"],
  arena_webdev: ["webdev", "rating"],
  arena_text: ["text", "rating"],
}

async function getJSON(url: string, headers: Record<string, string> = {}): Promise<unknown> {
  const res = await fetch(url, { headers, signal: AbortSignal.timeout(30_000) })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url.split("?")[0]}`)
  return res.json()
}

async function arenaBoard(config: string, field: string): Promise<Row[]> {
  const rows: Row[] = []
  for (let offset = 0; ; offset += 100) {
    const data = asObject(await getJSON(`${HF}&config=${config}&offset=${offset}&length=100`))
    for (const { row } of list(data.rows, asObject)) {
      const record = asObject(row)
      if (record.category !== "overall") return rows
      const name = optString(record.model_name)
      if (name !== undefined) rows.push({ name, value: optNumber(record[field]) })
    }
    if (offset + 100 >= (optNumber(data.num_rows_total) ?? 0)) return rows
  }
}

async function aaModels(key: string): Promise<Array<{ name: string; evaluations: Record<string, number> }>> {
  const out: Array<{ name: string; evaluations: Record<string, number> }> = []
  for (let page = 1; page < 20; page++) {
    const data = asObject(await getJSON(`${AA_URL}?page=${page}`, { "x-api-key": key }))
    out.push(
      ...list(data.data, (value) => {
        const model = asObject(value)
        const name = optString(model.slug) ?? optString(model.name)
        return name === undefined ? undefined : { name, evaluations: entries(model.evaluations, optNumber) }
      }),
    )
    if (asObject(data.pagination).has_more !== true) break
  }
  return out
}

function catalogModel(value: unknown): ModelInfo | undefined {
  const row = asObject(value)
  const selector = optString(row.id)
  const slash = selector?.indexOf("/") ?? -1
  if (selector === undefined || slash <= 0) return undefined
  const released = optString(row.releaseDate)
  const parsed = released ? Date.parse(released) : Number.NaN
  return {
    providerID: selector.slice(0, slash),
    id: selector.slice(slash + 1),
    name: optString(row.name),
    release_date: Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString(),
  }
}

const input = await new Promise<string>((resolve, reject) => {
  const chunks: Buffer[] = []
  process.stdin.on("data", (chunk) => chunks.push(chunk))
  process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")))
  process.stdin.on("error", reject)
})

try {
  const models = list(JSON.parse(input), catalogModel)
  const boards: Record<string, RankedRow[]> = {}
  for (const [source, [config, field]] of Object.entries(ARENA)) {
    boards[source] = percentiles(await arenaBoard(config, field))
  }
  const key = process.env.ARTIFICIAL_ANALYSIS_API_KEY
  if (key) {
    const modelsAA = await aaModels(key)
    const rows = (metric: string): Row[] => modelsAA.map((model) => ({ name: model.name, value: model.evaluations[metric] }))
    boards.aa_coding = percentiles(rows("artificial_analysis_coding_index"))
    boards.aa_agentic = percentiles(rows("artificial_analysis_agentic_index"))
    boards.aa_intelligence = percentiles(rows("artificial_analysis_intelligence_index"))
  } else {
    console.error("nixbench-priority: ARTIFICIAL_ANALYSIS_API_KEY not set, Artificial Analysis scores skipped")
  }
  process.stdout.write(JSON.stringify(screeningScore(models, boards)) + "\n")
} catch (error) {
  console.error(`nixbench-priority: ${errorText(error)}`)
  process.exitCode = 1
}
