#!/usr/bin/env python3
"""File helpers shared by the ranker and by every stack module.

A LEAF on purpose: it imports nothing else from this directory, which is what
keeps `rank_risk -> stack_* -> stack_common` acyclic. `rank_risk.py` is loaded
by path (`importlib.util.spec_from_file_location`) rather than imported by
name, so a stack module that reached back into it for these two names would
re-execute the ranker mid-import instead of finding it in `sys.modules`.

Both names moved here unchanged from `rank_risk.py`; `rank_risk` re-exports
them, so `rank_risk.SKIP_DIRS` and `rank_risk.read_text` still resolve.
"""
from __future__ import annotations

import os

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", "vendor",
             "site-packages", ".eggs"}


def read_text(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""
