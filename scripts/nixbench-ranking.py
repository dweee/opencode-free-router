#!/usr/bin/env python3
"""Refresh the public, explicit-zero-cost OpenRouter pool and aggregate NixBench screens."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CATALOG_URL = "https://models.dev/api.json"
NIXBENCH_REPO = "https://github.com/maximilianpw/nixbench"
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/+@-]{1,240}$")
SCHEMA = 1


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_catalog() -> tuple[dict[str, Any], str]:
    request = urllib.request.Request(
        CATALOG_URL,
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 t3-nixbench-router/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"models.dev returned HTTP {response.status}")
        data = json.load(response)
        fetched = response.headers.get("Date") or now()
    if not isinstance(data, dict):
        raise RuntimeError("models.dev API response is not an object")
    return data, fetched


def zero_cost(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("input") != 0 or value.get("output") != 0:
        return False
    cache = value.get("cache")
    if cache is not None and (not isinstance(cache, dict) or any(v != 0 for v in cache.values())):
        return False
    tiers = value.get("tiers")
    if tiers is not None and (not isinstance(tiers, list) or not tiers or not all(zero_cost(t) for t in tiers)):
        return False
    return True


def discover(data: dict[str, Any]) -> list[dict[str, Any]]:
    provider = data.get("openrouter")
    if not isinstance(provider, dict):
        return []
    out = []
    for key, model in (provider.get("models") or {}).items():
        if not isinstance(model, dict) or not zero_cost(model.get("cost")):
            continue
        modalities = model.get("modalities") or {}
        if model.get("tool_call") is not True or "text" not in (modalities.get("input") or []):
            continue
        context = (model.get("limit") or {}).get("context")
        if not isinstance(context, int) or context < 64000:
            continue
        model_id = model.get("id", key)
        selector = f"openrouter/{model_id}"
        if not MODEL_RE.fullmatch(selector):
            continue
        out.append({
            "id": selector,
            "name": model.get("name", key),
            "contextTokens": context,
            "releaseDate": model.get("release_date"),
            "catalogUpdated": model.get("last_updated"),
            "catalogCost": {"input": 0, "output": 0},
            "availability": "models.dev-zero-price; provider access must be checked at run time",
        })
    return sorted(out, key=lambda row: (row.get("releaseDate") or "", row["id"]), reverse=True)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schemaVersion": SCHEMA, "models": [], "availableModels": [], "attempted": []}
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA:
        raise RuntimeError(f"unsupported ranking file schema: {path}")
    return value


def save(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def recompute(doc: dict[str, Any]) -> None:
    available = {m["id"] for m in doc.get("availableModels", []) if isinstance(m, dict) and isinstance(m.get("id"), str)}
    models = [m for m in doc.get("models", []) if isinstance(m, dict) and isinstance(m.get("scoreRate"), (int, float))]
    models.sort(key=lambda m: (m["scoreRate"], m.get("completedAt", "")), reverse=True)
    doc["models"] = models
    current = next((m for m in models if m.get("id") in available), None)
    doc["currentModel"] = current.get("id") if current else None
    doc["recommendationStatus"] = "provisional-screening" if current else "no-benchmarked-free-model"
    doc["rankingNote"] = (
        "Provisional one-run screens only. NixBench 3.2.0 is in calibration; this is not an official leaderboard or a statistically stable recommendation."
        if current else
        "No currently free, eligible OpenRouter model has a recorded NixBench run. Catalog availability is not proof of account access."
    )


def refresh(args: argparse.Namespace) -> int:
    catalog, fetched = fetch_catalog()
    doc = load(args.file)
    available = discover(catalog)
    attempted = {row.get("id") for row in doc.get("attempted", []) if isinstance(row, dict)}
    screened = {row.get("id") for row in doc.get("models", []) if isinstance(row, dict)}
    # One newly eligible model per scheduled run keeps provider quotas bounded.
    new = [row for row in available if row["id"] not in attempted and row["id"] not in screened]
    # Prefer the newest model, one candidate per scheduled run to bound provider quotas.
    candidate = next(iter(new), None)
    doc.update({
        "schemaVersion": SCHEMA,
        "generatedAt": now(),
        "catalog": {"url": CATALOG_URL, "retrievedAt": fetched},
        "benchmark": {
            "name": "NixBench",
            "repository": NIXBENCH_REPO,
            "revision": args.nixbench_revision or None,
            "corpusId": "nixbench-public",
            "corpusVersion": args.corpus_version or "3.2.0",
            "taskCount": args.corpus_task_count or None,
            "corpusDigest": args.corpus_digest or None,
            "corpusStatus": "calibrating",
            "interpretation": "screening-only",
            "scope": "NixBench public corpus tasks supported by GitHub-hosted runners; KVM-dependent tasks omitted",
            "excludedTasks": [
                "boot-hook-marker-survives-reboot",
                "firewall-nftables-forwarding-silently-dropped",
                "state-directory-ownership-after-hardening",
            ],
            "agent": "OpenCode v2 with read/edit/glob/grep tools; shell, network tools, and external file access denied",
        },
        "availableModels": available,
        "nextCandidate": candidate["id"] if candidate else None,
    })
    recompute(doc)
    save(args.file, doc)
    print(json.dumps({"available": len(available), "candidate": doc["nextCandidate"], "file": str(args.file)}))
    return 0


def record(args: argparse.Namespace) -> int:
    doc = load(args.file)
    if not MODEL_RE.fullmatch(args.model):
        raise RuntimeError("invalid model selector")
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    score = summary.get("score")
    maximum = summary.get("max_score")
    tasks = summary.get("tasks")
    if (
        not isinstance(score, (int, float))
        or not isinstance(maximum, (int, float))
        or maximum <= 0
        or not isinstance(tasks, list)
        or len(tasks) != args.expected_tasks
        or any(
            not isinstance(task, dict)
            or not isinstance(task.get("score"), (int, float))
            or task.get("measurement_status") != "valid"
            for task in tasks
        )
    ):
        raise RuntimeError("NixBench summary is missing valid aggregate scores or task observations")
    entry = {
        "id": args.model,
        "score": score,
        "maxScore": maximum,
        "scoreRate": round(score / maximum, 6),
        "passedTasks": summary.get("passed"),
        "taskCount": len(tasks),
        "trials": 1,
        "completedAt": summary.get("created_at") or now(),
        "runId": summary.get("run_id"),
        "status": "screening-only",
        "configuration": {"agent": "opencode", "version": args.agent_version, "tools": ["read", "edit", "glob", "grep"], "shell": "denied"},
    }
    models = [m for m in doc.get("models", []) if not isinstance(m, dict) or m.get("id") != args.model]
    models.append(entry)
    doc["models"] = models
    doc["nextCandidate"] = None
    recompute(doc)
    save(args.file, doc)
    return 0


def failed(args: argparse.Namespace) -> int:
    doc = load(args.file)
    attempted = [row for row in doc.get("attempted", []) if not isinstance(row, dict) or row.get("id") != args.model]
    attempted.append({"id": args.model, "at": now(), "status": args.status})
    doc["attempted"] = attempted[-500:]
    doc["nextCandidate"] = None
    recompute(doc)
    save(args.file, doc)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--file", type=Path, required=True)
    refresh_parser.add_argument("--nixbench-revision", default="")
    refresh_parser.add_argument("--corpus-version", default="3.2.0")
    refresh_parser.add_argument("--corpus-task-count", type=int, default=0)
    refresh_parser.add_argument("--corpus-digest", default="")
    refresh_parser.set_defaults(func=refresh)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--file", type=Path, required=True)
    record_parser.add_argument("--model", required=True)
    record_parser.add_argument("--summary", type=Path, required=True)
    record_parser.add_argument("--expected-tasks", type=int, required=True)
    record_parser.add_argument("--agent-version", required=True)
    record_parser.set_defaults(func=record)
    failed_parser = sub.add_parser("failed")
    failed_parser.add_argument("--file", type=Path, required=True)
    failed_parser.add_argument("--model", required=True)
    failed_parser.add_argument("--status", choices=("runner-failed", "unavailable"), default="runner-failed")
    failed_parser.set_defaults(func=failed)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"nixbench-ranking: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
