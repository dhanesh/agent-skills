# python-isolation: run inline Python with no modules from the working directory

- id: python-isolation
- proven: no
- anchors: context-hygiene-kit/assets/hooks/session_start.sh, context-hygiene-kit/assets/hooks/stop.sh, context-hygiene-kit/scripts/install.sh, world-model-ledger/assets/hooks/session_start.sh, world-model-ledger/assets/hooks/stop.sh, world-model-ledger/assets/hooks/pretooluse.sh, world-model-ledger/assets/hooks/posttooluse-observe.sh, world-model-ledger/scripts/install.sh, world-model-ledger/eval/grade.py:grade_task3, tmux-agent-herdr-lite/scripts/agent-dashboard, tmux-agent-herdr-lite/scripts/agent-explain, tmux-agent-herdr-lite/scripts/agent-jump, tmux-agent-herdr-lite/scripts/agent-list, tmux-agent-herdr-lite/scripts/agent-pane, tmux-agent-herdr-lite/scripts/agent-resume, tmux-agent-herdr-lite/scripts/agent-status-scan, tmux-agent-herdr-lite/scripts/agent-status-summary, tmux-agent-herdr-lite/scripts/install.sh

## What it is

**Status: planned (run 5).** A hook or a cockpit script often starts in a directory
that the user does not control: the project of the session, or the current pane. When
Python runs code from `-c` or from stdin (`-`), it puts the working directory first on
`sys.path`. A `json.py`, `hashlib.py` or `shlex.py` in that directory then runs in place
of the standard module. After run 5, every inline Python call in these files runs with
`-I` (isolated mode), so Python never imports a module from the working directory:

- `context-hygiene-kit/assets/hooks/session_start.sh` and `stop.sh`, and the version
  check in `context-hygiene-kit/scripts/install.sh`.
- `world-model-ledger/assets/hooks/session_start.sh`, `stop.sh`, `pretooluse.sh` and
  `posttooluse-observe.sh`, and the version check and the starter-constraint heredoc in
  `world-model-ledger/scripts/install.sh`.
- The `python3 -` heredocs in `tmux-agent-herdr-lite/scripts/agent-dashboard`,
  `agent-explain`, `agent-jump`, `agent-list`, `agent-pane`, `agent-resume`,
  `agent-status-scan` and `agent-status-summary`, and the version check in its
  `install.sh`.

Also, `world-model-ledger/eval/grade.py` `grade_task3` runs the candidate program with
`-I` in a fresh temporary working directory. The candidate cannot read or write the
caller's directory by a relative path, and a module beside the temporary file is not
imported. The comment above the function no longer says that this is a sandbox: it is
only a separate process.

## How to reach it

Each hook reads one JSON object on stdin (`cwd`, `transcript_path`, `tool_input`) and
prints one JSON object on stdout. A hook finds its kit one directory above its own
`hooks/` directory, so the copy in the checkout uses `<skill>/assets` as `KIT_HOME`.
The context-hygiene SessionStart hook works in `CLAUDE_PROJECT_DIR`. The other hooks
work in the `cwd` from stdin, but their first `-c` call runs in the directory that
started the hook. The cockpit scripts read `AGENT_TMUX_ROOT` (default
`$HOME/.tmux/agent-panes`) and run without a tmux server: `agent-status-scan` prints
`agent-status-scan: tmux unreachable; registry left intact` and exits 0.
`grade.py task3 <file>` prints one JSON object, `{"pass": ..., "reason": ...}`, where
`reason` is the first 120 characters of the candidate's output.

## Drive it

Make a fixture git repo with three planted modules. Each module only appends a line
(`<step> <module>`) to a marker file. Then run each hook from inside that repo.

```sh
PI="$WT/.verify-run/$INSTANCE/pi"
mkdir -p "$PI/home" "$PI/proj" "$PI/bin" "$PI/gt-run" "$PI/gt-tmp"
git -C "$PI/proj" init -q
printf 'def f():\n    return 1\n' > "$PI/proj/a.py"
: > "$PI/proj/t.jsonl"
for m in json hashlib shlex; do
  cat > "$PI/proj/$m.py" <<PLANT
import os
with open("$PI/marker", "a") as _f:
    _f.write(os.environ.get("PI_STEP", "?") + " $m\n")
PLANT
done
export HOME="$PI/home" CLAUDE_PROJECT_DIR="$PI/proj"
printf '{"cwd": "%s", "transcript_path": "%s/t.jsonl", "session_id": "s1"}\n' \
  "$PI/proj" "$PI/proj" > "$PI/in-session.json"
printf '{"cwd": "%s", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}}\n' \
  "$PI/proj" > "$PI/in-tool.json"
hook() {  # capture-name, hook path, stdin file
  PI_STEP="$1" python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name "$1" -- \
    sh -c 'cd "$1" && bash "$2" < "$3"' sh "$PI/proj" "$WT/$2" "$3"
}
hook pi-chk-session context-hygiene-kit/assets/hooks/session_start.sh "$PI/in-session.json"
hook pi-chk-stop    context-hygiene-kit/assets/hooks/stop.sh          "$PI/in-session.json"
hook pi-wml-session world-model-ledger/assets/hooks/session_start.sh  "$PI/in-session.json"
hook pi-wml-stop    world-model-ledger/assets/hooks/stop.sh           "$PI/in-session.json"
hook pi-wml-pre     world-model-ledger/assets/hooks/pretooluse.sh     "$PI/in-tool.json"
hook pi-wml-post    world-model-ledger/assets/hooks/posttooluse-observe.sh "$PI/in-tool.json"
ls -A "$PI/proj" > "$PI/proj-after-hooks.txt"
```

- `pi-chk-session`: exit 0. stdout is one JSON object with
  `"hookEventName": "SessionStart"` and an `additionalContext` that starts
  `# Context Digest`.
- `pi-chk-stop`: exit 0 and stdout `{"continue": true}`.
- `pi-wml-session`: exit 0. stdout is one JSON object with `"continue": true`,
  `"hookEventName": "SessionStart"` and an `additionalContext` that starts
  `# world-model digest`.
- `pi-wml-stop`: exit 0, stdout `{"continue": true}` and a `world-model[stop]:` line on
  stderr. That line shows that the hook read `cwd` and `transcript_path` and ran the
  harvester.
- `pi-wml-pre`: exit 0 and stdout `{"continue": true}`.
- `pi-wml-post`: exit 0, stdout `{"continue": true}` and a `{"files": 1, ...}` line on
  stderr.
- `proj-after-hooks.txt` holds `.context` and `.world-model`.

Now run the cockpit scripts from the same directory. `agent-status-scan` calls `tmux`,
so put a wrapper first on `PATH` that sends every `tmux` call to a private socket.
No server starts on that socket, and the user's server is never asked.

```sh
REAL_TMUX="$(command -v tmux)"
cat > "$PI/bin/tmux" <<WRAP
#!/bin/sh
cd "$PI" && exec "$REAL_TMUX" -S ./s -f /dev/null "\$@"
WRAP
chmod +x "$PI/bin/tmux"
for s in agent-list agent-status-scan agent-dashboard agent-status-summary; do
  PI_STEP="pi-$s" env -u TMUX PATH="$PI/bin:$PATH" AGENT_TMUX_ROOT="$PI/agents" \
    python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name "pi-$s" -- \
    sh -c 'cd "$1" && bash "$2"' sh "$PI/proj" "$WT/tmux-agent-herdr-lite/scripts/$s"
done
(cd "$PI" && "$REAL_TMUX" -S ./s kill-server 2>/dev/null || true)
```

- `pi-agent-list`: exit 0 and `no registered agents (start one with: agent-pane <name> <command>)`.
- `pi-agent-status-scan`: exit 0 and `agent-status-scan: tmux unreachable; registry left intact`.
- `pi-agent-dashboard`: exit 0, `TMUX AGENT COCKPIT` and `No state file yet.`
- `pi-agent-status-summary`: exit 0 and `agents:-`.

Then grade a candidate that reports its isolation flag and writes a file by a relative
path. The caller's directory holds a probe file, and a planted `json.py` sits in
`TMPDIR`, where the grader writes the candidate file.

```sh
cat > "$PI/cand.py" <<'CAND'
import json, os, sys
print("ISOLATED", sys.flags.isolated, "CWD_MARKER", os.path.exists("gt-cwd-probe"))
open("gt-cand-wrote", "w").write("x")
def slugify(s):
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
CAND
cat > "$PI/gt-tmp/json.py" <<PLANT
with open("$PI/marker", "a") as _f:
    _f.write("pi-grade json\n")
PLANT
: > "$PI/gt-run/gt-cwd-probe"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pi-grade -- \
  sh -c 'cd "$1" && TMPDIR="$2" python3 "$3" task3 "$4"' sh \
  "$PI/gt-run" "$PI/gt-tmp" "$WT/world-model-ledger/eval/grade.py" "$PI/cand.py"
ls -A "$PI/gt-run" > "$PI/gt-run-after.txt"
grep -n 'sandbox' world-model-ledger/eval/grade.py > "$PI/grade-sandbox.txt" || true
cat "$PI/marker" > "$PI/marker-final.txt" 2>&1 || echo "no marker file" > "$PI/marker-final.txt"
```

- `pi-grade`: exit 0 and `{"pass": true, "reason": "ISOLATED 1 CWD_MARKER False\nOK"}`.
- `gt-run-after.txt`: only `gt-cwd-probe`. There is no `gt-cand-wrote`.
- `grade-sandbox.txt`: empty.
- `marker-final.txt`: `no marker file`. No hook, cockpit script or grader run imported a
  planted module.

Last, a static check lists every inline Python call in the anchored files that does not
start with `-I`:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pi-static -- \
  sh -c 'grep -nE "(\"\\\$PY\"|python3?) +-(c|\$| )" context-hygiene-kit/assets/hooks/*.sh context-hygiene-kit/scripts/install.sh world-model-ledger/assets/hooks/*.sh world-model-ledger/scripts/install.sh tmux-agent-herdr-lite/scripts/agent-* tmux-agent-herdr-lite/scripts/install.sh'
```

- `pi-static`: exit 1 (grep found no line), an empty stdout and an empty stderr.

## Proof

The six hook captures (exit 0 and the normal hook output), `proj-after-hooks.txt`, the
four cockpit captures, the `pi-grade` capture with `ISOLATED 1 CWD_MARKER False`,
`gt-run-after.txt`, `grade-sandbox.txt`, `marker-final.txt` with no marker, and the
`pi-static` capture with no line.

## Gotchas

- Before the fix (dry run on `eeac771`), the marker file held:
  `pi-chk-session json`, `pi-chk-stop json` (two lines), `pi-wml-session json`,
  `pi-wml-stop json` (two lines), `pi-wml-pre json`, `pi-wml-post json`, and
  at least one `hashlib`, `json` and `shlex` line for each of `pi-agent-list`,
  `pi-agent-status-scan`, `pi-agent-dashboard` and `pi-agent-status-summary`, and
  `pi-grade json`. `pi-chk-session` exited 1 with
  `AttributeError: module 'json' has no attribute 'dumps'`. The other hooks printed only
  `{"continue": true}` with an empty stderr, because the `get` call failed and the hook
  stopped early. `pi-grade` printed `ISOLATED 0 CWD_MARKER True`, and `gt-run-after.txt`
  held `gt-cand-wrote`. `proj-after-hooks.txt` held `.context` but no `.world-model`.
  `grade-sandbox.txt` held line 24, `executed in a subprocess
  sandbox`. `pi-static` listed 20 lines: context-hygiene-kit `session_start.sh:28`,
  `stop.sh:25`, `install.sh:40`; world-model-ledger `session_start.sh:17`, `:44`,
  `stop.sh:15`, `pretooluse.sh:15`, `:37`, `posttooluse-observe.sh:20`, `install.sh:49`,
  `:133`; tmux-agent-herdr-lite `agent-dashboard:13`, `agent-explain:17`,
  `agent-jump:27`, `agent-list:24`, `agent-pane:70`, `agent-resume:41`,
  `agent-status-scan:12`, `agent-status-summary:12`, `install.sh:14`.
- The three `install.sh` version checks import only `sys`, which is built in, so a
  planted module cannot show them. The static check is their proof. The `agent-pane`
  heredoc needs a tmux server; `features/tmux-cockpit.md` drives it.
- Always run the cockpit scripts with the `tmux` wrapper first on `PATH`. Without it,
  `agent-status-scan` asks the user's own tmux server for its panes. Keep the `-S` path
  short and relative: a long socket path fails on macOS.
- Calls such as `"$PY" "$KIT_HOME/world_model.py"` run a script file. Python puts the
  script's directory on `sys.path`, not the working directory, so they are out of scope
  here. In a project-mode install the kit lives in the project root, so that directory is
  the project. Do not drive `world-model-ledger/scripts/install.sh` into the planted repo:
  its script-file calls then import the planted module for that other reason.
- The blocks export `HOME` (and the tmux block changes `PATH`). Run them in a subshell, or
  set them back before the next recipe.
- Run each hook from inside `$PI/proj` (the `cd` in the capture). The first `-c` call of
  most hooks runs before the hook changes to `cwd`, so a hook started from `$WT` does not
  meet the planted modules.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
