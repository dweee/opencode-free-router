#!/usr/bin/env python3
"""Run one night of the NixBench guidance queue.

A systemd user timer starts this at 02:00 Europe/London, an hour after the
OpenRouter free-model counter resets at 00:00 UTC. Finished runs are skipped.
A first-task failure stays queued and the next model is tried if the daily
allowance is still there. A run that gets past the first task ends the night
and is retried the next morning.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NIXBENCH_REV = "290eb8c18d4a0833fcc3746662ff08766102232c"
NIXBENCH_REPO = "https://github.com/maximilianpw/nixbench.git"
STATE_DIR = Path.home() / ".local" / "state" / "nixbench-guidance-compare"


def load_compare():
    spec = importlib.util.spec_from_file_location("nixbench_compare", ROOT / "scripts" / "nixbench-compare.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load nixbench-compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def revision(path: Path) -> str | None:
    if not path.joinpath(".git").is_dir():
        return None
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def ensure_nixbench(state_dir: Path = STATE_DIR) -> Path:
    candidates = [
        Path(os.environ["NIXBENCH_ROOT"]) if os.environ.get("NIXBENCH_ROOT") else None,
        Path("/tmp/nixbench-src"),
        state_dir / "nixbench-src",
    ]
    for candidate in candidates:
        if candidate is not None and revision(candidate) == NIXBENCH_REV:
            return candidate
    destination = state_dir / "nixbench-src"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.joinpath(".git").is_dir():
        subprocess.check_call(["git", "clone", NIXBENCH_REPO, str(destination)])
    subprocess.check_call(["git", "-C", str(destination), "fetch", "--depth", "1", "origin", NIXBENCH_REV])
    subprocess.check_call(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    if revision(destination) != NIXBENCH_REV:
        raise RuntimeError(f"{destination} is {revision(destination)}, expected {NIXBENCH_REV}")
    return destination


def main() -> int:
    compare = load_compare()
    checkout = ensure_nixbench(compare.STATE_DIR)
    os.environ["NIXBENCH_ROOT"] = str(checkout)
    os.environ.setdefault("NIXBENCH_COMPARE_JOBS", "1")
    return compare.daily_main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"nixbench-daily: {error}", file=sys.stderr)
        raise SystemExit(1)
