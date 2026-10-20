// Routes every opencode session onto the best free model it can reach, and moves it on when that one runs out.
import fs from "node:fs"
import path from "node:path"
import { spawn } from "node:child_process"
import type { Plugin } from "@opencode/plugin"
import type { SessionPrompt, SessionRetry } from "@opencode/plugin/promise/session"
import {
  DIR,
  isVirtual,
  isNixVirtual,
  readJSON,
  writeJSONAtomic,
  loadConfig,
  loadPins,
  decodeModel,
  decodeState,
  mergeState,
  rankedIds,
  orderCandidates,
  pick,
  classifyFailure,
  coolingUntil,
  split,
  list,
  asObject,
  optString,
  errorText,
} from "../lib.mts"
import type { Config, Failover, ModelInfo, State } from "../lib.mts"

type Json = string | number | boolean | null | Json[] | { [key: string]: Json }

// What the router reads of a session.
export interface SessionLike {
  model?: { providerID: string; id: string } | undefined
  parentID?: string | undefined
  fork?: { sessionID: string } | undefined
  metadata?: { [key: string]: Json } | undefined
}

interface Registration {
  dispose(): Promise<void>
}

// The part of opencode's plugin context the router uses. Tests pass a fake; opencode's own context
// has to fit it too, which `satisfies Plugin.Plugin` at the end of this file checks.
export interface RouterContext {
  model: {
    list(): Promise<{ data: unknown[] }>
    default(): Promise<{ data: { providerID: string; id: string } | null }>
  }
  session: {
    get(input: { sessionID: string }): Promise<SessionLike>
    switchModel(input: { sessionID: string; model: { providerID: string; id: string } }): Promise<void>
    update(input: { sessionID: string; metadata: { [key: string]: Json } }): Promise<void>
    hook: {
      (name: "prompt", callback: (e: SessionPrompt) => Promise<void> | void): Promise<Registration>
      (name: "retry", callback: (e: SessionRetry) => Promise<void> | void): Promise<Registration>
    }
  }
  event: {
    subscribe(options: { signal: AbortSignal }): AsyncIterable<{ type: string; data?: unknown }>
  }
}

// Per-session bookkeeping for the execution that is running. `attemptAtSwitch`: opencode's attempt
// counter never restarts within an execution (its first failure is attempt 2), so failures are
// counted from the attempt of the last switch. `left`: models this execution already switched away
// from (or free/auto, once picked for), with how many retries a request still on one has been given.
// `chain`: retry handling runs one at a time per session.
interface Turn {
  busy: boolean
  switches: number
  attemptAtSwitch: number
  left: Map<string, number>
  chain: Promise<void>
}

interface TurnBook {
  of(sid: string): Turn
  begin(sid: string): void
  drop(sid: string): void
  idleAll(): void
}

interface Chooser {
  choose(sid: string, exclude?: string[], mode?: "general" | "nixbench"): Promise<string | undefined>
  setSticky(sid: string, id: string): void
}

const STATE = path.join(DIR, "state.json")
const RANKING = path.join(DIR, "ranking.json")
const NIXBENCH_RANKING = path.join(DIR, "nixbench-ranking.json")
const NIXBENCH_URL = process.env.FREE_ROUTER_NIXBENCH_URL || "https://raw.githubusercontent.com/dweee/opencode-free-router/master/data/nixbench-ranking.json"
const LOG = path.join(DIR, "router.log")
const LIVE_TTL = 5 * 60e3
const NIXBENCH_TTL = 6 * 3600e3

// The log is created 0600, but one from an older install or a rotation keeps the mode it had.
function secureLog() {
  for (const f of [LOG, `${LOG}.1`]) {
    try {
      fs.chmodSync(f, 0o600)
    } catch {
      // not there, or not ours to change
    }
  }
}

function log(msg: string) {
  try {
    const st = fs.statSync(LOG, { throwIfNoEntry: false })
    if (st && st.size > 1 << 20) {
      fs.renameSync(LOG, `${LOG}.1`)
      secureLog()
    }
    fs.appendFileSync(LOG, `${new Date().toISOString()} ${msg}\n`, { mode: 0o600 })
  } catch {
    // a log that cannot be written is not worth crashing over
  }
}

const readState = () => decodeState(readJSON(STATE))

// Merge-on-write: other opencode processes share state.json.
function update(fn: (mine: State) => void) {
  const mine: State = { cooldowns: {}, sessions: {} }
  fn(mine)
  writeJSONAtomic(STATE, mergeState(readState(), mine))
}

function which(bin: string) {
  for (const dir of (process.env.PATH ?? "").split(path.delimiter)) {
    const p = path.join(dir, bin)
    try {
      fs.accessSync(p, fs.constants.X_OK)
      return p
    } catch {
      // not this directory
    }
  }
  return undefined
}

// Re-rank in the background when ranking.json is missing or stale. process.execPath is the
// opencode binary here, so run rank.mts with node (or bun) from PATH. One run at a time via a
// lock file; a lock older than 10 minutes is treated as abandoned.
function maybeRefresh(cfg: Config) {
  if (process.env.FREE_ROUTER_RANKING) return
  const st = fs.statSync(RANKING, { throwIfNoEntry: false })
  if (st && Date.now() - st.mtimeMs < cfg.refreshHours * 3600e3) return
  const lock = path.join(DIR, "rank.lock")
  try {
    fs.writeFileSync(lock, String(process.pid), { flag: "wx" })
  } catch {
    const l = fs.statSync(lock, { throwIfNoEntry: false })
    if (l && Date.now() - l.mtimeMs < 10 * 60e3) return
    fs.writeFileSync(lock, String(process.pid))
  }
  const runtime = which("node") ?? which("bun")
  if (!runtime) {
    fs.rmSync(lock, { force: true })
    log("WARN ranking is stale but neither node nor bun is on PATH; run rank.mts manually")
    return
  }
  const out = fs.openSync(LOG, "a", 0o600)
  const child = spawn(runtime, [path.join(DIR, "rank.mts"), "--quiet", "--lock", lock], {
    detached: true,
    stdio: ["ignore", out, out],
    env: { ...process.env, FREE_ROUTER_DIR: DIR },
  })
  child.unref()
  fs.closeSync(out)
  log(`refreshing ranking in the background (pid ${child.pid})`)
}

// The ids that are live, free right now and eligible, best first. Both inputs are cached: the
// ranking file until its mtime moves, opencode's model list for LIVE_TTL. The runtime free
// check guards a ranking that has gone stale.
function createCandidates(ctx: RouterContext, cfg: Config) {
  let ranking = { mtime: 0, ids: new Array<string>() }
  let live = { at: 0, models: new Array<ModelInfo>() }

  function getRanking() {
    const st = fs.statSync(RANKING, { throwIfNoEntry: false })
    if (st && st.mtimeMs !== ranking.mtime) ranking = { mtime: st.mtimeMs, ids: rankedIds(readJSON(RANKING)) }
    return ranking.ids
  }

  async function getLive() {
    if (Date.now() - live.at < LIVE_TTL && live.models.length) return live.models
    live = { at: Date.now(), models: list((await ctx.model.list()).data, decodeModel) }
    return live.models
  }

  async function getNixbenchRanking() {
    const st = fs.statSync(NIXBENCH_RANKING, { throwIfNoEntry: false })
    const cached = asObject(readJSON(NIXBENCH_RANKING))
    const hasCatalog = typeof asObject(cached.catalog).retrievedAt === "string"
    if (!st || !hasCatalog || Date.now() - st.mtimeMs >= NIXBENCH_TTL) {
      try {
        const response = await fetch(NIXBENCH_URL, { signal: AbortSignal.timeout(15_000) })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const data: unknown = await response.json()
        const document = asObject(data)
        if (document.schemaVersion !== 1 || typeof asObject(document.catalog).retrievedAt !== "string") {
          throw new Error("invalid NixBench ranking document")
        }
        writeJSONAtomic(NIXBENCH_RANKING, data)
      } catch (e) {
        log(`WARN NixBench ranking refresh failed: ${errorText(e).slice(0, 180)}`)
      }
    }
    return rankedIds(readJSON(NIXBENCH_RANKING))
  }

  return async (mode: "general" | "nixbench" = "general") => {
    const ranked = mode === "nixbench" ? await getNixbenchRanking() : getRanking()
    const ordered = orderCandidates(ranked, await getLive(), cfg, loadPins())
    return mode === "nixbench" ? ordered.filter((id) => ranked.includes(id)) : ordered
  }
}

// The model a session sticks to, and the one to move it to.
function createChooser(candidates: (mode?: "general" | "nixbench") => Promise<string[]>): Chooser {
  async function choose(sid: string, exclude: string[] = [], mode: "general" | "nixbench" = "general") {
    const state = readState()
    const ordered = await candidates(mode)
    const sticky = state.sessions[sid]?.model
    if (!exclude.length && sticky && ordered.includes(sticky) && !coolingUntil(state, sticky)) return sticky
    const p = pick(ordered, state, exclude)
    if (p?.allCooling) log(`WARN all free models are cooling down; using the one that frees up first: ${p.id}`)
    return p?.id
  }

  const setSticky = (sid: string, id: string) =>
    update((s) => {
      s.sessions[sid] = { model: id, at: Date.now() }
    })

  return { choose, setSticky }
}

function createTurnBook(): TurnBook {
  const turns = new Map<string, Turn>()
  const fresh = (): Turn => ({ busy: false, switches: 0, attemptAtSwitch: 1, left: new Map(), chain: Promise.resolve() })
  const of = (sid: string) => {
    let t = turns.get(sid)
    if (!t) turns.set(sid, (t = fresh()))
    return t
  }
  return {
    of,
    // Each execution, a queued one included, gets a fresh switch budget.
    begin: (sid) => {
      const t = of(sid)
      Object.assign(t, { busy: true, switches: 0, attemptAtSwitch: 1 })
      t.left.clear()
    },
    drop: (sid) => void turns.delete(sid),
    idleAll: () => {
      for (const t of turns.values()) t.busy = false
    },
  }
}

const idOf = (m: { providerID: string; id: string }) => `${m.providerID}/${m.id}`
const toRef = (id: string) => {
  const { providerID, modelID } = split(id)
  return { providerID, id: modelID }
}
const isFreeAuto = (id: string) => isVirtual(split(id))

// The pick kept in the session's own metadata, which outlives the sticky record in state.json.
const metaPick = (info: SessionLike) => optString(asObject(asObject(info.metadata).freeRouter).pick)

// Which model a session is on and which it is routed to; the two places the pick is kept.
function createSessions(ctx: RouterContext, chooser: Chooser) {
  const stickyOf = (sid: string) => readState().sessions[sid]?.model
  const info = (sid: string) => ctx.session.get({ sessionID: sid })
  const routeMode = (value: SessionLike): "general" | "nixbench" => asObject(asObject(value.metadata).freeRouter).mode === "nixbench" ? "nixbench" : "general"

  // The model a session runs on: its own, else opencode's default.
  async function effectiveModel(info: SessionLike) {
    if (info.model) return idOf(info.model)
    const d = (await ctx.model.default()).data
    return d ? idOf(d) : undefined
  }

  // A deleted parent or fork source is nobody's pick.
  async function originOnPick(origin: string, model: string) {
    if (stickyOf(origin) === model) return true
    try {
      return metaPick(await info(origin)) === model
    } catch (e) {
      log(`session ${origin} lookup failed: ${errorText(e).slice(0, 200)}`)
      return false
    }
  }

  // Whether `model` is the pick of this session's router: its sticky record, else the pick kept in
  // its metadata, else (a child or fork) the pick of the session it came from. A model the user chose
  // with /model differs from the pick, so routing ends there.
  async function onPick(sid: string, model: string, known?: SessionLike) {
    if (stickyOf(sid) === model) return true
    const own = known ?? (await info(sid))
    if (metaPick(own) === model) return true
    for (const origin of [own.parentID, own.fork?.sessionID]) {
      if (origin && (await originOnPick(origin, model))) return true
    }
    return false
  }

  // Move the session to `id` and remember it in both places the pick is kept. A session already on
  // `id` is not switched again: every switch is a line in its history.
  async function switchTo(sid: string, id: string, known?: SessionLike, mode: "general" | "nixbench" = "general") {
    const current = known ?? (await info(sid))
    if (!current.model || idOf(current.model) !== id) await ctx.session.switchModel({ sessionID: sid, model: toRef(id) })
    chooser.setSticky(sid, id)
    const freeRouter = mode === "nixbench" ? { pick: id, mode } : { pick: id }
    if (metaPick(current) !== id || routeMode(current) !== mode) await ctx.session.update({ sessionID: sid, metadata: { ...current.metadata, freeRouter } })
  }

  return { stickyOf, info, effectiveModel, onPick, switchTo, routeMode }
}

type Sessions = ReturnType<typeof createSessions>

// opencode's own retry keeps the model for the whole execution, so a failover switches the session's
// model and tells opencode to retry right away on the new one.
function createFailover(cfg: Config, chooser: Chooser, sessions: Sessions) {
  return async function failover(e: SessionRetry, t: Turn, from: string, c: Failover, message: string) {
    const sid = e.sessionID
    const coolKey = c.scope === "provider" ? `${from.split("/")[0]}/*` : from
    update((s) => {
      s.cooldowns[coolKey] = { until: Date.now() + c.cooldownMs, klass: c.klass, reason: message.slice(0, 200) }
    })
    // Whatever stops the switch from here on, the error surfaces now instead of opencode's own wait.
    e.decision = { retry: false }
    if (t.switches >= cfg.maxSwitchesPerTurn) {
      log(`give up ${sid}: already switched ${t.switches} times this turn; leaving ${from} (${c.klass})`)
      return
    }
    const info = await sessions.info(sid)
    const mode = sessions.routeMode(info)
    const to = await chooser.choose(sid, [from], mode)
    if (!to) {
      log(`no alternative for ${sid}; staying on ${from} (${c.klass})`)
      return
    }
    // The budget is spent and `from` marked left before the switch, so one that stops half-way counts.
    // With autoContinue off nothing may retry on the old model mid-turn: the next message uses the new one.
    t.switches++
    t.attemptAtSwitch = e.attempt
    t.left.set(from, cfg.autoContinue ? 0 : 1)
    // A model the session comes back to is a pick again, not one it has left.
    t.left.delete(to)
    await sessions.switchTo(sid, to, info, mode)
    log(`failover ${sid}: ${from} -> ${to} (${c.klass}: ${message.slice(0, 160)})`)
    if (cfg.autoContinue) e.decision = { retry: true, delay: 0 }
    else log(`switched ${sid} to ${to}; autoContinue off, next message will use it`)
  }
}

function createRouter(cfg: Config, turns: TurnBook, chooser: Chooser, sessions: Sessions) {
  const failover = createFailover(cfg, chooser, sessions)

  async function onPrompt(e: SessionPrompt) {
    const sid = e.sessionID
    const t = turns.of(sid)
    // A steer or queued message while a turn runs: the session is on its pick already.
    if (t.busy) return
    const info = await sessions.info(sid)
    const model = await sessions.effectiveModel(info)
    if (!model || !(isFreeAuto(model) || (await sessions.onPick(sid, model, info)))) return
    const mode = isNixVirtual(split(model)) ? "nixbench" : isFreeAuto(model) ? "general" : sessions.routeMode(info)
    // A fresh turn; also the backstop when the event stream that resets this has died.
    t.switches = 0
    t.attemptAtSwitch = 1
    t.left.clear()
    const sticky = sessions.stickyOf(sid)
    const id = await chooser.choose(sid, [], mode)
    if (!id) {
      log(`ERROR no free model available for ${sid}; free/auto left in place, the request will fail`)
      return
    }
    // The whole model goes, not just its id: a variant picked for free/auto means nothing to a real one.
    await sessions.switchTo(sid, id, info, mode)
    if (sticky !== id) log(`pick ${sid} -> ${id}${sticky ? ` (was ${sticky})` : ""}`)
  }

  async function onRetry(e: SessionRetry) {
    const sid = e.sessionID
    const t = turns.of(sid)
    const from = idOf(e.model)
    // A request still on a model this execution already left (a compaction keeps the model it started
    // with) or on free/auto after its pick: the first one is retried, in case it is the primary request
    // that lost the race to the switch, the next ones are let fail, since retrying only hits the
    // provider that just failed.
    const used = t.left.get(from)
    if (used !== undefined) {
      if (used < 1) t.left.set(from, used + 1)
      e.decision = used < 1 ? { retry: true, delay: 0 } : { retry: false }
      return
    }
    // A request the prompt hook never routed (a command, say): pick now, or fail fast with no model.
    if (isFreeAuto(from)) {
      const mode = isNixVirtual(split(from)) ? "nixbench" : "general"
      const id = await chooser.choose(sid, [], mode)
      if (!id) {
        log(`ERROR no free model available for ${sid}; free/auto left in place, the request will fail`)
        e.decision = { retry: false }
        return
      }
      await sessions.switchTo(sid, id, undefined, mode)
      t.left.set(from, 1)
      log(`pick ${sid} -> ${id}`)
      e.decision = { retry: true, delay: 0 }
      return
    }
    if (!(await sessions.onPick(sid, from))) return
    const { error } = e
    const c = classifyFailure(
      { type: error.type, message: error.message, status: error.status, body: error.response?.body, attempt: Math.max(1, e.attempt - t.attemptAtSwitch), delay: e.decision.retry ? e.decision.delay : 0 },
      cfg,
    )
    if (c.action === "failover") await failover(e, t, from, c, error.message)
    else if (c.action === "wait") log(`retry ${sid} attempt ${e.attempt}: ${error.message.slice(0, 120)} (letting opencode retry)`)
  }

  return { onPrompt, onRetry }
}

// Hooks run inside opencode: whatever they throw is logged, never passed on.
async function guarded(name: string, sid: string, fn: () => Promise<void>) {
  try {
    await fn()
  } catch (e) {
    // The message is cut like every logged provider error; the stack frames say where.
    const frames = e instanceof Error ? (e.stack ?? "").split("\n").filter((l) => /^\s+at /.test(l)).slice(0, 3) : []
    log(`${name} hook failed ${sid}: ${errorText(e).slice(0, 200)}${frames.map((l) => `\n${l}`).join("")}`)
  }
}

// A retry waits for the one before it on the same session, but not forever. Once the wait is up it
// runs alongside a previous retry that is still hung: accepted, against waiting for it forever.
const RETRY_WAIT = 30e3
function after(previous: Promise<void>) {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, RETRY_WAIT)
    timer.unref()
    void previous.then(() => {
      clearTimeout(timer)
      resolve()
    })
  })
}

const plugin = {
  id: "free-router",
  async setup(ctx: RouterContext) {
    const cfg = loadConfig()
    secureLog()
    try {
      maybeRefresh(cfg)
    } catch (e) {
      log(`refresh check failed: ${errorText(e)}`)
    }
    const chooser = createChooser(createCandidates(ctx, cfg))
    const turns = createTurnBook()
    const { onPrompt, onRetry } = createRouter(cfg, turns, chooser, createSessions(ctx, chooser))

    const registrations = [
      await ctx.session.hook("prompt", (e) => guarded("prompt", e.sessionID, () => onPrompt(e))),
      await ctx.session.hook("retry", async (e) => {
        const t = turns.of(e.sessionID)
        const run = after(t.chain).then(() => guarded("retry", e.sessionID, () => onRetry(e)))
        t.chain = run
        await run
      }),
    ]

    // Whether a turn is running comes from events. If the stream ends, every session falls back to
    // "not busy" and the next prompt starts a fresh turn.
    const events = new AbortController()
    void (async () => {
      try {
        for await (const ev of ctx.event.subscribe({ signal: events.signal })) {
          try {
            const sid = optString(asObject(ev.data).sessionID)
            if (sid === undefined) continue
            if (ev.type === "session.deleted") turns.drop(sid)
            else if (ev.type === "session.execution.started") turns.begin(sid)
            else if (/^session\.(execution\.(succeeded|failed|interrupted)|idle)$/.test(ev.type)) turns.of(sid).busy = false
          } catch (e) {
            // Never the event itself: it can carry prompts and provider errors.
            log(`event ${ev.type} failed: ${errorText(e).slice(0, 200)}`)
          }
        }
      } catch (e) {
        if (!events.signal.aborted) log(`event stream failed: ${errorText(e).slice(0, 200)}`)
      } finally {
        turns.idleAll()
      }
    })()

    return async () => {
      events.abort()
      await Promise.all(registrations.map((r) => r.dispose()))
    }
  },
} satisfies Plugin.Plugin

export default plugin
