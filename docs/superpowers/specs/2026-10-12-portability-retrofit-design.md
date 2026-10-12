# Portability retrofit: one way to find Python

**Status:** Designed unattended on 2026-10-12. The owner said "start the portability retrofit". Jev scored each decision, and the Opus advisor reviewed the design. This closes gap 9 of `docs/factory/2026-09-19-assessment.md`: "hard-coded `python3` in SKILL.md commands; the macOS python3 stub".

**Goal:** A skill that runs Python works on any POSIX machine that has Python 3.10 or newer, whatever the interpreter is called. Today the skills assume `python3`. That fails in two cases:

- The machine has only `python`. Some Linux images, pyenv and conda setups are like this.
- The machine is a Mac without the Command Line Tools. There `/usr/bin/python3` is a stub. Running it opens an install dialog and exits non-zero. Before it exits, a headless run can be stuck behind that dialog.

## Decisions made unattended

| # | Decision | Jev | Notes |
|---|---|---|---|
| P1 | **Keep the literal `python3` in every command, and add one shared rule that says when to use something else.** | A 0.99 | A `{python}` placeholder or a launcher script would change every command. That would break the Claude Code permission rules that the conductor's re-entry depends on (`Bash(python3 /abs/…/conductor.py *)`), because rules match the command text before any variable expands. The common machine keeps working with no change. |
| P2 | **Hooks and installers find the interpreter with one small `sh` function.** | H1 1.00 | |
| P3 | **Add the macOS stub guard and the new override to skill-contract's `resolve_python()`, and re-vendor it.** | 0.66 (uncertain); decided by the advisor | The conductor resolves `{python}` in every verify command through this function (`factory-conductor/assets/conductor.py:2241`). On a Mac without the Command Line Tools, an unattended run would probe the stub at every verify. So the guard is on the factory path, not optional. The three places must use one definition of the stub, or they drift. |
| P4 | **Native Windows is out of scope.** | 0.13 | The hooks are bash, and factory-conductor does not start on Windows. The scope is macOS, Linux and WSL. `py -3` stays in the lookup because it costs nothing and skill-contract already has it. |
| P5 | **Leave the `#!/usr/bin/env python3` shebangs alone.** | 0.22 | No SKILL.md runs a helper through its shebang. |
| P6 | **Three stacked PRs:** (1) resolver and vendor; (2) hooks and installers; (3) the SKILL.md rule and its gate. | three 0.76 (conf. 0.64) | Each PR stays small enough to review. The owner merges each one. |
| P7 | **The shared rule is plain prose, with no BCP 14 keywords.** | 0.12 | It is a default procedure, not a hard rule (PP-7 review item 7). So it needs no register rows. |

Only code facts and design options went to Jev.

## 1. One definition of "the interpreter"

All three places use this lookup, in this order. The first candidate that passes wins.

1. `SKILL_CONTRACT_PYTHON`, when set. Only skill-contract's resolver reads it, as it does today, so existing setups keep working.
2. `SKILL_PYTHON`, when set. This is the new override for every skill.
3. `python3`, unless it is the macOS stub.
4. `python`.
5. `py -3`. Only the Python resolver and the SKILL.md rule use it. The `sh` function does not, because it is POSIX only.

**The stub test is exact.** A `python3` candidate is the stub when all three of these are true:
- the platform is macOS (`sys.platform == "darwin"`, or `uname -s` prints `Darwin`);
- `python3` resolves to `/usr/bin/python3` (so a Homebrew, pyenv or conda `python3` earlier on PATH still wins);
- `xcode-select -p` exits non-zero.

A stub candidate is skipped without being run, so the dialog never opens.

**"Passes"** means the candidate runs `-c 'import sys; sys.exit(sys.version_info < (3, 10))'` with exit 0. skill-contract already probes this way, with `-I` and a 10-second timeout. The hooks do not probe the version, because they run on every tool call and an extra process each time is too slow. A hook that has the wrong Python fails softly, as it does today.

## 2. PR 1: the resolver (skill-contract and its 8 adopters)

- `docs/skill-contract/reference/contract_check.py`: `resolve_python()` reads `SKILL_PYTHON` after `SKILL_CONTRACT_PYTHON` and applies the stub test to a `python3` candidate. The `xcode-select` call has its own short timeout. If it cannot run, the candidate is not treated as the stub. The docstring names the full order.
- `make contract-vendor` copies it into the 8 adopters. The gate already pins the copies byte-identical.
- Unit tests in the reference suite fake `sys.platform`, `shutil.which` and `subprocess.run`. They cover these cases:
  - the stub is skipped;
  - a Homebrew `python3` is kept;
  - the stub is kept when `xcode-select -p` succeeds;
  - `SKILL_PYTHON` wins over `python3`;
  - `SKILL_CONTRACT_PYTHON` wins over `SKILL_PYTHON`;
  - Linux is unchanged.
- The "Runtime lookup for `{python}`" text changes in three places: `docs/skill-contract/SPEC.md` (if it states the order), the skill-contract design doc, and the two SKILL.md lines that list the order (crafting-self-prompting-loops and spec-first-planning). Those lines change to name `SKILL_PYTHON` and the stub. They are prose without keywords, so they need no register rows.
- Version bumps: a patch for every adopter, with a README note.

## 3. PR 2: hooks and installers

**Scope:** the bash hooks of world-model-ledger and context-hygiene-kit, and the `scripts/install.sh` of world-model-ledger, context-hygiene-kit and tmux-agent-herdr-lite.

- One file, `assets/find_python.sh`, defines `find_python`. It sets `PY` to the interpreter, or to empty when none is found. Each kit that needs it carries an identical copy, and a test pins the copies together, as `test_ledger.py` does for the agent-name check. A hook sources the copy next to it. An installer sources the copy in its own skill.
- `SKILL_PYTHON` in the `sh` function is one executable, a name or a path, with no arguments. The function uses it only when `command -v` finds it. This limit is documented in the READMEs.
- Every `python3` in a hook changes to `"$PY"`, including `python3 -c` and `python3 - <<'PY'`. When `PY` is empty, a hook exits 0 quietly, as it does today when a step fails. An installer stops with a clear message: "this skill needs Python 3.10 or newer; set SKILL_PYTHON or install python3".
- The stub test in `sh` compares `command -v python3` with `/usr/bin/python3`. The tests set an internal variable to point that comparison at a fake path. They also put fake `uname` and `xcode-select` commands on PATH. The variable is named only in the tests and the READMEs, never in a SKILL.md, reference or STOP line.
- Version bumps and README notes for the three skills.

## 4. PR 3: the shared SKILL.md rule and its gate

**The rule.** Each SKILL.md that tells the agent to run Python gets this paragraph, byte-identical, in its setup section, before the first command:

> **Python interpreter.** Most commands in this skill and its references are written with `python3`. Find the interpreter once, before the first of them. If `$SKILL_PYTHON` is set, use it. Otherwise, on macOS, if `command -v python3` prints `/usr/bin/python3` and `xcode-select -p` fails, that `python3` is the install stub: skip it without running it. Use the first of `python3`, `python` and `py -3` that is not skipped and for which `<interpreter> -c 'import sys; sys.exit(sys.version_info < (3, 10))'` exits 0. Write the interpreter you found in place of `python3` in every command, and in any `Bash(...)` permission rule you suggest. Commands that start with `uv run` or `{python}` keep their own form. In a session resumed under a `Bash(...)` allowlist, skip this lookup and use the interpreter that the allowlist names. If none passes, follow this skill's own fallback for running without Python where it gives one; otherwise stop and tell the user that this skill needs Python 3.10 or newer.

2026-10-12, after the PR 3 review: the rule now leaves `uv run` and `{python}` commands alone, skips the lookup in a session resumed under an allowlist (so unattended re-entry is not stalled by a refused probe), and defers to a skill's own no-Python fallback.

**Which SKILL.md files.** A skill "runs Python" when its SKILL.md body or a `references/*.md` file has a command line that starts with `python3` or `python`. That is 24 skills today. The two skills that mention Python only in frontmatter (clean-code and tmux-agent-herdr-lite) do not get the rule. A frontmatter `compatibility:` line never counts.

**The re-entry permission rule.** The factory-conductor SKILL.md "Scheduled re-entry" section and `spec-first-planning/references/unattended.md` (the `agent_cmd` example and the allowlist advice) change to say that the `Bash(...)` rule names the interpreter that the lookup found. Otherwise a resumed session on a `python`-only machine stalls on a permission prompt. The timer's own `watch` command already uses `sys.executable`, so it needs no change.

**The gate.** A new check, `scripts/gates/python-interpreter.sh`, runs per skill in `make gate`. It fails when a skill runs Python (as defined above) and its SKILL.md does not contain the rule byte-for-byte. Gate self-tests plant a missing rule and an edited rule. CLAUDE.md's gate table gets a row.

**Version bumps** for the 24 skills, with a README note each. The PR body carries the semantic-review checklist for every SKILL.md touched.

## 5. Out of scope

- Repository tooling: `scripts/gates/*.sh`, the Makefile, CI, and the test and smoke scripts (mockstar's `test_smoke*.sh`, for example). They run on CI's Ubuntu and on the owner's Mac.
- Native Windows (P4) and the shebangs (P5).
- Commands inside fenced example output (for example the `VERIFY:` lines in `run-protocol.md`), which show what a tool printed.

## 6. Proof

- **A/B rows** under a new `SINCE_PORTABILITY` constant, one campaign per PR:
  - PR 1: `resolve_python()` with a faked darwin, a `/usr/bin/python3` and a failing `xcode-select`. The baseline returns the stub, and the branch skips it.
  - PR 2: each hook and installer run with a PATH that holds only a `python` shim. The baseline fails or does nothing, and the branch works. The macOS stub uses the fake `uname`, the fake `xcode-select` and the internal path variable.
  - PR 3: the rule is present in each of the 24 SKILL.md files. This is prompt-only, so the row checks the text, not model behaviour. The spec says so plainly, as `CLAUDE.md` requires.
- `make gate`, `make bcp14`, `make readme`, `make contract` and `make ab-validate` (0 WORSE, 0 UNPROVEN) on each PR. CI on Ubuntu is the check of record. The macOS stub path is proven only with fakes, because no CI runner lacks the Command Line Tools.

## 7. Residual risks

- An agent can ignore the prose rule. Only the hooks, the installers and the contract checker enforce the lookup in code.
- `SKILL_PYTHON` with arguments (for example `py -3`) works in the prose rule and in skill-contract, but not in the `sh` function. The READMEs say so.
- Python is optional, or comes from `uv`, in four skills: crafting-self-prompting-loops, feynman-walkthrough, mockstar-mock and base-in-reality. The rule defers to their own fallbacks for running without Python, so it does not stop them where Python is not needed.
- A skill that worked on Python 3.8 or 3.9 now tells the agent to stop. All of the repository's test suites run on 3.10 and newer, so no supported setup is lost.
