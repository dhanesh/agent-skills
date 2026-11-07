# tmux-cockpit: open an agent pane with the command passed as words

- id: tmux-cockpit
- proven: no
- anchors: tmux-agent-herdr-lite/scripts/agent-pane

## What it is

**Status: planned (run 5).** `tmux-agent-herdr-lite/scripts/agent-pane` opens a tmux
window for an agent and registers it for the cockpit. Today it joins the command words
with `$*` (line 40) and puts that text into the `sh -c` wrapper that tmux runs
(line 60). The shell then parses the joined text again, so a word that holds `;`,
`$(...)` or a quote runs as shell code or breaks the wrapper. After run 5, agent-pane
gives the command to the new pane as separate words. Each word reaches the program as
one literal argument. The wrapper still prints `AGENT_STATUS: working name=<name>`
before the command and `AGENT_STATUS: done exit_code=<rc>` after it, and the shell
stays open. agent-pane still writes the pane's metadata JSON and prints the pane id.

## How to reach it

`agent-pane [--agent <kind>] [--cwd <dir>] <name> <command...>`. It reads
`AGENT_TMUX_SESSION` (default `agents`) and `AGENT_TMUX_ROOT` (default
`$HOME/.tmux/agent-panes`). It creates the session if it is absent and runs `tmux` from
`PATH`. It writes `$AGENT_TMUX_ROOT/panes/<pane id without %>.json` and prints the pane
id, such as `%1`. A missing name or command exits 64.

## Drive it

Put a `tmux` wrapper first on `PATH` that sends every call to a private server on a
short relative socket with no config file. Then open three panes, each with one
dangerous command word.

```sh
TC="$WT/.verify-run/$INSTANCE/tc"
mkdir -p "$TC/home" "$TC/work" "$TC/bin"
REAL_TMUX="$(command -v tmux)"
cat > "$TC/bin/tmux" <<WRAP
#!/bin/sh
cd "$TC" && exec "$REAL_TMUX" -S ./s -f /dev/null "\$@"
WRAP
chmod +x "$TC/bin/tmux"
unset TMUX
export HOME="$TC/home" AGENT_TMUX_ROOT="$TC/agents" AGENT_TMUX_SESSION=tc \
  PATH="$TC/bin:$PATH" SHELL=/bin/sh
AP=tmux-agent-herdr-lite/scripts/agent-pane
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tc-semi -- \
  "$AP" --cwd "$TC/work" semi printf '%s\n' 'a;touch tc-semi'
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tc-subst -- \
  "$AP" --cwd "$TC/work" subst printf '%s\n' 'b$(touch tc-subst)'
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tc-quote -- \
  "$AP" --cwd "$TC/work" quote printf '%s\n' "it's ok"
for w in semi subst quote; do
  for i in $(seq 1 50); do
    tmux capture-pane -p -J -t "tc:$w" 2>/dev/null | grep -q 'AGENT_STATUS: done' && break
    sleep 0.2
  done
  tmux capture-pane -p -J -t "tc:$w" > "$TC/pane-$w.txt" 2>&1
done
tmux list-windows -t tc -F '#W' > "$TC/windows.txt" 2>&1
ls -A "$TC/work" > "$TC/work-after.txt"
ls "$TC/agents/panes" > "$TC/meta-files.txt"
tmux kill-server
```

- `tc-semi`, `tc-subst`, `tc-quote`: exit 0, each prints one pane id (`%1`, `%2`, `%3`),
  and the three ids differ.
- `pane-semi.txt`: `AGENT_STATUS: working name=semi`, then the literal line
  `a;touch tc-semi`, then `AGENT_STATUS: done exit_code=0`.
- `pane-subst.txt`: the literal line `b$(touch tc-subst)` between the two
  `AGENT_STATUS:` lines.
- `pane-quote.txt`: the literal line `it's ok` between the two `AGENT_STATUS:` lines.
- `windows.txt`: `control`, `semi`, `subst` and `quote`.
- `work-after.txt`: empty. There is no `tc-semi` and no `tc-subst` file: no command word
  ran as shell code.
- `meta-files.txt`: three JSON files, one for each printed pane id.

## Proof

The three agent-pane captures (exit 0 and three different pane ids), the three pane
texts with the literal words between the `AGENT_STATUS:` lines, `windows.txt` with the
`quote` window alive, `work-after.txt` with no marker file, and `meta-files.txt`.

## Gotchas

- Before the fix (dry run on `eeac771`): `work-after.txt` held `tc-semi` and `tc-subst`,
  so the shell ran `touch` from both words. `pane-semi.txt` showed `an` and
  `pane-subst.txt` showed `bn`: the shell also lost the backslash of `%s\n`. The `quote`
  window closed at once on a shell syntax error, so `tc:quote` gave
  `can't find window: quote`, and `tc-quote` printed `%0`, the pane of the `control`
  window, not a new pane. The metadata `command` field held the joined text, such as
  `printf %s\n a;touch tc-semi`.
- Do not assert the exact text of the metadata `command` field: the fix may store it
  in another form. Assert the pane text and the marker files.
- Never run agent-pane without the wrapper first on `PATH`: it calls plain `tmux`, which
  would open windows on the user's own server. Keep the `-S` path short and relative,
  because a long socket path fails on macOS. Always end with `tmux kill-server`, even
  when a step fails.
- The blocks export `HOME` (and the tmux block changes `PATH`). Run them in a subshell, or
  set them back before the next recipe.
- tmux `#()` format jobs do not run inside the agent sandbox. This recipe does not use
  them.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
