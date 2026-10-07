"""Agent-reported status and pane discovery for the tmux agent cockpit.

Screen-scraping (agent_classify) guesses a pane's state from what it shows.
Two things here make tracking exact instead:

* **Hook status** — Claude Code fires lifecycle hooks (UserPromptSubmit,
  PermissionRequest, Stop, ...) with ``$TMUX_PANE`` in the environment.
  ``agent-hook`` maps each event to a status and writes it to
  ``panes/<id>.hook``; ``agent-status-scan`` prefers it over the classifier.
  The scan never writes that file, so a hook update cannot be lost to a scan
  that read the pane record a moment earlier.
* **Adoption** — agents launched through an alias (``ccl`` → ``claude …``),
  restored by tmux-resurrect, or started before the shell hook existed never
  pass through ``preexec``. Every agent process still carries ``TMUX_PANE``,
  so the scan finds them by process and registers their panes.

Everything that touches the system (ps, tmux, files) is a thin wrapper around
pure functions so the logic is unit-testable offline.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_classify import infer_agent  # noqa: E402

# Trailing shell comment that marks a settings.json hook entry as ours, so a
# reinstall replaces exactly our entries and never touches anyone else's.
HOOK_MARKER = "# tmux-agent-herdr-lite"

CLAUDE_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
    "PostToolUse", "PostToolUseFailure", "Notification", "Stop", "StopFailure",
    "SessionEnd",
)

# Tools whose PreToolUse means "now waiting on the human", not "working".
_ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
_BLOCKING_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog"}

DEREGISTER = "__deregister__"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── event → status (pure) ────────────────────────────────────────────────────

def claude_event_status(payload):
    """Status a Claude Code hook payload implies, DEREGISTER, or None (no change)."""
    event = payload.get("hook_event_name", "")
    if event == "SessionStart":
        # A compaction restarts the session mid-turn; it says nothing new.
        return None if payload.get("source") == "compact" else "idle"
    if event == "UserPromptSubmit":
        return "working"
    if event == "PreToolUse":
        return "blocked" if payload.get("tool_name") in _ASKING_TOOLS else "working"
    if event == "PermissionRequest":
        return "blocked"
    if event in ("PostToolUse", "PostToolUseFailure"):
        return "working"
    if event == "Notification":
        kind = payload.get("notification_type", "")
        if kind in _BLOCKING_NOTIFICATIONS:
            return "blocked"
        if not kind and "permission" in payload.get("message", "").lower():
            return "blocked"
        return None  # idle_prompt and friends: Stop already said "done"
    if event == "Stop":
        return "done"
    if event == "StopFailure":
        return "error"
    if event == "SessionEnd":
        # /clear ends one session and starts the next in the same pane.
        return None if payload.get("reason") == "clear" else DEREGISTER
    return None


# ── settings.json merge (pure) ───────────────────────────────────────────────

def hook_command(agent_bin):
    return f"{shlex.quote(os.path.join(agent_bin, 'agent-hook'))} claude {HOOK_MARKER}"


def _is_ours(hook):
    return HOOK_MARKER in str(hook.get("command", ""))


def merge_claude_settings(settings, agent_bin, enable=True):
    """Return settings with our hook entries replaced (enable) or removed.

    Entries from other tools are left exactly as they were; groups and events
    emptied by removing ours are dropped so the file stays tidy.
    """
    out = dict(settings)
    hooks = {k: list(v) for k, v in (out.get("hooks") or {}).items()}
    for event, groups in list(hooks.items()):
        kept = []
        for group in groups:
            inner = [h for h in group.get("hooks", []) if not _is_ours(h)]
            if inner:
                kept.append({**group, "hooks": inner})
            elif not group.get("hooks"):
                kept.append(group)  # not ours to judge
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if enable:
        cmd = hook_command(agent_bin)
        for event in CLAUDE_EVENTS:
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": cmd, "timeout": 5}]})
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    return out


# ── adoption: find agent processes by TMUX_PANE (pure parsers) ───────────────

_PANE_RE = re.compile(r"(?:^|[\s\0])TMUX_PANE=(%\d+)(?=[\s\0]|$)")


def pane_from_environ(env_text):
    """TMUX_PANE value from an environment dump (NUL- or space-separated)."""
    m = _PANE_RE.search(env_text or "")
    return m.group(1) if m else ""


def agent_candidates(ps_lines):
    """[(pid, args, agent)] for `ps -o pid=,args=` lines that look like agents."""
    out = []
    for line in ps_lines:
        pid, _, args = line.strip().partition(" ")
        if not pid.isdigit():
            continue
        args = args.strip()
        agent = infer_agent(args)
        if agent:
            out.append((int(pid), args, agent))
    return out


def _environ_of(pids):
    """{pid: env text} — /proc where it exists, else `ps eww` (macOS/BSD, procps)."""
    envs = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                envs[pid] = fh.read().decode("utf-8", "replace")
        except OSError:
            pass
    rest = [p for p in pids if p not in envs]
    if not rest:
        return envs
    r = subprocess.run(["ps", "eww", "-o", "pid=,command=", "-p", ",".join(map(str, rest))],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        pid, _, rest = line.strip().partition(" ")
        if pid.isdigit():
            envs[int(pid)] = rest
    return envs


def discover_agent_panes(live_panes, cache_path=None):
    """{pane_id: (args, agent)} for agent processes running in live tmux panes.

    A process's TMUX_PANE never changes, so pid → pane is cached at
    cache_path and only new pids pay for an environment lookup; the scan runs
    this on every status-bar tick.
    """
    r = subprocess.run(["ps", "-ax", "-o", "pid=,args="], capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    cands = agent_candidates(r.stdout.splitlines())
    cache = {}
    if cache_path:
        try:
            cache = {int(k): v for k, v in json.load(open(cache_path)).items()}
        except Exception:
            cache = {}
    pids = [pid for pid, _, _ in cands]
    new = [pid for pid in pids if pid not in cache]
    for pid, env in _environ_of(new).items():
        cache[pid] = pane_from_environ(env)
    cache = {pid: cache[pid] for pid in pids if pid in cache}  # forget exited pids
    if cache_path:
        try:
            write_json_atomic(cache_path, {str(k): v for k, v in cache.items()})
        except OSError:
            pass
    found = {}
    for pid, args, agent in sorted(cands):  # oldest pid first: the session, not a child
        pane = cache.get(pid, "")
        if pane and pane in live_panes and pane not in found:
            found[pane] = (args, agent)
    return found


# ── registry writes ──────────────────────────────────────────────────────────

def pane_file(pane_dir, pane_id):
    return os.path.join(pane_dir, pane_id.lstrip("%") + ".json")


def hook_file(pane_dir, pane_id):
    return os.path.join(pane_dir, pane_id.lstrip("%") + ".hook")


def write_json_atomic(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def tmux_pane_meta(pane_id):
    """(session, window, cwd) for a pane, or None if tmux does not know it."""
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id,
             "#{session_name}\t#{window_name}\t#{pane_current_path}"],
            capture_output=True, text=True).stdout.rstrip("\n")
    except Exception:
        return None
    if not out:
        return None
    return tuple((out.split("\t") + ["", "", ""])[:3])


def register_pane(pane_dir, pane_id, command, agent=None, status="working"):
    """Write an auto-tracked record for pane_id. Returns True when written."""
    agent = agent or infer_agent(command)
    if not agent:
        return False
    meta = tmux_pane_meta(pane_id)
    if not meta:
        return False  # pane vanished
    session, window, cwd = meta
    os.makedirs(pane_dir, exist_ok=True)
    path = pane_file(pane_dir, pane_id)
    now = now_iso()
    created = now
    if os.path.exists(path):
        try:
            created = json.load(open(path)).get("created_at", now)
        except Exception:
            pass
    write_json_atomic(path, {
        "name": window or agent,
        "session": session,
        "window": window,
        "pane_id": pane_id,
        "command": command,
        "agent": agent,
        "cwd": cwd,
        "status": status,
        "created_at": created,
        "updated_at": now,
        "last_hash": "",
        "last_changed_at": now,
        "auto": True,  # shell-hook/adoption tracked; finished/prune may drop it freely
    })
    return True


def deregister_pane(pane_dir, pane_id):
    """Drop an auto-tracked record and its hook status; keep explicit launches."""
    path = pane_file(pane_dir, pane_id)
    try:
        os.remove(hook_file(pane_dir, pane_id))
    except OSError:
        pass
    if not os.path.exists(path):
        return
    try:
        auto = json.load(open(path)).get("auto", False)
    except Exception:
        auto = True  # unreadable -> safe to drop
    # Explicit `agent-pane` launches keep their record so resume metadata survives.
    if auto:
        try:
            os.remove(path)
        except OSError:
            pass
