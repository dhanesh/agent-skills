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
- [incident-probe](incident-probe.md) — detect live incidents with incident-watch's read-only probes (partly built)
