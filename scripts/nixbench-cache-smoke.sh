#!/usr/bin/env bash
set -euo pipefail
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
nixpkgs_rev=$(python3 -c 'import json; print(json.load(open(".nixbench-upstream/vendor/nixpkgs/pin.json"))["rev"])')
nix flake prefetch "github:NixOS/nixpkgs/$nixpkgs_rev"
nixpkgs_path=$(python3 -c 'import json; print(json.load(open(".nixbench-upstream/vendor/nixpkgs/pin.json"))["store_path"])')
export NIX_PATH="nixpkgs=$nixpkgs_path"
# The inner $out belongs to the Nix builder, not this wrapper.
# shellcheck disable=SC2016
if nix-build --no-out-link --expr 'let p=import <nixpkgs> {}; dep=p.runCommand ("nixbench-cache-smoke-dependency-"+builtins.getEnv "GITHUB_RUN_ID") {} "echo retained > $out"; in p.runCommand "nixbench-cache-smoke-failed" {inherit dep;} "exit 19"' > smoke-build.log 2>&1; then
  echo "Expected the dependent fixture to fail" >&2; exit 1
fi
cat smoke-build.log
grep -q 'builder failed with exit code 19' smoke-build.log
python3 scripts/nixbench-cache.py collect --manifest smoke-outputs.txt
dependency=$(grep 'nixbench-cache-smoke-dependency-' smoke-outputs.txt | tail -n 1)
test "$(cat "$dependency")" = retained
! grep -q 'nixbench-cache-smoke-failed' smoke-outputs.txt
