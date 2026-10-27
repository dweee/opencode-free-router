#!/usr/bin/env bash
set -euo pipefail

: "${NIXBENCH_WORKDIR:?NIXBENCH_WORKDIR is required}"
case "$NIXBENCH_WORKDIR" in
  /tmp/nixbench-isolated-*/work) ;;
  *) echo "Refusing shell outside a NixBench isolated worktree" >&2; exit 126 ;;
esac
[ -d "$NIXBENCH_WORKDIR" ] || { echo "NixBench worktree does not exist" >&2; exit 126; }
nix_store=${NIXBENCH_NIX_STORE:-/nix/store}
[ -d "$nix_store" ] || { echo "Nix store is unavailable" >&2; exit 126; }
# OpenCode edits this host path. Remounting it at /workspace makes shell
# commands address a directory OpenCode denies as an external directory.
work_parent=$(dirname "$NIXBENCH_WORKDIR")
bwrap_bin="${NIXBENCH_BWRAP:-/usr/bin/bwrap}"
[ -x "$bwrap_bin" ] || { echo "bubblewrap is unavailable" >&2; exit 126; }

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
  --dir "$work_parent"
  --bind "$NIXBENCH_WORKDIR" "$NIXBENCH_WORKDIR"
  --ro-bind "$nix_store" "$nix_store"
  --ro-bind /usr /usr
  --ro-bind /bin /bin
)
for path in /lib /lib64; do
  if [ -e "$path" ]; then args+=(--ro-bind "$path" "$path"); fi
done
if [ -d /nix/var/nix/profiles ]; then
  args+=(--ro-bind /nix/var/nix/profiles /nix/var/nix/profiles)
fi
inner_shell=/bin/bash
if [ ! -x /bin/bash ]; then
  [ -x /bin/sh ] || { echo "shell is unavailable" >&2; exit 126; }
  inner_shell=/bin/sh
  if [ -d /run/current-system/sw/bin ]; then
    args+=(--ro-bind /run/current-system/sw /run/current-system/sw)
    safe_path="/run/current-system/sw/bin:${safe_path}"
  fi
fi
if [ "${NIXBENCH_NIX_DAEMON:-}" = "1" ] && [ -S /nix/var/nix/daemon-socket/socket ]; then
  args+=(--bind /nix/var/nix/daemon-socket /nix/var/nix/daemon-socket)
  if [ -d /etc/nix ]; then args+=(--ro-bind /etc/nix /etc/nix); fi
  nix_remote=daemon
fi

args+=(
  --clearenv
  --setenv HOME /home/agent
  --setenv TMPDIR /tmp
  --setenv PATH "$safe_path"
  --setenv NIXBENCH_TASK_ID "${NIXBENCH_TASK_ID:-}"
  --setenv NIXBENCH_WORKDIR "$NIXBENCH_WORKDIR"
  --setenv NIXBENCH_PROMPT "$NIXBENCH_WORKDIR/NIXBENCH_PROMPT.md"
)
if [ -n "${nix_remote:-}" ]; then args+=(--setenv NIX_REMOTE "$nix_remote"); fi
if [ -n "${NIX_PATH:-}" ]; then args+=(--setenv NIX_PATH "$NIX_PATH"); fi
args+=(--chdir "$NIXBENCH_WORKDIR" -- "$inner_shell" "$@")
exec "$bwrap_bin" "${args[@]}"
