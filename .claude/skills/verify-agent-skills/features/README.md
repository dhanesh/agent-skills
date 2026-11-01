# agent-skills feature map

One file per user-facing CLI surface, in sweep order.

- [forge-feature-map](forge-feature-map.md) — lint a verify skill and read its feature map with forge.py
- [evidence-recorder](evidence-recorder.md) — record doctor checks and SHA-bound evidence with verify_evidence.py
- [spec-hints](spec-hints.md) — lint a spec and compile it into a task plan with spec_lint.py and spec_to_tasks.py
- [conductor-evidence-gate](conductor-evidence-gate.md) — have factory-conductor accept or reject a task's evidence before merge
- [ab-validate](ab-validate.md) — compare the branch against its baseline with scripts/ab-validate.py
