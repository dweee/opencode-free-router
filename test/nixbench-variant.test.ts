import { test } from "node:test"
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const variant = fileURLToPath(new URL("../scripts/nixbench-variant.py", import.meta.url))
const template = fileURLToPath(new URL("../templates/nixbench-opencode.json", import.meta.url))

function apply(name: string) {
  const run = spawnSync(
    "python3",
    [
      "-c",
      `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("variant", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
config = json.load(open(sys.argv[2]))
print(json.dumps(mod.apply_variant(config, sys.argv[3])))
`,
      variant,
      template,
      name,
    ],
    { encoding: "utf8" },
  )
  assert.equal(run.status, 0, run.stderr)
  return JSON.parse(run.stdout)
}

test("mcp variant exposes both nixos tools and their source guide", () => {
  const config = apply("mcp")
  const system = config.agents.nixbench.system
  assert.match(system, /nixos_nix/)
  assert.match(system, /source home-manager/)
  assert.match(system, /source darwin/)
  assert.match(system, /source noogle/)
  assert.match(system, /nixos_nix_versions/)
  assert.equal(config.mcp.servers.nixos.codemode, false)
  assert.deepEqual(
    config.agents.nixbench.permissions.filter((rule: { action: string }) => rule.action.startsWith("nixos_")),
    [
      { action: "nixos_nix", resource: "*", effect: "allow" },
      { action: "nixos_nix_versions", resource: "*", effect: "allow" },
    ],
  )
  assert.equal(system.includes("## Nix guidance"), false)
})

test("skills variant inlines both skill bodies without enabling the MCP", () => {
  const config = apply("skills")
  const system = config.agents.nixbench.system
  assert.match(system, /Treat `flake\.nix` and `flake\.lock`/)
  assert.match(system, /mkEnableOption/)
  assert.equal(config.mcp, undefined)
  assert.ok(config.skills[0].endsWith("/benchmark/skills"))
  assert.ok(config.agents.nixbench.permissions.some((rule: { action: string }) => rule.action === "skill"))
})

test("both variant keeps the skills and the full lookup guide", () => {
  const config = apply("both")
  const system = config.agents.nixbench.system
  assert.match(system, /## Nix guidance/)
  assert.match(system, /## Nix lookup tools/)
  assert.equal(config.mcp.servers.nixos.command[0], "nix")
})

test("applying a variant twice does not duplicate its guidance", () => {
  const once = apply("both")
  const run = spawnSync(
    "python3",
    [
      "-c",
      `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("variant", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
config = json.loads(sys.stdin.read())
print(json.dumps(mod.apply_variant(config, "both")))
`,
      variant,
    ],
    { encoding: "utf8", input: JSON.stringify(once) },
  )
  assert.equal(run.status, 0, run.stderr)
  const twice = JSON.parse(run.stdout)
  assert.equal(twice.agents.nixbench.system, once.agents.nixbench.system)
})
