#!/usr/bin/env bash
set -euo pipefail
: "${NIXBENCH_PROMPT:?NIXBENCH_PROMPT is required}"
: "${NIXBENCH_MODEL_ID:?NIXBENCH_MODEL_ID is required}"

prompt=$(<"$NIXBENCH_PROMPT")
exec opencode run --standalone --dir "$NIXBENCH_WORKDIR" --agent nixbench --model "$NIXBENCH_MODEL_ID" "$prompt"
