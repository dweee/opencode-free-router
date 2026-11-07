#!/usr/bin/env python3
"""Exit successfully only when agent logs report an exhausted provider quota."""
from pathlib import Path
import re
import sys

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
QUOTA = re.compile(r"Error:\s*(?:rate.?limit|quota|free messages|free usage limit|too many requests|usage limit|limit reached|429)|HTTP 429|status.?code.?429", re.I)


def quota_exhausted(text):
    return bool(QUOTA.search(ANSI.sub("", text)))


if __name__ == "__main__":
    root = Path(sys.argv[1])
    sys.exit(0 if any(quota_exhausted(path.read_text(errors="replace"))
                     for path in root.rglob("agent.log") if not path.is_symlink()) else 1)
