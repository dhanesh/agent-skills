# agent-skills feature map

One file per user-facing CLI surface, in sweep order.

- [forge-feature-map](forge-feature-map.md) — lint a verify skill and read its feature map with forge.py
- [evidence-recorder](evidence-recorder.md) — record doctor checks and SHA-bound evidence with verify_evidence.py
- [spec-hints](spec-hints.md) — lint a spec and compile it into a task plan with spec_lint.py and spec_to_tasks.py
- [conductor-evidence-gate](conductor-evidence-gate.md) — have factory-conductor accept or reject a task's evidence before merge
- [ab-validate](ab-validate.md) — compare the branch against its baseline with scripts/ab-validate.py
- [intake-queue](intake-queue.md) — import ops signals, then list, pick, sync and resolve queue items with ops-intake's intake.py
- [grant-check](grant-check.md) — write an autonomy grant with write_grant.py and ask contract_check.py check-grant what it covers
- [installer-detect](installer-detect.md) — detect a repo's stacks and plan its missing verifier rails with detect_stack.py
- [growth-review](growth-review.md) — register metric thresholds, import data and review breaches with growth.py
- [conductor-review-brief](conductor-review-brief.md) — brief a fresh reviewer and record its verdict with conductor review
- [factory-status-gate](factory-status-gate.md) — read the README stage table and run the gate self-tests
- [skill-catalog](skill-catalog.md) — check that the README catalog and the call graph list every skill
- [pipeline-tick](pipeline-tick.md) — run every factory stage in one scheduled, locked, capped tick with factory-pipeline (partly built)
- [incident-probe](incident-probe.md) — detect live incidents with incident-watch's read-only probes, and close each incident record for bug-autopsy
- [knowledge-git-pins](knowledge-git-pins.md) — check git source pins with okf.py status/diff and garden.py sweep without running repo-controlled git config (run 5)
- [okf-site-build](okf-site-build.md) — build a static site from an OKF bundle with okf_site.py, with hostile bundle content kept inert (run 5)
- [python-isolation](python-isolation.md) — run the hook kits, the world-model grader and the tmux cockpit scripts with Python isolated from the working directory (run 5)
- [tmux-cockpit](tmux-cockpit.md) — open an agent pane with agent-pane, passing the command as words, not shell text (run 5)
- [jev-cli](jev-cli.md) — resolve the jev CLI's dependencies with no project uv config and a pinned SDK (run 5)
- [posture-audit](posture-audit.md) — audit a repo's security posture with audit_posture.py on fixture workflows (run 5)
- [loop-templates](loop-templates.md) — check that crafting-self-prompting-loops' human-checkpointed template acts only on an explicit approval (run 5)
- [tsn-discovery](tsn-discovery.md) — discover a Node repo's tests with test-safety-net, running repo TypeScript only on opt-in (run 5)
- [reentry-timer](reentry-timer.md) — install, show and remove the conductor and pipeline timers only with the grant's consent (run 5)
