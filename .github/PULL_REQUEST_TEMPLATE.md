<!-- Keep it short. The diff is the record; this frames it. -->

## Summary

<!-- One or two sentences: what this change does and why. -->

## What changed

<!-- Bullet the concrete changes. Name the skill(s) and files touched. -->
-

## Verification

<!-- How you confirmed it works. `make gate` is the baseline; add anything else you ran. -->
- [ ] `make gate` is green (gate self-tests · validate · scan-leaks · prompting-playbook ·
      frontmatter · asset-paths · unit + shell suites · outcome evals)
- [ ] For a touched skill: `make gate-skill SKILL=<dir>` green
- [ ] For any SKILL.md touched: ran the semantic review checklist (`repo2skill/references/semantic-review.md`)
- [ ] Behavioural change? `make ab-validate` shows IMPROVED or HELD (never WORSE/UNPROVEN),
      and any new guardrail has an A/B row — a rule with no row is a claim nobody measured
- [ ] Other checks (describe):

## Notes / scope

<!-- Known limitations, follow-ups, or anything a reviewer should weigh. Optional. -->
</content>
