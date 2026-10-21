#!/usr/bin/env python3
"""Attach Nix skills and the nixos MCP guidance to a NixBench OpenCode config."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

VARIANTS = ("mcp", "skills", "both")
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "benchmark" / "skills"
GUIDANCE_PATH = REPO_ROOT / "benchmark" / "nixos-mcp-guidance.md"
GUIDANCE_HEADING = "\n\n## Nix lookup tools\n\n"
SKILLS_HEADING = "\n\n## Nix guidance\n\n"


def skill_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end == -1:
            raise RuntimeError(f"skill frontmatter is unclosed: {path}")
        text = text[end + 5 :]
    return text.strip() + "\n"


def apply_variant(config: dict[str, Any], variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise RuntimeError(f"unknown NixBench variant: {variant}")
    agent = config["agents"]["nixbench"]
    system = agent["system"]
    permissions = agent["permissions"]
    if variant in ("skills", "both") and SKILLS_HEADING not in system:
        bodies = [
            skill_body(SKILLS_DIR / "nix" / "SKILL.md"),
            skill_body(SKILLS_DIR / "nix-module-house-style" / "SKILL.md"),
        ]
        system += SKILLS_HEADING + "\n".join(bodies)
        config["skills"] = [str(SKILLS_DIR)]
        permissions.append({"action": "skill", "resource": "*", "effect": "allow"})
    if variant in ("mcp", "both") and GUIDANCE_HEADING not in system:
        system += GUIDANCE_HEADING + GUIDANCE_PATH.read_text(encoding="utf-8").strip() + "\n"
        config["mcp"] = {
            "servers": {
                "nixos": {
                    "type": "local",
                    "command": ["nix", "run", "github:utensils/mcp-nixos", "--"],
                    "codemode": False,
                }
            }
        }
        permissions.extend(
            [
                {"action": "nixos_nix", "resource": "*", "effect": "allow"},
                {"action": "nixos_nix_versions", "resource": "*", "effect": "allow"},
            ]
        )
    agent["system"] = system
    return config


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: nixbench-variant.py <base-config> <variant> <output>", file=sys.stderr)
        return 2
    base, variant, output = sys.argv[1:]
    config = json.loads(Path(base).read_text(encoding="utf-8"))
    rendered = apply_variant(config, variant)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
