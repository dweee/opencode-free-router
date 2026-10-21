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
exec "$opencode_bin" run --standalone --agent nixbench --model "$NIXBENCH_MODEL_ID" "$prompt"
