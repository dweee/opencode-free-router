# Changelog

Add entries under `## Unreleased`. The next push to `master` that changes shipped code cuts a
release: the release workflow moves them under the new version, tags it and publishes the GitHub
release. A `[minor]` or `[major]` marker in a commit subject picks the bump; otherwise it is a patch.

## Unreleased

## 0.1.4 — 2026-10-21

- Sandbox NixBench shell while enabling local checks
- Allow NixBench generated prompt reads
- Use explicit OpenRouter-compatible provider for NixBench
- Allow slower free-model NixBench runs
- Register newly free models for OpenCode screening

## 0.1.3 — 2026-10-19

- **Breaking: opencode 2 only.** The plugin is ported to opencode 2's plugin API; opencode 1.x is no
  longer supported and `install.sh` refuses it. After upgrading opencode, **re-run `install.sh`**: it
  installs the plugin as `plugin/index.ts`, removes the old `plugin.ts`, and rewrites the one-line
  `"plugin": ["./plugin.ts"]` of an existing `free.jsonc` to `"plugins": ["./plugin"]`. A multi-line
  entry is left alone and the install stops with the edit to make. The template now uses opencode 2's
  config keys (`providers`, `package`, `settings`, `capabilities`); an existing `free.jsonc` with the
  old provider block keeps working.
- Failover is now done inside opencode's own retry: the plugin switches the session's model and
  opencode retries the request on it at once, in the same turn. The abort / wait-for-idle / "continue"
  prompt, the status polling and the bb queued message are gone, and so is the `[free-router]` message
  in the conversation. `autoContinue: false` now means the turn fails and the next message uses the
  new model. When the switch budget (`maxSwitchesPerTurn`) is spent or no model is left, the provider's
  error is shown at once instead of waiting out a long retry.
- A session is routed while it is on `free/auto` or on the model the router picked for it (kept in the
  session's metadata as well as in `state.json`); choosing another model with `/model` ends routing
  for that session, `/model free/auto` brings it back. Child sessions and forks follow the pick of
  the session they came from.
- `opencode-free` runs every command that can with its own server (`--standalone`), since opencode 2's
  shared background service ignores `OPENCODE_CONFIG`. It refuses the `service` command, `serve
  --service`, an unquoted `service` word in a message, and the commands that manage or may
  auto-start the service (`debug`, `pair`, `mcp`, `plugin`, `upgrade`, `update`, `uninstall`): run
  those as plain `opencode`, so the service never comes up with the router's config and keys.
- The ranker lists models with `opencode api --standalone model.list`. A model counts as free only if
  every one of its price tiers is zero, and disabled models are skipped. `cache/candidates.json` no
  longer keeps headers, request bodies or provider settings.
- `router.log` and `state.json` keep up to 200 characters of a provider's error message, never its
  response body (SECURITY.md).
- To go back to opencode 1.x, first look at `free.jsonc` in the install directory. With a `"provider"`
  block (the old format) whose plugin line this version's `install.sh` rewrote, replace
  `"plugins": ["./plugin"]` with `"plugin": ["./plugin.ts"]`. With a `"providers"` block it was made
  from this version's template with opencode 2 keys: move it aside (`mv "$dir/free.jsonc"
  "$dir/free.jsonc.v2"`); v0.1.2's `install.sh` then creates a v1 one, and you carry any provider or
  model settings of your own over by hand. Either way, run the `install.sh` of v0.1.2 (this version
  deleted `plugin.ts`), then `rm -f "$dir/plugin/index.ts"; rmdir "$dir/plugin"` in the install
  directory.

## 0.1.2 — 2026-09-30

- Split the plugin's server closure into named factories

## 0.1.1 — 2026-09-28

- The code is now TypeScript, type-checked by TypeScript 7 in strict mode, and still ships as source
  with no build step: opencode loads `plugin.ts`, and Node or Bun runs `rank.mts`. **Node 22.18 or
  newer is now required** (Bun works as before); `install.sh` refuses an older `node` on `PATH`,
  since the plugin starts the ranker with it.
- Upgrading with `install.sh` replaces `plugin.js`, `lib.mjs` and `rank.mjs` with the new files and
  points the `plugin` entry of an existing `free.jsonc` at `./plugin.ts`. Nothing else in your files
  changes.
- `config.json`, `pins.json`, `state.json`, the ranking and every API response are now checked field
  by field when read. A value of the wrong type in `config.json` keeps its default, and malformed
  entries in `pins.json` are skipped, where before they could break a ranking run.

- Four more free providers on the `freeTier` allowlist, each checked against the provider's own terms
  rather than a price of 0: Z.AI and Zhipu AI (the free GLM Flash models, same key for both
  endpoints), ModelScope (all of its inference is free, with a 2,000 requests a day quota) and
  Mistral (Devstral Small, Codestral and Mistral Small on the free Experiment plan, matched by name
  since the catalog prices them). `providers.env.example` and the README list their keys and caveats.
  Cloudflare Workers AI, Vercel AI Gateway, Hugging Face and the OpenRouter clone gateways were left
  out on purpose, and the README says why.
- A new test keeps `templates/config.example.json` in step with the shipped defaults.
- A `402 Payment Required` now fails over like a removed model (24-hour cooldown) instead of being
  ignored, so a free-plan account that asks for a model it cannot pay for moves on instead of
  retrying a request that can never succeed.
- Tests now run `rank.mjs` as a CLI (stubbed network, fake `opencode`) and `plugin.js` against a
  fake opencode client, bringing line coverage of `src/` to 100%. No behavior change; an
  unreachable API-key check in `rank.mjs` was removed.
- The bb agent registered by `install.sh --bb` now lists its model with `opencode-free
  --list-models`, which prints `free/auto` without starting opencode. Listing it through opencode
  (an ACP probe, or `opencode models free`) sometimes failed, for example with "database is locked"
  while other opencode processes were running, and bb then showed only "Agent default" until a
  later refresh happened to succeed. Re-registering keeps fields you set on the entry by hand, such
  as `icon`, and a failed registration no longer prints the other agents' env values.

## 0.1.0 — 2026-09-25

First public release.

- `free/auto` model for opencode: the plugin swaps it for the best-ranked free model and keeps it
  for the whole session.
- Ranking from LMArena (agent, webdev, text) and Artificial Analysis (coding, agentic,
  intelligence), with stealth-model detection via OpenRouter and `pins.json` overrides.
- Failover on quota, rate limit, removal or refusal: cooldown, next-ranked model, automatic
  continue (a visible thread message under bb).
- Free providers are an explicit allowlist (Zen, OpenRouter and NVIDIA 0-priced models; Groq,
  Cerebras and Google free tiers). Subscription plans that report price 0 are never used.
- The plugin refreshes the ranking itself when it is older than 6 hours.
- `install.sh` / `uninstall.sh`, the `opencode-free` wrapper, and optional bb agent
  registration (`--bb`).
