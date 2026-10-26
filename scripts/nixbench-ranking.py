#!/usr/bin/env python3
"""Refresh the public, explicit-zero-cost OpenRouter pool and aggregate NixBench screens."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CATALOG_URL = "https://models.dev/api.json"
NIXBENCH_REPO = "https://github.com/maximilianpw/nixbench"
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/+@-]{1,240}$")
SCHEMA = 1
OPENCODE_LOW_EVIDENCE = (
    "opencode/beam-free",
)
OPENCODE_WEAK_EVIDENCE = (
    "opencode/big-pickle",
)
OPENCODE_BENCHMARK_ORDER = (
    "opencode/deepseek-v4.1-flash-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/glm-5.3-flash-free",
    "opencode/nemotron-3.5-super-vl-free",
    "opencode/flint-40b-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
)


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


def discover(data: dict[str, Any], provider_id: str = "openrouter") -> list[dict[str, Any]]:
    provider = data.get(provider_id)
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
        model_id = str(model.get("id", key))
        # OpenRouter routers may select a paid model despite reporting zero cost.
        if provider_id == "openrouter" and model_id.startswith("openrouter/"):
            continue
        selector = f"{provider_id}/{model_id}"
        if not MODEL_RE.fullmatch(selector):
            continue
        out.append({
            "id": selector,
            "name": model.get("name", key),
            "contextTokens": context,
            "releaseDate": model.get("release_date"),
            "catalogUpdated": model.get("last_updated"),
            "catalogCost": {"input": 0, "output": 0},
            "availability": f"models.dev-zero-price for {provider_id}; account access must be checked at run time",
        })
    return sorted(out, key=lambda row: (row.get("releaseDate") or "", row["id"]), reverse=True)


def order_available(available: list[dict[str, Any]], priority: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the priority script's order. An empty priority keeps release-date order."""
    if not priority:
        return list(available)
    rank = {row.get("id"): index for index, row in enumerate(priority) if isinstance(row, dict)}
    original = {row.get("id"): index for index, row in enumerate(available)}

    def key(row: dict[str, Any]) -> tuple[int, int]:
        index = rank.get(row.get("id"))
        if index is None:
            return (1, original.get(row.get("id"), 0))
        return (0, index)

    ordered = sorted(available, key=key)
    by_id = {row.get("id"): row for row in priority if isinstance(row, dict)}
    for row in ordered:
        hit = by_id.get(row.get("id"))
        score = hit.get("score") if isinstance(hit, dict) else None
        if isinstance(score, (int, float)):
            row["priority"] = {
                "score": score,
                "reason": hit.get("reason") if isinstance(hit, dict) else None,
                "sources": hit.get("sources") if isinstance(hit, dict) and isinstance(hit.get("sources"), dict) else {},
            }
    return ordered


def screen_priority(available: list[dict[str, Any]]) -> list[dict[str, Any]]:
    script = Path(__file__).with_name("nixbench-priority.mts")
    node = os.environ.get("NODE") or "node"
    try:
        completed = subprocess.run(
            [node, "--experimental-strip-types", str(script)],
            input=json.dumps(available),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"nixbench-ranking: screening priority unavailable ({error}); using release order", file=sys.stderr)
        return []
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        print(f"nixbench-ranking: screening priority unavailable ({detail}); using release order", file=sys.stderr)
        return []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        print(f"nixbench-ranking: screening priority unavailable ({error}); using release order", file=sys.stderr)
        return []
    if not isinstance(parsed, list):
        print("nixbench-ranking: screening priority unavailable (not a list); using release order", file=sys.stderr)
        return []
    return [row for row in parsed if isinstance(row, dict)]


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
    provider = doc.get("provider", "openrouter")
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
        f"No currently free, eligible {provider} model has a recorded NixBench run. Catalog availability is not proof of account access."
    )


def choose_candidate(new: list[dict[str, Any]], provider_id: str) -> dict[str, Any] | None:
    if provider_id != "opencode":
        return next(iter(new), None)
    low_rank = {model_id: index for index, model_id in enumerate(OPENCODE_LOW_EVIDENCE)}
    weak_rank = {model_id: index for index, model_id in enumerate(OPENCODE_WEAK_EVIDENCE)}
    benchmark_rank = {model_id: index for index, model_id in enumerate(OPENCODE_BENCHMARK_ORDER)}
    # Prefer known sparse-evidence models first, then newly discovered models
    # without a curated benchmark record, followed by benchmarked candidates.
    # Big Pickle has a small independent battery, so Beam stays ahead of it.
    def key(model: dict[str, Any]) -> tuple[int, int]:
        model_id = model["id"]
        if model_id in low_rank:
            tier, rank = 0, low_rank[model_id]
        elif model_id not in benchmark_rank:
            if model_id in weak_rank:
                tier, rank = 2, weak_rank[model_id]
            else:
                tier, rank = 1, 0
        else:
            tier, rank = 3, benchmark_rank[model_id]
        return (tier, rank)

    # `new` inherits release-date-descending catalog order, retained for ties.
    return min(new, key=key) if new else None


def refresh(args: argparse.Namespace) -> int:
    catalog, fetched = fetch_catalog()
    doc = load(args.file)
    available = discover(catalog, args.provider)
    if args.provider == "openrouter":
        available = order_available(available, screen_priority(available))
    attempted = {row.get("id") for row in doc.get("attempted", []) if isinstance(row, dict)}
    screened = {row.get("id") for row in doc.get("models", []) if isinstance(row, dict)}
    # One model per scheduled run keeps provider quotas bounded. Models with no
    # leaderboard match go first; scored models follow the free-model ranker weights.
    new = [row for row in available if row["id"] not in attempted and row["id"] not in screened]
    # One new candidate per scheduled run bounds provider quotas.
    candidate = choose_candidate(new, args.provider)
    doc.update({
        "schemaVersion": SCHEMA,
        "generatedAt": now(),
        "provider": args.provider,
        "candidateOrdering": ({
            "sparseEvidenceFirst": list(OPENCODE_LOW_EVIDENCE),
            "limitedEvidenceNext": list(OPENCODE_WEAK_EVIDENCE),
            "benchmarkOrder": list(OPENCODE_BENCHMARK_ORDER),
            "unlistedModels": "treated as lacking a curated coding-agent benchmark and screened before benchmarked candidates",
        } if args.provider == "opencode" else None),
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
            "agent": f"OpenCode v2 using the {args.provider} provider with read/edit/glob/grep and Bubblewrap shell; shell network disabled, environment cleared, external file access denied",
            **({"candidateOrder": "Unmatched models first, then LMArena agent, webdev, and text indexes, plus Artificial Analysis coding, agentic, and intelligence indexes when a key is configured"} if args.provider == "openrouter" else {}),
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
        "configuration": {
            "agent": "opencode",
            "provider": args.model.split("/", 1)[0],
            "version": args.agent_version,
            "tools": ["read", "edit", "glob", "grep", "shell"],
            "shell": "Bubblewrap network-disabled; environment cleared; task worktree writable; system/Nix files read-only",
        },
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
    if args.status != "deferred":
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
    refresh_parser.add_argument("--provider", default="openrouter", choices=("openrouter", "opencode"))
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
    failed_parser.add_argument("--status", choices=("runner-failed", "unavailable", "deferred"), default="runner-failed")
    failed_parser.set_defaults(func=failed)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"nixbench-ranking: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
