# reentry-timer: install, show and remove the conductor and pipeline timers only with consent

- id: reentry-timer
- proven: no
- anchors: factory-conductor/assets/reentry_timer.py:install, factory-conductor/assets/reentry_timer.py:installed, factory-conductor/assets/reentry_timer.py:uninstall, factory-conductor/assets/reentry.py, factory-conductor/assets/conductor.py:cmd_reentry, factory-conductor/assets/conductor.py:cmd_watch, factory-conductor/assets/conductor.py:_watch_named_run, factory-conductor/SKILL.md, factory-pipeline/assets/pipeline.py:cmd_schedule, factory-pipeline/SKILL.md

## What it is

**Status: planned (run 5).** The skills.sh Gen scanner rates factory-conductor and
factory-pipeline Medium for PERSISTENCE, because both can install an OS timer (launchd,
a systemd user timer or a tagged crontab line, through
`factory-conductor/assets/reentry_timer.py`). After the fix, each SKILL.md says this
plainly:

- factory-conductor: a timer is installed only when the grant's `reentry` block consents.
  `conductor reentry status` shows the installed timer and `conductor reentry uninstall`
  removes it. The timer removes itself after the run: a tick for a finished or missing run
  prints `REENTRY: done` and uninstalls that run's timer.
- factory-pipeline: a timer is installed only when the owner runs `schedule install`
  explicitly. `schedule status` shows it and `schedule uninstall` removes it.

The code does not change in behaviour. `conductor reentry install` with no `reentry`
block prints `REENTRY: disabled`, exits 3 and writes no timer.

## How to reach it

`python3 factory-conductor/assets/conductor.py reentry install|status|uninstall --root
<repo>` and `python3 factory-conductor/assets/conductor.py watch --root <repo> --run
<run-id>`. reentry_timer has test hooks that install no real timer:
`FACTORY_CONDUCTOR_TIMER_DRYRUN=1` runs no `launchctl`, `systemctl` or `crontab`,
`FACTORY_CONDUCTOR_TIMER_HOME=<dir>` puts the timer files under `<dir>` in place of the
real HOME, and `FACTORY_CONDUCTOR_TIMER_KIND=launchd` forces the kind. With launchd the
timer is the file `<dir>/Library/LaunchAgents/io.agent-skills.factory-conductor.<run-id>.plist`.

## Drive it

Build a gated run (its grant has no `reentry` block), try to install, then add a newer
human grant with a `reentry` block through the conductor's own test kit, and install,
show and remove the timer. Last, install again and let `watch` remove the timer of a run
that is gone. Every timer file goes to a temp home in the instance directory.

```sh
I="$WT/.verify-run/$INSTANCE"
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name rt --feature reentry-timer=a.txt
RT="$I/rt"
mkdir -p "$I/timer-home"
export FACTORY_CONDUCTOR_TIMER_DRYRUN=1 FACTORY_CONDUCTOR_TIMER_HOME="$I/timer-home" FACTORY_CONDUCTOR_TIMER_KIND=launchd
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-no-consent -- \
  python3 factory-conductor/assets/conductor.py reentry install --root "$RT"
find "$I/timer-home" -type f > "$I/rt-no-consent-files.txt"
cat > "$I/grant_reentry.py" <<'EOF'
import glob, os, sys
assets, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, assets)
import conductor_testkit as K
plan = sorted(glob.glob(os.path.join(root, ".skill-contract", "envelopes", "task-plan-v1-*.json")))[-1]
print(K.write_grant(root, plan, reentry={"agent_cmd": [sys.executable, "-c", "pass", "{prompt}"],
                                         "interval_min": 10, "stall_min": 30, "max_reentries": 2}))
EOF
python3 "$I/grant_reentry.py" "$WT/factory-conductor/assets" "$RT"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-install -- \
  python3 factory-conductor/assets/conductor.py reentry install --root "$RT"
find "$I/timer-home" -type f > "$I/rt-install-files.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-status -- \
  python3 factory-conductor/assets/conductor.py reentry status --root "$RT"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-uninstall -- \
  python3 factory-conductor/assets/conductor.py reentry uninstall --root "$RT"
find "$I/timer-home" -type f > "$I/rt-uninstall-files.txt"
python3 factory-conductor/assets/conductor.py reentry install --root "$RT"
RID="$(ls "$RT/.skill-contract/runs")"
mv "$RT/.skill-contract/runs/$RID" "$I/rt-moved-run"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-watch-done -- \
  python3 factory-conductor/assets/conductor.py watch --root "$RT" --run "$RID"
find "$I/timer-home" -type f > "$I/rt-watch-files.txt"
unset FACTORY_CONDUCTOR_TIMER_DRYRUN FACTORY_CONDUCTOR_TIMER_HOME FACTORY_CONDUCTOR_TIMER_KIND
```

Then check the SKILL.md sentences. `sentences.py` joins the file into one line, splits it
into sentences, and prints the sentences that hold every given word. It exits 0 when one
or more match.

```sh
cat > "$I/sentences.py" <<'EOF'
import re, sys
text = re.sub(r"\s+", " ", open(sys.argv[1], encoding="utf-8").read())
words = [w.lower() for w in sys.argv[2:]]
hits = [s for s in re.split(r"(?<=[.!?])\s+", text) if all(w in s.lower() for w in words)]
for s in hits:
    print("SENTENCE:", s[:300])
print("SENTENCES: %d match %s" % (len(hits), " + ".join(words)))
sys.exit(0 if hits else 1)
EOF
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-consent -- \
  python3 "$I/sentences.py" factory-conductor/SKILL.md timer only reentry
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-show -- \
  python3 "$I/sentences.py" factory-conductor/SKILL.md "reentry status" timer
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-remove -- \
  python3 "$I/sentences.py" factory-conductor/SKILL.md "reentry uninstall" removes
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-self -- \
  python3 "$I/sentences.py" factory-conductor/SKILL.md "REENTRY: done" timer
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-pipe-consent -- \
  python3 "$I/sentences.py" factory-pipeline/SKILL.md "schedule install" owner
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-pipe-show -- \
  python3 "$I/sentences.py" factory-pipeline/SKILL.md "schedule status" timer
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rt-doc-pipe-remove -- \
  python3 "$I/sentences.py" factory-pipeline/SKILL.md "schedule uninstall" removes
```

Expected observations:

- `rt-no-consent`: exit 3 and `REENTRY: disabled`. `rt-no-consent-files.txt` is empty: no
  timer without the grant's `reentry` block.
- `rt-install`: exit 0 and `REENTRY: installed every 10 min`. `rt-install-files.txt`
  holds one plist under `$I/timer-home/Library/LaunchAgents/` whose name ends
  `<run-id>.plist`.
- `rt-status`: exit 0 and a `REENTRY: timer <path of that plist>` line, then the
  `REENTRY: lease`, `REENTRY: count 0` and `REENTRY: last none` lines.
- `rt-uninstall`: exit 0 and `REENTRY: uninstalled`. `rt-uninstall-files.txt` is empty.
- `rt-watch-done`: exit 0 and `REENTRY: done`. `rt-watch-files.txt` is empty: the tick
  for a run that is gone removed that run's timer.
- `rt-doc-consent`: exit 0, n of 1 or more. The `SENTENCE:` line says a timer is
  installed only when the grant's `reentry` block allows it.
- `rt-doc-show`, `rt-doc-remove` and `rt-doc-self`: exit 0 each.
- `rt-doc-pipe-consent`, `rt-doc-pipe-show` and `rt-doc-pipe-remove`: exit 0 each.
- Nothing new under the real `~/Library/LaunchAgents`, and no `crontab` or
  `systemctl --user` change: the dry-run hooks were set for every timer call.

Before the fix (dry run on `factory/run5-base`, `eeac771`): the code captures all match
the expected lines above (`REENTRY: disabled` exit 3 with no file; `installed every 10
min` with one plist; `REENTRY: timer <plist>`; `uninstalled` with no file;
`REENTRY: done` with no file). `rt-doc-consent` exits 1 with
`SENTENCES: 0 match timer + only + reentry`: the conductor SKILL.md says "When the grant
carries a `reentry` block, run ... reentry install", but no sentence says a timer is
installed only then. `rt-doc-show`, `rt-doc-remove` and `rt-doc-self` exit 0. The
pipeline sentences exist today ("You MUST NOT run `schedule install` unless the owner
asked ...", `schedule status`, `schedule uninstall`), so the three `rt-doc-pipe-*`
captures exit 0 (regression guards).

## Proof

The `rt-no-consent` capture and its empty file list show that no consent gives no timer.
The `rt-install`, `rt-status` and `rt-uninstall` captures and their file lists show the
install, the command that shows the timer and the command that removes it. The
`rt-watch-done` capture and its empty file list show that the timer removes itself after
the run. The seven `rt-doc-*` captures show the SKILL.md sentences.

## Gotchas

- Never run these commands without the three `FACTORY_CONDUCTOR_TIMER_*` variables: they
  would install a real launchd, systemd or cron timer. Run the `unset` line before you
  leave the shell.
- `rt-watch-done` moves the run directory out of `.skill-contract/runs/` to stand for a
  run that is gone. A finished run gives the same `REENTRY: done`, but finishing the
  fixture run needs a push and a pull request, which this offline recipe cannot do.
- The gated-run grant sets `local_reversible: grant`, so the newer grant covers the
  install. A grant that asks prints a `GATE: ASK` line and exits 3.
- The pipeline's `schedule install` drive (install at 15 and 1440 minutes, refuse 10 and
  1500, uninstall) is in `features/pipeline-tick.md` (the `pt-sched-*` captures). This
  recipe checks only the pipeline SKILL.md sentences, so the two recipes do not repeat
  each other.
- A sentence check matches words, not exact wording. When a match is found, read the
  `SENTENCE:` line and confirm it says what the feature claims.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
