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
import subprocess
import sys

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


def record_path(pane_id, root=None):
    """On-disk JSON record for a pane id (single owner of the naming scheme)."""
    return os.path.join(pane_dir(root), pane_id.lstrip("%") + ".json")


def pane_alive(pane_id):
    """True if tmux still has this pane.

    Compare the echoed id: some tmux builds exit 0 with empty output for a
    dead pane target, so the return code alone is not trustworthy.
    """
    r = subprocess.run(["tmux", "display-message", "-p", "-t", pane_id, "#{pane_id}"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == pane_id


def _main(argv):
    # CLI entrypoint shared by the coordination scripts:
    #   python3 agent_registry.py resolve <target> [field]
    # Prints the record as JSON, or one field's value. Exit 1 with a message
    # on stderr when the target is unknown.
    if len(argv) >= 2 and argv[0] == "resolve":
        try:
            rec = resolve(argv[1])
        except LookupError as e:
            print(f"agent_registry: {e}", file=sys.stderr)
            return 1
        if len(argv) >= 3:
            print(rec.get(argv[2], ""))
        else:
            print(json.dumps(rec))
        return 0
    print("usage: agent_registry.py resolve <target> [field]", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
