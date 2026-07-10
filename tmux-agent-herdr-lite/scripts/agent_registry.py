"""Shared pane-registry helpers for the tmux agent cockpit.

The registry is the directory of per-pane JSON records written by
``agent-pane`` and refreshed by ``agent-status-scan`` (default
``~/.tmux/agent-panes/panes``). Coordination commands (``agent-list``,
``agent-read``, ``agent-send``, ``agent-run``, ``agent-wait``,
``agent-explain``) resolve a human target — an agent name, a window name, or
a raw tmux pane id — to one record through this module, mirroring how Herdr
lets its CLI address agents by name, label, or pane id.
"""

import json
import os

DEFAULT_ROOT = os.path.expanduser(os.environ.get(
    "AGENT_TMUX_ROOT", os.path.join("~", ".tmux", "agent-panes")))


def pane_dir(root=None):
    return os.path.join(root or DEFAULT_ROOT, "panes")


def load_records(root=None):
    """Load every pane record, oldest-registered first (stable file order)."""
    d = pane_dir(root)
    records = []
    if not os.path.isdir(d):
        return records
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def resolve(target, root=None):
    """Resolve ``target`` to a single pane record, or raise LookupError.

    Match order (first hit wins): exact agent name, exact window name,
    pane id (with or without the ``%`` prefix). Names are preferred so a
    numeric agent name cannot be shadowed by a pane id.
    """
    records = load_records(root)
    for key in ("name", "window"):
        for rec in records:
            if rec.get(key) == target:
                return rec
    want = target if target.startswith("%") else "%" + target
    for rec in records:
        if rec.get("pane_id") == want:
            return rec
    raise LookupError(
        f"no registered agent matches '{target}' "
        f"({len(records)} record(s) in {pane_dir(root)})")
