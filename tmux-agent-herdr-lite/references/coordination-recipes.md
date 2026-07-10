# Agent-to-agent coordination recipes

Ports of the workflows Herdr's own agent skill teaches, expressed with this
cockpit's commands. Run these from any shell (including from inside a
registered pane — an agent can drive its siblings).

## Run a dev server and wait until it is ready

```bash
agent-pane server 'npm run dev'
agent-wait server --match 'ready|listening on' --timeout 120
agent-read server --lines 20
```

## Run tests beside your work and inspect the result

```bash
agent-pane tests 'uv run pytest -q'
agent-wait tests --status done --timeout 600 || agent-read tests --lines 50
agent-read tests --lines 40 --source recent
```

`agent-wait --status` refreshes the scan on each poll; `done` includes the
exit sentinel `AGENT_STATUS: done exit_code=0` that `agent-pane` prints when
the command finishes.

## Spawn a coding agent and hand it a task

```bash
agent-pane --agent claude reviewer 'claude'
agent-wait reviewer --status idle --timeout 60   # wait for the prompt box
agent-run reviewer 'Review the diff in HEAD~1 and list defects only.'
agent-wait reviewer --status done --timeout 1800
agent-read reviewer --lines 100
```

## Answer another agent's permission prompt

```bash
agent-jump blocked                 # or: agent-list to see who is blocked
agent-read backend --lines 15      # read the exact question first
agent-send backend --keys Enter    # accept the highlighted option
# or answer textually without submitting, then submit:
agent-send backend '2'
agent-send backend --keys Enter
```

Read before answering — `blocked` is a routing hint, not a description of
which option is safe.

## Watch a sibling agent robustly

```bash
agent-read worker --lines 30                       # what has already happened
agent-wait worker --match 'AGENT_STATUS: (done|blocked)' --timeout 900
agent-read worker --lines 100 --source recent      # full context on wake
```

## Fan out isolated workers with git worktrees

```bash
agent-worktree create fix-auth --window
agent-pane --agent claude --cwd ~/.tmux/agent-panes/worktrees/myrepo/fix-auth auth 'claude'
agent-worktree create fix-api --window
agent-pane --agent codex --cwd ~/.tmux/agent-panes/worktrees/myrepo/fix-api api 'codex'
# ...later, after merging their branches:
agent-worktree remove fix-auth
agent-worktree remove fix-api
```

## Recover after a tmux server restart

```bash
agent-workspace                       # recreate/attach the cockpit
agent-resume --dry-run                # see what died
agent-resume                          # relaunch everything cold
# or resume one agent's native CLI session:
agent-resume reviewer --session-id 01JXYZ...   # -> claude --resume 01JXYZ...
```

With the optional tmux-resurrect/continuum layer installed
(`assets/tmux-agent-persistence.conf`), layouts and pane contents come back on
their own; `agent-resume` covers re-registering and native session resume.

## Protocol notes

- `agent-list --json` is the machine-readable registry: name, window, pane
  id, agent kind, status, classification reason, timestamps.
- Names are stable; pane ids are not. Resolve targets by name.
- `agent-send` never presses Enter; `agent-run` always does. There is no
  hidden third behavior.
- Statuses are routing hints. Verify artifacts (tests, diffs) before trusting
  `done`.
