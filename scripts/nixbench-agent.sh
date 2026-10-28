#!/usr/bin/env bash
set -euo pipefail
: "${NIXBENCH_PROMPT:?NIXBENCH_PROMPT is required}"
: "${NIXBENCH_MODEL_ID:?NIXBENCH_MODEL_ID is required}"
: "${OPENCODE_CONFIG_DIR:?OPENCODE_CONFIG_DIR is required}"

prompt=$(<"$NIXBENCH_PROMPT")
python3 - "$OPENCODE_CONFIG_DIR/opencode.json" "$NIXBENCH_WORKDIR" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
workdir = str(Path(sys.argv[2]).resolve(strict=True))
config = json.loads(config_path.read_text())
rules = config["agents"]["nixbench"]["permissions"]
rules.append({"action": "external_directory", "resource": f"{workdir}/*", "effect": "allow"})
config_path.write_text(json.dumps(config, indent=2) + "\n")
PY
cd "$NIXBENCH_WORKDIR"
opencode_bin=${OPENCODE_BIN:-opencode}
# A silent provider gap is an infrastructure failure. NixBench treats any
# non-zero agent exit as an invalid measurement and stops the corpus, so retry
# that one error once before giving up. The 900s task limit still applies.
log=$(mktemp)
# nix-build leaves result* symlinks into /nix/store. NixBench treats any symlink
# that leaves the worktree as a workspace escape and voids the task, so drop
# those build outputs once the agent is done.
drop_store_symlinks() {
  find "$NIXBENCH_WORKDIR" -type l -lname '/nix/store/*' -delete
}
trap 'drop_store_symlinks; rm -f "$log"' EXIT
attempt=1
max_attempts=2
while true; do
  set +e
  "$opencode_bin" run --standalone --agent nixbench --model "$NIXBENCH_MODEL_ID" "$prompt" 2>&1 | tee -a "$log"
  status=${PIPESTATUS[0]}
  set -e
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if [ "$attempt" -ge "$max_attempts" ] || ! grep -q "Upstream idle timeout exceeded" "$log"; then
    exit "$status"
  fi
  attempt=$((attempt + 1))
  printf 'NixBench agent retrying after upstream idle timeout (attempt %s/%s)\n' "$attempt" "$max_attempts" >&2
done
