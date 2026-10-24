// The NixBench shell and the OpenCode process have to agree on one worktree path.
// Remounting it at /workspace makes file tools and shell commands miss each other,
// and OpenCode denies /workspace as an external directory.
import { test } from "node:test"
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const SHELL = fileURLToPath(new URL("../scripts/nixbench-safe-shell.sh", import.meta.url))
const AGENT = fileURLToPath(new URL("../scripts/nixbench-agent.sh", import.meta.url))
const TEMPLATE = fileURLToPath(new URL("../templates/nixbench-opencode.json", import.meta.url))

function tempDir(prefix: string) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix))
}

test("sandbox keeps the host worktree path instead of remounting /workspace", () => {
  const root = tempDir("nixbench-shell-")
  // The guard accepts only the isolated /tmp prefix NixBench creates.
  const isolated = path.join("/tmp", `nixbench-isolated-${process.pid}`, "work")
  fs.mkdirSync(isolated, { recursive: true })
  const store = path.join(root, "store")
  fs.mkdirSync(store)
  const recorded = path.join(root, "bwrap-args")
  const bwrap = path.join(root, "bwrap")
  fs.writeFileSync(bwrap, `#!/bin/sh\nprintf '%s\\n' "$@" > ${JSON.stringify(recorded)}\n`)
  fs.chmodSync(bwrap, 0o755)
  const prompt = path.join(isolated, "NIXBENCH_PROMPT.md")
  fs.writeFileSync(prompt, "task\n")

  const run = spawnSync(SHELL, ["-c", "true"], {
    encoding: "utf8",
    env: {
      PATH: "/usr/bin:/bin",
      NIXBENCH_WORKDIR: isolated,
      NIXBENCH_BWRAP: bwrap,
      NIXBENCH_NIX_STORE: store,
      NIXBENCH_PROMPT: prompt,
    },
  })
  assert.equal(run.status, 0, run.stderr)
  const args = fs.readFileSync(recorded, "utf8").split("\n")
  const parentDir = args.findIndex((arg, index) => arg === "--dir" && args[index + 1] === path.dirname(isolated))
  assert.notEqual(parentDir, -1)
  const bind = args.indexOf("--bind")
  const tmpfs = args.findIndex((arg, index) => arg === "--tmpfs" && args[index + 1] === "/tmp")
  assert.ok(parentDir > tmpfs)
  assert.ok(parentDir < bind)
  assert.deepEqual(args.slice(bind, bind + 3), ["--bind", isolated, isolated])
  assert.equal(args[args.indexOf("--chdir") + 1], isolated)
  assert.equal(args[args.indexOf("NIXBENCH_WORKDIR") + 1], isolated)
  assert.equal(args[args.indexOf("NIXBENCH_PROMPT") + 1], path.join(isolated, "NIXBENCH_PROMPT.md"))
  assert.equal(args.includes("/workspace"), false)
  fs.rmSync(isolated, { recursive: true, force: true })
  fs.rmSync(root, { recursive: true, force: true })
})

test("nixbench openrouter stays up through silent reasoning until the task limit", () => {
  const config = JSON.parse(fs.readFileSync(TEMPLATE, "utf8")) as {
    providers: { openrouter: { settings: { chunkTimeout: unknown; headerTimeout: unknown } } }
    agents: { nixbench: { system: string } }
  }
  assert.equal(config.providers.openrouter.settings.chunkTimeout, false)
  assert.equal(config.providers.openrouter.settings.headerTimeout, 900000)
  assert.match(config.agents.nixbench.system, /real absolute path/)
})

test("an upstream idle timeout is retried once and any other agent failure is not", () => {
  const root = tempDir("nixbench-agent-")
  const configDir = path.join(root, "config")
  const workdir = path.join(root, "work")
  fs.mkdirSync(configDir)
  fs.mkdirSync(workdir)
  fs.copyFileSync(TEMPLATE, path.join(configDir, "opencode.json"))
  const prompt = path.join(workdir, "NIXBENCH_PROMPT.md")
  fs.writeFileSync(prompt, "fix the module\n")
  const count = path.join(root, "count")
  fs.writeFileSync(count, "0\n")
  const fake = path.join(root, "opencode")
  fs.writeFileSync(
    fake,
    `#!/bin/sh
n=$(cat ${JSON.stringify(count)})
printf '%s\n' "$((n + 1))" > ${JSON.stringify(count)}
if [ "$n" -eq 0 ]; then
  echo "Error: Upstream idle timeout exceeded" >&2
  exit 1
fi
echo completed
exit 0
`,
  )
  fs.chmodSync(fake, 0o755)

  const env = {
    PATH: "/usr/bin:/bin",
    NIXBENCH_PROMPT: prompt,
    NIXBENCH_MODEL_ID: "openrouter/example/model:free",
    NIXBENCH_WORKDIR: workdir,
    OPENCODE_CONFIG_DIR: configDir,
    OPENCODE_BIN: fake,
  }
  const retried = spawnSync(AGENT, [], { encoding: "utf8", env })
  assert.equal(retried.status, 0, retried.stderr)
  assert.match(retried.stderr, /retrying after upstream idle timeout/)
  assert.equal(fs.readFileSync(count, "utf8").trim(), "2")

  fs.writeFileSync(count, "0\n")
  fs.writeFileSync(
    fake,
    `#!/bin/sh
n=$(cat ${JSON.stringify(count)})
printf '%s\n' "$((n + 1))" > ${JSON.stringify(count)}
echo "Error: model rejected the request" >&2
exit 1
`,
  )
  const rejected = spawnSync(AGENT, [], { encoding: "utf8", env })
  assert.equal(rejected.status, 1)
  assert.equal(fs.readFileSync(count, "utf8").trim(), "1")
  fs.rmSync(root, { recursive: true, force: true })
})
