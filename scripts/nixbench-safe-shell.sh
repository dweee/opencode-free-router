#!/usr/bin/env bash
set -euo pipefail

: "${NIXBENCH_WORKDIR:?NIXBENCH_WORKDIR is required}"
case "$NIXBENCH_WORKDIR" in
  /tmp/nixbench-isolated-*/work) ;;
  *) echo "Refusing shell outside a NixBench isolated worktree" >&2; exit 126 ;;
esac
[ -d "$NIXBENCH_WORKDIR" ] || { echo "NixBench worktree does not exist" >&2; exit 126; }
[ -d /nix/store ] || { echo "Nix store is unavailable" >&2; exit 126; }
[ -x /usr/bin/bwrap ] || { echo "bubblewrap is unavailable" >&2; exit 126; }

safe_path="${NIXBENCH_NIX_BIN_DIR:+$NIXBENCH_NIX_BIN_DIR:}/usr/bin:/bin"
args=(
  --die-with-parent
  --new-session
  --unshare-user
  --unshare-pid
  --as-pid-1
  --unshare-ipc
  --unshare-uts
  --unshare-cgroup
  --unshare-net
  --proc /proc
  --dev /dev
  --tmpfs /tmp
  --tmpfs /home
  --dir /home/agent
  --bind "$NIXBENCH_WORKDIR" /workspace
  --ro-bind /nix/store /nix/store
  --ro-bind /usr /usr
  --ro-bind /bin /bin
)
for path in /lib /lib64; do
  if [ -e "$path" ]; then args+=(--ro-bind "$path" "$path"); fi
done
if [ -d /nix/var/nix/profiles ]; then
  args+=(--ro-bind /nix/var/nix/profiles /nix/var/nix/profiles)
fi

args+=(
  --clearenv
  --setenv HOME /home/agent
  --setenv TMPDIR /tmp
  --setenv PATH "$safe_path"
  --setenv NIXBENCH_TASK_ID "${NIXBENCH_TASK_ID:-}"
  --setenv NIXBENCH_WORKDIR /workspace
  --setenv NIXBENCH_PROMPT /workspace/NIXBENCH_PROMPT.md
)
if [ -n "${NIX_PATH:-}" ]; then args+=(--setenv NIX_PATH "$NIX_PATH"); fi
args+=(--chdir /workspace -- /bin/bash "$@")
exec /usr/bin/bwrap "${args[@]}"
