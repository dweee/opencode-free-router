#!/usr/bin/env bash
set -euo pipefail
: "${NIXBENCH_PROMPT:?NIXBENCH_PROMPT is required}"
: "${NIXBENCH_MODEL_ID:?NIXBENCH_MODEL_ID is required}"

prompt=$(<"$NIXBENCH_PROMPT")
cd "$NIXBENCH_WORKDIR"
exec opencode run --standalone --agent nixbench --model "$NIXBENCH_MODEL_ID" "$prompt"
