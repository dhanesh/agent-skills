# Spec: Scanner ratings and audit fixes (run 5)

## Problem
skills.sh shows three security scanners for each skill of this repository. On
2026-11-07 they rated `okf-site-kit` Critical (Gen), `ops-intake` and
`security-posture-audit` High (Gen), `factory-conductor` and `factory-pipeline` Medium
(Gen), four skills with Socket alerts and ten skills Medium on Snyk. The owner wants
the ratings to improve. The scanners read the published files. A rating moves only when
the text or code that it flagged changes, and only after skills.sh scans the repo again.

The causes, read from each scanner's detail page and checked against `main` at
`eeac771`:

- `okf-site-kit` (Critical): one link to a domain that a reputation service blocklists,
  in `references/bundle-variations.md`. The audit of 2026-11-06 also found three
  content-integrity defects in `okf_site.py`: unescaped MDX attributes, script-scheme
  links and raw HTML copied from bundle bodies, and an asset copy that follows symlinks.
- `ops-intake` (High): test and eval fixtures that contain a remote download piped to a
  shell, and literal invisible Unicode characters in one test.
- `security-posture-audit` (High): detector fixtures with a remote download piped to a
  shell, in test and eval sources, and no explicit data boundary in SKILL.md.
- `factory-conductor`, `factory-pipeline` (Medium): they install OS timers. SKILL.md
  does not say plainly when a timer is installed and how to remove it.
- Socket: `tmux-agent-herdr-lite` splices the agent command into an `sh -c` wrapper with
  `$*`; the `crafting-self-prompting-loops` human-checkpointed template acts on any
  decision other than TIMEOUT or BLOCK; `world-model-ledger`'s eval grader runs candidate
  code and calls that a sandbox; `test-safety-net` runs the analyzed repo's TypeScript by
  default.

The audit of 2026-11-06 and its validation of 2026-11-07 also confirmed these defects:

- `okf.py` and `garden.py` run `git status` in a source pin's locator with no hardening,
  so an embedded bare repo's `core.fsmonitor` command runs (seen on `main`).
- Both tools pass a pinned fingerprint from bundle frontmatter to `git diff`, so a value
  such as `--output=<path>` is read as an option and creates a file (seen on `main`).
- The `jev` CLI resolves its SDK with uv's config discovery from the working directory
  and an open version range.
- Hook kits and tmux cockpit scripts start Python from stdin or `-c` with the working
  directory first on `sys.path`, so a planted `json.py` runs.

## Users
- The repository owner (Dhanesh Purohit), who merges the run's pull request and then the
  pull request from `factory/run5-base` to `main`.
- People who install these skills and see the skills.sh ratings.
- The executors, reviewers and verifiers of this run.

## Goals
- Every flagged pattern that a code or wording change can fix is gone from the files that
  the scanners read.
- Every confirmed audit defect is fixed, with a test that fails on the old code.
- No skill loses a behaviour that its tests or evals prove today.

## Non-goals
- Changing the ratings directly. skills.sh rescans on its own schedule; this run cannot
  trigger a rescan.
- Splitting or disguising strings so that a scanner does not see them. Detector
  fixtures that need a risky pattern keep it, in labelled fixture files.
- Snyk W011 ("reads untrusted content") on nine skills. Reading that content is what the
  skills do; the run report lists each one with the control that handles it (owner
  decision, 2026-11-07).
- Snyk W009 on `release-conductor` ("money access"). No payment code exists; the report
  names it as a false positive.
- The tmux `new-window -c` format leads. They wait for the owner's tmux check.
- Any human gate removed, any new timer, any new dependency.

## Constraints
- B1 [invariant]: No change removes a human gate: merge, production deploy, rollback, spend, external messages and delete stay with the owner.
- B2 [goal]: Each flagged pattern that a change can fix is absent from the published files after the run.
- T1 [boundary]: New code is stdlib-only Python or POSIX shell, and its tests and evals run offline inside `make gate`.
- T2 [invariant]: A fix changes behaviour only where the requirement says so; every other output line, flag and file format keeps its meaning.
- T3 [invariant]: No fix disguises a string to hide it from a scanner; a needed risky pattern stays visible in a labelled fixture file.
- S1 [invariant]: Content from a target repo, a bundle or a working directory cannot run code or write a file outside the tool's output through the fixed paths.
- O1 [goal]: Each skill whose behaviour this run changes has an A/B row that moved, and `make ab-validate` passes.

## Tensions
- TN1 [trade_off]: The posture-audit detector needs a download-to-shell pattern in its fixtures, against the goal of removing that pattern from published files. (between: B2, T3; status: resolved; strategy: Partition; decision: D26)
- TN2 [trade_off]: Precise Node test discovery is more accurate but runs the analyzed repo's TypeScript. (between: S1, T2; status: resolved; strategy: Prioritize; decision: D25)
- TN3 [trade_off]: `--no-config` stops a repo from choosing jev's index, but also ignores the owner's own uv user config; an unpinned SDK stays current but installs each release unreviewed. (between: S1, T2; status: resolved; strategy: Prioritize; decision: D23)

## Required truths
- RT1 [SPECIFICATION_READY]: The OKF tools never let a source pin run git config or pass an option to git (parent: OUTCOME; maps_to: S1, T1, T2; reqs: R1, R2, R3, R4; confidence: 0.85; check: the four OKF test files exit 0)
- RT2 [SPECIFICATION_READY]: Python started by the hook kits, the grader and the tmux scripts imports nothing from the working directory (parent: OUTCOME; maps_to: S1, T1; reqs: R9, R10, R11; confidence: 0.85; check: the isolation tests exit 0)
- RT3 [SPECIFICATION_READY]: jev resolves the latest SDK with no project config, and sends images and box questions only on opt-in (parent: OUTCOME; maps_to: S1, T2; reqs: R5, R6, R7, R8; confidence: 0.85; check: the jev test exits 0)
- RT4 [SPECIFICATION_READY]: The flagged patterns in okf-site-kit, ops-intake and security-posture-audit are gone or labelled (parent: OUTCOME; maps_to: B2, T3; reqs: R13, R17, R18; confidence: 0.8; check: the fixture and link tests exit 0)
- RT5 [SPECIFICATION_READY]: The Socket-flagged code paths take words not shell text, act only on approval, and run repo code only on opt-in (parent: OUTCOME; maps_to: S1, B1, T2; reqs: R12, R19, R20; confidence: 0.75; check: the tmux, template and tsn tests exit 0)
- RT6 [SPECIFICATION_READY]: The site builder keeps hostile bundle content inert (parent: OUTCOME; maps_to: S1, T1; reqs: R14, R15, R16; confidence: 0.75; check: the okf_site tests exit 0)
- RT7 [SPECIFICATION_READY]: The timer skills say when a timer exists and how to remove it (parent: OUTCOME; maps_to: B1, B2; reqs: R21; confidence: 0.7; check: the SKILL.md greps and gates pass)
- RT8 [SPECIFICATION_READY]: Each changed skill has an A/B row that moved (parent: OUTCOME; maps_to: O1, T1; reqs: R22; confidence: 0.75; check: make ab-validate prints AB_RESULT: PASS)

## Solution options
- OPT-A: Fix each flagged path at its source: hardened git argv and a SHA check, `-I` on every inline interpreter, argv passing in agent-pane, an approve-only gate, opt-in precise discovery, escaped site output, labelled fixture files, and plain SKILL.md wording for timers. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1, RT2, RT3, RT4, RT5, RT6, RT7, RT8)
- OPT-B: Remove or rename the flagged files and strings so that the scanners no longer match, and leave the code paths as they are. (complexity: Low; reversibility: TWO_WAY; satisfies: RT4, RT8)

Recommended: OPT-A — it is the only option that satisfies every truth. OPT-B breaks T3 and S1: it hides patterns and leaves the confirmed defects in place.

## Decisions
- D5: Where does the schedule run? -> On the owner's always-on machine with the conductor's timer kinds; no hosted scheduler (source: AC10 owner decisions, 2026-10-13)
- D7: May the work add a dependency? -> No. Stdlib-only Python and POSIX shell (source: CLAUDE.md house style; owner decision sweep, 2026-11-04)
- D8: May it change a public interface, schema or data format? -> Only where a requirement says so: the new `--precise` opt-in and the approve-only gate change defaults on purpose; every other change is by addition (source: owner answers, 2026-11-07)
- D9: May it add a service or scheduled job? -> No. The run installs no timer and starts no service (source: owner decision sweep, 2026-11-04)
- D10: Expiry and budget? -> 3 days; max_parallel 2; max_repairs_per_task 2; max_dispatches derived by the conductor; wall clock 1440 minutes (source: owner answer, 2026-11-07, same as run 4)
- D11: Enforce a token cap? -> No. Tokens are recorded, not enforced; the dispatch cap is the cost bound (source: owner answer, 2026-11-07, same as run 4)
- D12: Re-entry if the session dies? -> Yes, headless claude by absolute path, every 10 minutes, stall 30, at most 5 re-entries, no permission-bypass flag (source: owner answer, 2026-11-07, same as run 4)
- D13: May the run consult a System One model? -> Yes, Jev, for naming and test-case selection only, with requirement text and file names only (source: owner answer, 2026-11-07, same as run 4)
- D14: Which branches does the grant cover? -> factory/* (source: owner decision sweep, 2026-11-04)
- D16: Who owns `.factory/check-commands.txt`? -> The owner. A run task may read it but MUST NOT edit it (source: owner answer, 2026-11-04)
- D23: How does jev resolve its SDK? -> Shebang `uv run --quiet --no-config --script`, dependency `typesafe-sdk>=0.7.5` with no upper bound, so jev always gets the latest SDK and a skill update never needs a pin bump; the owner accepts that jev ignores the owner's own uv user config, and that each new SDK release installs with no review (source: owner answers, 2026-11-07)
- D29: Does jev support Box questions and Image assets? -> Yes. A `--images` flag sends the `TypeSafe-Danger-Zone-Images: true` header and turns each `{"image": "<path>"}` placeholder into an SDK `Image` read from that regular file; without `--images`, a box question or an image placeholder exits 2 before any call; the log keeps each image as its path and sha256; `jev log` shows box answers; jev-instructions.md gains a Box row and the rule that images go out only with `--images` (source: owner answer, 2026-11-07)
- D24: What happens to the blocklisted link in okf-site-kit? -> Remove the URL and keep the sentence (source: owner answer, 2026-11-07)
- D25: Does test-safety-net run repo TypeScript by default? -> No. Precise Node discovery becomes opt-in with an explicit flag; the default runs no repo code (source: owner answer, 2026-11-07)
- D26: How do detector fixtures that need a risky pattern stay? -> In labelled fixture data files under the skill's `assets/fixtures/`, read by the tests; never split or encoded to hide them (source: owner scope answer and constraint T3, 2026-11-07)
- D27: What does the run do about Snyk W011 and W009? -> Nothing in code. The run report lists each one with its control (source: owner answer, 2026-11-07)
- D28: Which leads join the audit fixes? -> The tmux `python3 -` heredocs and the OKF fingerprint option (source: owner answer, 2026-11-07)

## Requirements
- R1: Every git command that `feynman-walkthrough/assets/okf.py` runs must carry `-c core.fsmonitor=false -c core.hooksPath=/dev/null`. [where: feynman-walkthrough/assets/okf.py, feynman-walkthrough/assets/test_okf_git_safe.py] [feature: knowledge-git-pins] [proof: an agent runs okf status and okf diff on a pin whose locator is an embedded bare repo with a core.fsmonitor marker command, and sees no marker and a git-argv log with no unsafe call] [parallel-safe]
- R2: Every git command that `knowledge-gardener/assets/garden.py` runs must carry `-c core.fsmonitor=false -c core.hooksPath=/dev/null`. [where: knowledge-gardener/assets/garden.py, knowledge-gardener/assets/test_garden_git_safe.py] [feature: knowledge-git-pins] [proof: an agent runs garden.py sweep over the same fixture and sees no marker and no unsafe git call] [parallel-safe]
- R3: `okf.py` must report a git pin whose fingerprint is not a 40-hex commit SHA, with an optional `+dirty` suffix, as an error for that source, and must pass no such value to git. [where: feynman-walkthrough/assets/okf.py, feynman-walkthrough/assets/test_okf_fingerprint.py] [feature: knowledge-git-pins] [proof: an agent sets a pin's fingerprint to --output=<dir>/clobber, runs okf status and okf diff, and sees the source reported as an error and no clobber file] [after: R1]
- R4: `garden.py` must report a git pin whose fingerprint is not a 40-hex commit SHA, with an optional `+dirty` suffix, as an error for that source, and must pass no such value to git. [where: knowledge-gardener/assets/garden.py, knowledge-gardener/assets/test_garden_fingerprint.py] [feature: knowledge-git-pins] [proof: an agent runs garden.py sweep on the same bad pin and sees an error for that source and no clobber file] [after: R2]
- R5: The `jev` shebang must be `#!/usr/bin/env -S uv run --quiet --no-config --script` and its PEP 723 dependency must be `typesafe-sdk>=0.7.5` with no upper bound. [where: jev-agent-setup/assets/jev, jev-agent-setup/assets/test_jev_script.py, jev-agent-setup/SKILL.md] [feature: jev-cli] [proof: an agent asks uv for its settings with the shebang's words from a folder whose uv.toml names index.invalid, and sees no index.invalid; the installer copies the same first lines] [parallel-safe]
- R6: `jev` must send a box question or an `{"image": "<path>"}` placeholder only when `--images` is given, must then send the `TypeSafe-Danger-Zone-Images: true` header and an SDK `Image` read from that regular file, and must exit 2 before any call otherwise. [where: jev-agent-setup/assets/jev, jev-agent-setup/assets/test_jev_images.py] [feature: jev-cli] [proof: an agent runs jev against a stub SDK with a box request, without and with --images and with a directory placeholder, and sees exit 2 and no call, then the header and an Image, then exit 2] [after: R5]
- R7: The `jev` log must record each image as its path and sha256 and never its bytes, and `jev log` must show a box answer's four edges and its confidence. [where: jev-agent-setup/assets/jev, jev-agent-setup/assets/test_jev_images.py] [feature: jev-cli] [proof: an agent runs jev log -v after the stub box call and sees top, bottom, left, right and the confidence, and the log file holds no image bytes] [after: R6]
- R8: `jev-agent-setup/assets/jev-instructions.md` must list Box as a decision kind and must state that images are sent only with `--images` and only from files the user chose. [where: jev-agent-setup/assets/jev-instructions.md, jev-agent-setup/assets/test_jev_instructions.py, jev-agent-setup/SKILL.md, jev-agent-setup/README.md] [feature: jev-cli] [proof: an agent installs into a scratch home and reads the Box row and the images rule in the installed instruction block] [after: R7]
- R9: Every inline Python call in context-hygiene-kit's hooks and installer (`"$PY" -`, `"$PY" -c`) must run with `-I`. [where: context-hygiene-kit/assets/hooks/session_start.sh, context-hygiene-kit/assets/hooks/stop.sh, context-hygiene-kit/scripts/install.sh, context-hygiene-kit/assets/test_hooks_isolation.py] [feature: python-isolation] [proof: an agent runs each hook with a working directory that holds a planted json.py marker module and sees no marker and the hook's normal output] [parallel-safe]
- R10: Every inline Python call in world-model-ledger's hooks and installer must run with `-I`, and `world-model-ledger/eval/grade.py` must run each candidate with `-I` in a fresh temporary working directory and must not call that a sandbox. [where: world-model-ledger/assets/hooks/session_start.sh, world-model-ledger/assets/hooks/stop.sh, world-model-ledger/assets/hooks/pretooluse.sh, world-model-ledger/assets/hooks/posttooluse-observe.sh, world-model-ledger/scripts/install.sh, world-model-ledger/eval/grade.py, world-model-ledger/assets/test_isolation.py] [feature: python-isolation] [proof: an agent runs each hook and the grader from a working directory with planted modules and sees no marker, the hooks' normal output and an unchanged grade] [parallel-safe]
- R11: Every inline Python call in `tmux-agent-herdr-lite/scripts/` must run with `-I`. [where: tmux-agent-herdr-lite/scripts/agent-dashboard, tmux-agent-herdr-lite/scripts/agent-explain, tmux-agent-herdr-lite/scripts/agent-jump, tmux-agent-herdr-lite/scripts/agent-list, tmux-agent-herdr-lite/scripts/agent-pane, tmux-agent-herdr-lite/scripts/agent-resume, tmux-agent-herdr-lite/scripts/agent-status-scan, tmux-agent-herdr-lite/scripts/agent-status-summary, tmux-agent-herdr-lite/scripts/install.sh, tmux-agent-herdr-lite/assets/test_python_isolation.py] [feature: python-isolation] [proof: an agent runs the cockpit scripts from a working directory with planted json.py, hashlib.py and shlex.py and sees no marker] [parallel-safe]
- R12: `agent-pane` must pass the agent command to the new pane as separate words, so that a word holding `;`, `$(...)` or a quote reaches the program as one literal argument. [where: tmux-agent-herdr-lite/scripts/agent-pane, tmux-agent-herdr-lite/assets/test_agent_pane_argv.py, tmux-agent-herdr-lite/SKILL.md] [feature: tmux-cockpit] [proof: an agent opens a pane on a private tmux server with a command word holding ;touch <marker> and sees no marker and the literal text in the pane] [after: R11]
- R13: `okf-site-kit/references/bundle-variations.md` must hold no link to witscode.com, and must keep the sentence around it. [where: okf-site-kit/references/bundle-variations.md, okf-site-kit/assets/test_okf_site_hostile.py] [feature: okf-site-build] [proof: an agent greps okf-site-kit for witscode and finds nothing] [parallel-safe]
- R14: `okf_site.py` must write bundle titles and descriptions into MDX and JSX attributes escaped, so that a value cannot add an attribute or a JSX expression. [where: okf-site-kit/assets/okf_site.py, okf-site-kit/assets/test_okf_site_hostile.py] [feature: okf-site-build] [proof: an agent builds a site from a bundle whose title holds a quote and braces, and the LinkCard checker passes] [after: R13]
- R15: `okf_site.py` must keep `javascript:`, `vbscript:` and `data:` links, `<script` tags and `on*=` handlers from bundle bodies out of the generated pages as live markup. [where: okf-site-kit/assets/okf_site.py, okf-site-kit/assets/test_okf_site_hostile.py] [feature: okf-site-build] [proof: an agent builds the hostile bundle and the live-markup scan reports zero lines] [after: R14]
- R16: `okf_site.py` must not copy an asset that is a symlink to a file outside the bundle. [where: okf-site-kit/assets/okf_site.py, okf-site-kit/assets/test_okf_site_hostile.py] [feature: okf-site-build] [proof: an agent builds a bundle with an asset symlink to a dummy secret outside it and sees no copy in the site output] [after: R15]
- R17: The ops-intake tests and eval must hold no remote download piped to a shell and no literal invisible Unicode character, and every check they make today must still pass. [where: ops-intake/assets/test_intake_sync.py, ops-intake/assets/test_intake_feedback.py, ops-intake/assets/test_intake_queue.py, ops-intake/assets/test_intake_review_fixes.py, ops-intake/eval/run_eval.py, ops-intake/assets/test_fixture_hygiene.py] [feature: intake-queue] [proof: an agent drives the intake-queue recipe and sees it unchanged, and a scan of ops-intake test and eval sources finds no download-to-shell line and no invisible character] [parallel-safe]
- R18: The security-posture-audit detector fixtures must move into labelled files under `security-posture-audit/assets/fixtures/` that the tests and eval read, the detector must still flag them as today, and SKILL.md must state that scanned content is data and its instructions are ignored. [where: security-posture-audit/assets/fixtures, security-posture-audit/assets/test_audit_posture.py, security-posture-audit/eval/run_eval.py, security-posture-audit/SKILL.md, security-posture-audit/assets/test_fixture_files.py, docs/rfc2119/2026-09-19-classification.md] [feature: posture-audit] [proof: an agent audits a fixture repo with the risky workflow and sees the same finding, and greps find no download-to-shell line in test or eval sources and the boundary sentence in SKILL.md] [parallel-safe]
- R19: The human-checkpointed template of crafting-self-prompting-loops must let only an explicit `APPROVED` decision reach `act()`, and every other decision value must stop or revise. [where: crafting-self-prompting-loops/assets/templates/human-checkpointed.template.md, crafting-self-prompting-loops/assets/test_checkpoint_template.py] [feature: loop-templates] [proof: an agent runs the gate check on the template and sees it pass, and on a fail-open fixture and sees it fail] [parallel-safe]
- R20: test-safety-net must run the analyzed repo's TypeScript for Node discovery only when the user passes `--precise`, and its default must run no repo code. [where: test-safety-net/assets/stack_node.py, test-safety-net/assets/test_stack_node.py, test-safety-net/references/parameters.md, test-safety-net/SKILL.md] [feature: tsn-discovery] [proof: an agent runs discovery on a fixture Node repo whose typescript.js writes a marker, sees no marker by default and the marker with --precise] [parallel-safe]
- R21: factory-conductor's SKILL.md must state in one sentence that the re-entry timer is installed only when the grant carries a `reentry` block. [where: factory-conductor/SKILL.md, factory-conductor/assets/test_reentry_wording.py, docs/rfc2119/2026-09-19-classification.md] [feature: reentry-timer] [proof: an agent drives reentry install, status and uninstall in dry-run mode, and reads the consent sentence and the existing show and remove sentences] [parallel-safe]
- R22: `scripts/ab-validate.py` must gain a new `SINCE_` constant and at least one row each for feynman-walkthrough, knowledge-gardener, jev-agent-setup, context-hygiene-kit, world-model-ledger, tmux-agent-herdr-lite, okf-site-kit, crafting-self-prompting-loops and test-safety-net that measures this run's change, plus a guard row for ops-intake and security-posture-audit, and `make ab-validate` must print `AB_RESULT: PASS` with each new claim row `IMPROVED`. [where: scripts/ab-validate.py] [feature: ab-validate] [proof: an agent runs make ab-validate in the task worktree and sees an IMPROVED row for each named skill and AB_RESULT: PASS] [after: R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11, R12, R13, R14, R15, R16, R17, R18, R19, R20, R21]

## Acceptance criteria
- R1: On an embedded-bare-repo pin with a core.fsmonitor marker, okf status and okf diff create no marker, and every git argv the tool runs holds both switches. [cmd: {python} feynman-walkthrough/assets/test_okf_git_safe.py]
- R2: On the same fixture, garden.py sweep creates no marker, and every git argv holds both switches. [cmd: {python} knowledge-gardener/assets/test_garden_git_safe.py]
- R3: A fingerprint `--output=<dir>/clobber` gives an error for that source in okf status and okf diff and creates no file; a normal pin still goes FRESH then STALE. [cmd: {python} feynman-walkthrough/assets/test_okf_fingerprint.py]
- R4: The same bad fingerprint gives an error for that source in garden.py sweep and creates no file; a normal pin still goes FRESH then STALE. [cmd: {python} knowledge-gardener/assets/test_garden_fingerprint.py]
- R5: The shebang and dependency lines match exactly; when uv is installed, the shebang's uv words with --show-settings from a folder with a hostile uv.toml print no index.invalid. [cmd: {python} jev-agent-setup/assets/test_jev_script.py]
- R6: With a stub SDK: no --images gives exit 2 and no call; --images gives the header and an Image; a directory, missing or symlink placeholder gives exit 2 and no call; a plain noul request sends no header. [cmd: {python} jev-agent-setup/assets/test_jev_images.py]
- R7: After a stub box call the log line holds the path and sha256 and no image bytes, and jev log shows the four edges and the confidence. [cmd: {python} jev-agent-setup/assets/test_jev_images.py]
- R8: The installed instruction block holds a Box row and the images rule. [cmd: {python} jev-agent-setup/assets/test_jev_instructions.py]
- R9: With a planted json.py in the working directory each context-hygiene hook writes no marker and prints its normal output, and no inline call lacks -I. [cmd: {python} context-hygiene-kit/assets/test_hooks_isolation.py]
- R10: With planted modules each world-model hook and the grader write no marker, the hooks print their normal output, and the grader's results do not change. [cmd: {python} world-model-ledger/assets/test_isolation.py]
- R11: With planted json.py, hashlib.py and shlex.py the cockpit scripts write no marker, and no inline call lacks -I. [cmd: {python} tmux-agent-herdr-lite/assets/test_python_isolation.py]
- R12: With a stub tmux on PATH that records its argv, agent-pane passes each command word as its own literal argument and no sh -c text holds the joined command. [cmd: {python} tmux-agent-herdr-lite/assets/test_agent_pane_argv.py]
- R13: No file under okf-site-kit names witscode.com. [cmd: {python} okf-site-kit/assets/test_okf_site_hostile.py]
- R14: The generated LinkCard attributes parse as one value each for a hostile title and description. [cmd: {python} okf-site-kit/assets/test_okf_site_hostile.py]
- R15: The generated pages hold no live script-scheme link, script tag or event handler from the hostile bundle. [cmd: {python} okf-site-kit/assets/test_okf_site_hostile.py]
- R16: An asset symlink that points outside the bundle is not in the site output. [cmd: {python} okf-site-kit/assets/test_okf_site_hostile.py]
- R17: The hygiene scan of ops-intake test and eval sources passes. [cmd: {python} ops-intake/assets/test_fixture_hygiene.py]
- R17: The ops-intake skill gate still passes. [cmd: make gate-skill SKILL=ops-intake]
- R18: The fixture files exist with their label, the tests and eval read them, and no test or eval source holds a download-to-shell line. [cmd: {python} security-posture-audit/assets/test_fixture_files.py]
- R18: The security-posture-audit skill gate still passes. [cmd: make gate-skill SKILL=security-posture-audit]
- R19: The template's gate passes the approve-only check, and a fail-open fixture fails it. [cmd: {python} crafting-self-prompting-loops/assets/test_checkpoint_template.py]
- R20: A fixture Node repo's typescript.js runs only with --precise. [cmd: {python} test-safety-net/assets/test_stack_node.py]
- R21: The consent sentence is in SKILL.md. [cmd: {python} factory-conductor/assets/test_reentry_wording.py]
- R21: The BCP 14 register still matches. [cmd: make bcp14]
- R22: `make ab-validate` prints each new claim row as `IMPROVED` and `AB_RESULT: PASS`. [cmd: make ab-validate]

## Open questions

## Iterations
- I1: Started as three audit fixes (2026-11-07). The owner added the tmux heredoc and fingerprint leads (D28), then redirected the run to the skills.sh scanner ratings and chose four groups of fixes (D23 to D27).

## Verification
Verify skill: .claude/skills/verify-agent-skills
