# growth.py commands and flags

Run every command as `python3 "$SKILL_DIR/assets/growth.py" --root <repo> <command> …`.
`--root` comes before the command and defaults to the current directory. `--today YYYY-MM-DD`
sets "today" for tests only. Outside the test suite, a command with `--today` prints `STOP:`
and exits 2.

| Command | Flags | Effect | Prints | Exit |
|---|---|---|---|---|
| `lint [PLAN]` | `PLAN` defaults to `growth/metrics.md` | checks the plan | `LINT: ok <n> metrics`, or `PROBLEM:` lines and `LINT: fail <k> problems` | 0, 2 |
| `register` | `--by NAME` (required, not `agent:…` or a lookalike), `--reason TEXT` (required when the thresholds changed since the last registration, or when `plan.json` holds no registration but the log or the data remembers one) | freezes the parsed thresholds | `LINT:`, `REGISTERED: <sha12> by <name> at <time>, <n> metrics`, `NEXT:`; on a refusal `NOTE:` and `STOP:` | 0, 2, 3 (lock) |
| `import FILE` | `FILE` is a path, or `-` for stdin | stores the JSON-lines records | `PROBLEM:` lines (at most 50, then `PROBLEM: and <k> more`), `IMPORT: added <a> corrected <c> unchanged <u> problems <p>`, `NEXT: review` | 0, 2, 3 (problem records or lock) |
| `review` | none | judges each registered metric | `METRIC:`, `CORRECTED:`, `NOTE:`, `REVIEW:`, `NEXT:` | 0, 2, 3 (breach, stale, a corrected or unjudged latest period, a registration changed by hand, or lock) |
| `signals` | `--source NAME` (an ops-intake source name, not `release`) | prints one intake-signals-jsonl line per breach | JSON lines on stdout only; `NOTE:`/`STOP:` and a final `SIGNALS: <k>` on stderr | 0, 2, 3 (a deleted registration the log remembers, or lock) |

Exit codes:
- **0**: OK.
- **2**: refused input, a bad plan, no registration yet, an unreadable or malformed state
  file, a symlinked state file, or a state directory that cannot be shown to be git-ignored.
  Nothing changed.
- **3**: attention. `review`: a BREACH, a STALE plan, a `CORRECTED:` line or an unjudged
  record for the latest complete period, or `plan.json` and `log.jsonl` that disagree about
  the latest registration. `review` and `signals`: `plan.json` holds no registration but the
  log records one. `import`: some records were problems (the others imported). Any command:
  the growth lock was held for 900 seconds.

Every write takes an exclusive lock on `.skill-contract/growth/.lock`, after the tool proves
`.skill-contract/growth/` is git-ignored: the directory holds its own `.gitignore` with `*`
and no `!` line, and neither it nor `.skill-contract` is a symlink. `plan.json`,
`data.jsonl`, `log.jsonl` and `.lock` are refused when they are symlinks.
