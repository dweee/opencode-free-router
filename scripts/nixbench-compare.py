#!/usr/bin/env python3
"""Run NixBench guidance variants for every eligible free OpenRouter model.

Variants are mcp, skills, and both. Different models run together. The three
variants of one model do not run at the same time, because free-model rate
limits are per model. Results stay out of data/nixbench-ranking.json.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".local" / "state" / "nixbench-guidance-compare"
SUMMARY_PATH = ROOT / "data" / "nixbench-guidance-comparison.json"
TEMPLATE = ROOT / "templates" / "nixbench-opencode.json"
NIXBENCH_REV = "290eb8c18d4a0833fcc3746662ff08766102232c"
NIXPKGS_STORE = Path("/nix/store/3zvg83mg9aavm9bgh26ydljchply7i25-source")
EXCLUDED = {
    "boot-hook-marker-survives-reboot",
    "firewall-nftables-forwarding-silently-dropped",
    "state-directory-ownership-after-hardening",
}
VARIANTS = ("mcp", "skills", "both")
JOB_TIMEOUT_SECONDS = 8 * 60 * 60


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VARIANT = load_module("nixbench_variant", ROOT / "scripts" / "nixbench-variant.py")
RANKING = load_module("nixbench_ranking", ROOT / "scripts" / "nixbench-ranking.py")


def openrouter_key() -> str:
    database = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    row = connection.execute(
        "SELECT value FROM credential WHERE integration_id='openrouter' AND active=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("OpenRouter credential is not stored in OpenCode")
    value = json.loads(row[0])
    key = value.get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("OpenRouter credential has no key")
    return key


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]")


def prepare_corpus(nixbench: Path) -> Path:
    source = nixbench / "tasks"
    target = STATE_DIR / "corpus"
    tasks = target / "tasks"
    if tasks.is_dir() and any(tasks.iterdir()):
        return target
    if target.exists():
        shutil.rmtree(target)
    tasks.mkdir(parents=True)
    (target / "contracts").mkdir()
    shutil.copy2(nixbench / "corpus.toml", target / "corpus.toml")
    for task in source.iterdir():
        if task.is_dir() and task.name not in EXCLUDED:
            shutil.copytree(task, tasks / task.name)
            contract = nixbench / "contracts" / task.name
            if contract.is_dir():
                shutil.copytree(contract, target / "contracts" / task.name)
    return target


def write_config(job_dir: Path, row: dict[str, Any], variant: str, shell_path: Path) -> None:
    config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    model_id = row["id"].removeprefix("openrouter/")
    config["providers"]["openrouter"]["models"][model_id] = {
        "name": row.get("name", model_id),
        "limit": {"context": row.get("contextTokens", 64000), "output": 8192},
        "capabilities": {"tools": True},
    }
    config["shell"] = str(shell_path)
    VARIANT.apply_variant(config, variant)
    destination = job_dir / "config" / "opencode.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def write_shell(job_dir: Path, bwrap: Path, nix_bin: Path) -> Path:
    path = job_dir / "nixbench-safe-shell"
    path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"export NIXBENCH_BWRAP={json.dumps(str(bwrap))}",
                "export NIXBENCH_NIX_DAEMON=1",
                f"export NIXBENCH_NIX_BIN_DIR={json.dumps(str(nix_bin))}",
                f"export NIX_PATH={json.dumps(f'nixpkgs={NIXPKGS_STORE}')}",
                f"exec {json.dumps(str(ROOT / 'scripts' / 'nixbench-safe-shell.sh'))} \"$@\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def newest_summary(results_dir: Path) -> Path | None:
    found = [path for path in results_dir.glob("*/summary.json") if path.is_file()]
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def summarize_run(summary_path: Path | None) -> dict[str, Any]:
    if summary_path is None or not summary_path.is_file():
        return {"status": "failed", "reason": "no summary"}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    tasks = summary.get("tasks")
    if not isinstance(tasks, list):
        return {"status": "failed", "reason": "summary has no tasks", "summary": str(summary_path)}
    invalid = [
        f"{task.get('task_id')}: {task.get('invalid_reason') or task.get('measurement_status')}"
        for task in tasks
        if isinstance(task, dict) and task.get("measurement_status") != "valid"
    ]
    score = summary.get("score")
    maximum = summary.get("max_score")
    record: dict[str, Any] = {
        "status": "complete" if tasks and not invalid and len(tasks) == 18 else "incomplete",
        "summary": str(summary_path),
        "taskCount": len(tasks),
        "passedTasks": summary.get("passed"),
        "score": score,
        "maxScore": maximum,
        "runId": summary.get("run_id"),
    }
    if isinstance(score, (int, float)) and isinstance(maximum, (int, float)) and maximum:
        record["scoreRate"] = round(score / maximum, 6)
    if invalid:
        record["invalid"] = invalid[:8]
    return record


class Store:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.lock = threading.Lock()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = STATE_DIR / "state.json"

    def update(self, mutate: Any) -> dict[str, Any]:
        with self.lock:
            document = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
            mutate(document)
            text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
            text = redact(text, self.secret)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(self.path)
            SUMMARY_PATH.write_text(text, encoding="utf-8")
            return document


def slug(model_id: str) -> str:
    return model_id.replace("/", "__")


def run_job(
    store: Store,
    job: dict[str, str],
    row: dict[str, Any],
    nixbench: Path,
    corpus: Path,
    bwrap: Path,
    nix_bin: Path,
    opencode: Path,
    secret: str,
) -> None:
    variant = job["variant"]
    model_id = job["model"]
    job_dir = STATE_DIR / "jobs" / variant / slug(model_id)
    results_dir = STATE_DIR / "results" / variant / slug(model_id)
    log_path = job_dir / "bench.log"
    job_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    shell_path = write_shell(job_dir, bwrap, nix_bin)
    write_config(job_dir, row, variant, shell_path)
    bin_dir = job_dir / "bin"
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / "opencode"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(opencode)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(job_dir / "home"),
            "OPENCODE_HOME": str(job_dir / "home"),
            "OPENCODE_CONFIG_DIR": str(job_dir / "config"),
            "OPENROUTER_API_KEY": secret,
            "NIXBENCH_MODEL_ID": model_id,
            "NIXBENCH_BWRAP": str(bwrap),
            "NIXBENCH_NIX_DAEMON": "1",
            "NIXBENCH_NIX_BIN_DIR": str(nix_bin),
            "NIX_PATH": f"nixpkgs={NIXPKGS_STORE}",
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )
    (job_dir / "home").mkdir(exist_ok=True)
    command = [
        sys.executable,
        str(nixbench / "bench.py"),
        "--tasks-dir",
        str(corpus / "tasks"),
        "--results-dir",
        str(results_dir),
        "run-all",
        "--agent-cmd",
        str(ROOT / "scripts" / "nixbench-agent.sh"),
        "--agent-timeout-seconds",
        "900",
        "--model",
        model_id,
        "--kind",
        "opencode",
        "--agent-version",
        subprocess.check_output([str(opencode), "--version"], text=True).strip(),
        "--network",
        "enabled",
        "--label",
        f"OpenCode guidance {variant}: {model_id}",
    ]
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.update(lambda document: document["runs"].__setitem__(job["key"], {**job, "status": "running", "startedAt": started, "log": str(log_path)}))
    print(f"start {variant} {model_id}", flush=True)
    try:
        with log_path.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(
                command,
                cwd=nixbench,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=JOB_TIMEOUT_SECONDS,
                check=False,
            )
        record = summarize_run(newest_summary(results_dir))
        record.update({"exitCode": completed.returncode, "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    except subprocess.TimeoutExpired:
        record = {"status": "failed", "reason": "job timeout", "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    except (OSError, json.JSONDecodeError, RuntimeError) as error:
        record = {"status": "failed", "reason": redact(str(error), secret), "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store.update(lambda document, record=record: document["runs"].__setitem__(job["key"], {**document["runs"].get(job["key"], job), **record}))
    print(f"done {variant} {model_id} {record.get('status')} score={record.get('score')}", flush=True)


def main() -> int:
    if not NIXPKGS_STORE.joinpath("default.nix").is_file():
        raise RuntimeError(f"pinned nixpkgs is missing: {NIXPKGS_STORE}")
    nixbench = Path(os.environ.get("NIXBENCH_ROOT", "/tmp/nixbench-src"))
    revision = subprocess.check_output(["git", "-C", str(nixbench), "rev-parse", "HEAD"], text=True).strip()
    if revision != NIXBENCH_REV:
        raise RuntimeError(f"NixBench checkout {nixbench} is {revision}, expected {NIXBENCH_REV}")
    secret = openrouter_key()
    catalog, fetched = RANKING.fetch_catalog()
    eligible = RANKING.discover(catalog)
    stored = json.loads((ROOT / "data" / "nixbench-ranking.json").read_text(encoding="utf-8"))
    stored_ids = {row["id"] for row in stored.get("availableModels", [])}
    live_ids = {row["id"] for row in eligible}
    bwrap = Path(os.environ.get("NIXBENCH_BWRAP", "/nix/store/y0ra9qr3rz81d9wl7dfrldv3j25dc98q-bubblewrap-0.12.0/bin/bwrap"))
    if not bwrap.is_file():
        raise RuntimeError(f"bubblewrap is missing: {bwrap}")
    nix_bin = Path(shutil.which("nix-instantiate") or "").resolve().parent
    if not nix_bin.joinpath("nix-instantiate").is_file():
        raise RuntimeError("nix-instantiate is not on PATH")
    opencode = Path(shutil.which("opencode2") or shutil.which("opencode") or "")
    if not opencode.is_file():
        raise RuntimeError("opencode2 is not on PATH")
    corpus = prepare_corpus(nixbench)
    probe_path = Path("/tmp/openrouter-probe.json")
    probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.is_file() else []
    jobs = [
        {"key": f"{variant}/{row['id']}", "variant": variant, "model": row["id"]}
        for row in eligible
        for variant in VARIANTS
    ]
    by_model = {row["id"]: row for row in eligible}
    limit = int(os.environ.get("NIXBENCH_COMPARE_JOBS", "4"))
    store = Store(secret)
    store.update(
        lambda document: document.update(
            {
                "schemaVersion": 1,
                "catalogRetrievedAt": fetched,
                "eligibleModels": eligible,
                "catalogDelta": {
                    "added": sorted(live_ids - stored_ids),
                    "removed": sorted(stored_ids - live_ids),
                },
                "probe": probe,
                "nixbenchRevision": NIXBENCH_REV,
                "nixpkgs": str(NIXPKGS_STORE),
                "parallelJobs": limit,
                "runs": document.get("runs", {}),
            }
        )
    )
    pending = []
    for job in jobs:
        current = {}
        if store.path.is_file():
            current = json.loads(store.path.read_text(encoding="utf-8")).get("runs", {}).get(job["key"], {})
        if current.get("status") == "complete":
            print(f"skip complete {job['key']}", flush=True)
            continue
        pending.append(job)
    print(
        f"eligible {len(eligible)}; added {sorted(live_ids - stored_ids) or 'none'}; "
        f"removed {sorted(stored_ids - live_ids) or 'none'}; queued {len(pending)} at {limit} workers",
        flush=True,
    )
    running: set[str] = set()
    threads: list[threading.Thread] = []
    schedule = threading.Lock()

    def finish(job: dict[str, str]) -> None:
        try:
            run_job(store, job, by_model[job["model"]], nixbench, corpus, bwrap, nix_bin, opencode, secret)
        finally:
            with schedule:
                running.discard(job["model"])

    def start_available() -> None:
        with schedule:
            threads[:] = [thread for thread in threads if thread.is_alive()]
            while len(threads) < limit and pending:
                choice = next((job for job in pending if job["model"] not in running), None)
                if choice is None:
                    return
                pending.remove(choice)
                running.add(choice["model"])
                thread = threading.Thread(target=finish, args=(choice,), name=choice["key"])
                threads.append(thread)
                thread.start()

    while True:
        start_available()
        with schedule:
            alive = any(thread.is_alive() for thread in threads)
            work_left = bool(pending) or alive
        if not work_left:
            break
        time.sleep(1)
    print("comparison queue finished", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"nixbench-compare: {error}", file=sys.stderr)
        raise SystemExit(1)
