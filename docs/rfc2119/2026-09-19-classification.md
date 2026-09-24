# BCP 14 classification, 2026-09-19

Spec: [`docs/superpowers/specs/2026-09-19-bcp14-skills-design.md`](../superpowers/specs/2026-09-19-bcp14-skills-design.md) §2.

**What a candidate is.** Every block under a hard-rule heading (Invariants, Rules, Safety, Boundaries, Trust, Guardrails, Contract, Non-negotiables), plus every sentence containing never or always. Code blocks and inline code are excluded.

**Where the levels come from.** Jev (`jev-1.13.0`) judged each candidate's level and, separately, whether ignoring it causes harm (RFC 2119 §6). A rewriter then set the final level. **The final level is the one in force.** Any departure from Jev carries a reason.

**Levels:**
- **MUST** includes MUST NOT.
- **SHOULD** includes SHOULD NOT.
- **MAY** is an option.
- **plain** means no keyword; the sentence is left as prose.

**How the header counts work.** Each candidate counts once, at its final level. A keyword inside a candidate at another level is not counted separately; for example, the MAY in verifier-installer c8's "MAY propose, but SHOULD NOT impose" is not.

Skills appear in rewrite order.

**Line numbers are informational; the gate matches on text.** `scripts/gates/bcp14-registry.sh` (`make bcp14`, part of `make gate`) reads this file. Every capitalised keyword in a SKILL.md body (fences, inline code and the declaration skipped) has to fall under a row in its skill's section, and the row's final level has to equal the strongest keyword it covers. A row matches on its quoted text: emphasis, backticks and escapes are ignored, the text ends at its first `…`, and a truncated row covers the rest of its paragraph, list item or table row. Every skill also needs a section here, and no SKILL.md prose may use a lowercase "must" or "shall". So when a keyword sentence changes, its row's quoted text has to change with it. Line numbers were refreshed in the 2026-09-25 backfill and can drift after that.

**Backfill, 2026-09-25.** The gate found the register had drifted from the skills in three ways:
- **Stale quotes.** 60 rows quoted a sentence as it read before the BCP 14 rewrite ("Never edit code"), not the keyworded text now in the skill ("You MUST NOT edit code"). These are re-quoted to the current text. Their candidates, Jev numbers and levels are unchanged, because the rewrite kept the sentence and only added the keyword. Four more rows were fixed so they match their sentence: spec-first-planning c12 and c16, factory-conductor c2, and crafting-self-prompting-loops c12.
- **Unclassified keywords.** jev-agent-setup had no section, and 15 lowercase "must"s (none were "shall") sat in SKILL.md prose. Jev (`jev-1.13.0`) judged each one in one batch per skill, with the §2 criteria and the §6 harm question. The new rows carry Jev's level, its confidence and its harm probability. A lowercase "must" became a keyword with a row, or plain prose when it was not a rule: a mention, a condition, rationale or a description. Rewording never hides a rule.
- **Decorative keywords.** The 2026-09 prompt audit flagged keywords that sit on something other than a rule. Jev was asked about each one as a possible restatement:
  - Six are now plain: clean-code c16 and c20, crafting-self-prompting-loops c4, mockstar-mock c14, security-posture-audit c10 and verifier-installer c4.
  - Five clauses were demoted inside rows that keep their level: world-model-ledger c7 and c8, and starlight-handbook-kit c2.
  - One is kept. In the shared "Locating this skill's helpers" preamble in 13 skills, Jev rated the clause "and MUST NOT receive a relative form" plain at 0.61 (harm 0.60). We departed from that and kept the keyword. Removing it would take agent-ready-rails and tmux-agent-herdr-lite out of the PP-5 advisory, and the owner asked that every skill's PP-5 status stay as it is until the separate PP-5 change. Revisit it then.

  Two flags are outside this register's scope, which is SKILL.md only, and are left unchanged:
  - base-in-reality `assets/workflow.mjs`: "You MAY only cite sources you actually fetched". Jev rates it MUST (0.92, harm 0.46): the rule is real and the MAY misstates it.
  - jev-agent-setup `assets/jev-instructions.md`: the heading "… REQUIRED". Jev gives MUST at a low confidence (0.35, harm 0.58). The block is owner policy.

## test-safety-net (29 candidates · 11 MUST · 0 SHOULD · 0 MAY · 18 plain · 16 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 35 | Build the change-detector that unblocks agent work on an untested codebase. This is **not… | MUST (0.34, 0.79) | plain | the "never" describes the deliverable; the no-coverage-percentage rule itself is stated in Deliverable |
| c2 | 71 | The ranker and the python, go and rust guards are stdlib-only python3 — the go guard driv… | MUST (0.84, 0.88) | plain | both nevers describe guard behaviour (offline, never fetches); no never/always sentence here is a directive |
| c3 | 103 | **On go, make sure the module's dependencies are already downloaded.** The go guard runs… | MUST (0.41, 0.63) | plain | both nevers describe the go guard (never fetches; exit 5 never a verdict); the imperative is a workflow step |
| c4 | 108 | **On rust, check the lockfile, the toolchain and the cargo cache before you plan to write… | MUST (0.48, 0.81) | MUST |  |
| c5 | 125 | Offline, stdlib + git only, deterministic. Read `references/parameters.md` for the full a… | MUST (0.61, 0.75) | plain | "always"/"never" describe the ranker (rust reads heuristic, never runs cargo); the imperatives are workflow steps |
| c6 | 142 | Every unit lands in exactly one of the four testability tiers described in full in `refer… | SHOULD (0.39, 0.69) | plain | a summary of the tier outcomes; "reported, never applied" is carried as MUST NOT by Invariant 1 |
| c7 | 158 | The ranker's triage is conservative on purpose — it would rather under-tier a unit than o… | MUST (0.72, 0.70) | MUST |  |
| c8 | 181 | **The runtime guard, not the tier, is what enforces "never real I/O."** Static triage is… | MUST (0.94, 0.87) | plain | both nevers describe the guard (quoted invariant name; never written into the repo); Invariant 2 carries the MUST NOT |
| c9 | 191 | On **python** that is `assets/io_guard.py`, loaded as a **pytest plugin via `-p`** (never… | MUST (0.63, 0.77) | plain | describes how the guard loads (plugin, not a conftest.py or fixture); eval check 19 needs the negation wording, which stays |
| c10 | 234 | **On node, read the exit status, not the per-test results.** When a violation lands *afte… | MUST (0.74, 0.81) | plain | "Never the test that caused it" describes node --test attribution, not a directive |
| c11 | 271 | - **You MUST NOT keep a test reported `ok` from a run that exited nonzero.** | MUST (0.69, 0.78) | MUST |  |
| c12 | 283 | **Copy the whole block.** `<package>` is the package directory (`./internal/billing`), an… | MUST (0.60, 0.75) | plain | restates Invariant 1 (append, never overwrite), which already carries the MUST NOT; one keyword per rule |
| c13 | 324 | **Copy the whole block.** Run it from the crate root, adding `-p <package>` for a workspa… | MUST (0.52, 0.73) | plain | restates Invariant 1 (append, never overwrite), which already carries the MUST NOT; one keyword per rule |
| c14 | 369 | - A unit the ranker tiers 3 as *not reachable* or *binary-only* is reported, never tested. | MUST (0.51, 0.76) | plain | states the triage outcome for an unreachable unit; a test for one cannot be proven (Invariant 3 carries the MUST NOT) |
| c15 | 380 | - A compile error is exit 5, never RED; cargo exits 101 for both. | MUST (0.47, 0.79) | plain | defines the exit mapping (compile error is exit 5, never RED); descriptive |
| c16 | 382 | - a dependency not in the local cargo cache (run `cargo fetch`, or build the tests once;… | MUST (0.52, 0.80) | plain | "the proof never downloads anything" describes the guard |
| c17 | 409 | - The guard never passes `--nocapture`, and you MUST NOT add it: under it the default… | MUST (0.67, 0.84) | MUST |  |
| c18 | 406 | - **Tier 2 candidate:** blocks the uncontrollable groups always, plus every controllable… | MUST (0.64, 0.81) | plain | "blocks ... always" describes the Tier 2 guard; the "name all of it" imperative is a workflow step |
| c19 | 412 | The guard patches the **lowest** layer reachable, which for CPython is the `os` primitive… | MUST (0.87, 0.83) | plain | "never routes through" describes CPython internals |
| c20 | 479 | 1. **You MUST NOT modify source.** You MUST only create test files. When a test file… | MUST (0.90, 0.85) | MUST |  |
| c21 | 482 | 2. **You MUST NOT write a test that performs real I/O.** Enforced by the tier-aware… | MUST (0.94, 0.83) | MUST |  |
| c22 | 485 | 3. **You MUST NOT ship an unproven test.** A test that did not go RED MUST be discarded… | MUST (0.88, 0.66) | MUST |  |
| c23 | 487 | 4. **You MUST NOT leave the suite red.** End state is a green suite plus suspected bugs… | MUST (0.92, 0.77) | MUST |  |
| c24 | 489 | 5. **Hard gate before writing** — step 3's confirmation MUST happen before any test file… | MUST (0.86, 0.76) | MUST | (no departure) changed in 1.4.0 (autonomy-grant Task 7, now line 489): "…MUST happen before any test file is touched, unless `check-grant … --action local_reversible` exits 0; then you MAY write tests without asking, and MUST name the grant id and action class in the report. A grant never lifts Invariant 1." Still MUST: the grant is the one release, and it never reaches source edits |
| c25 | 500 | You MUST emit every captured value with `repr()`. You MUST NOT build a test's expected… | MUST (0.97, 0.82) | MUST |  |
| c26 | 519 | **Promoted units** get a line in the report naming the ranker's original tier, the tier u… | MUST (0.73, 0.44) | plain | harm 0.44 caps it at SHOULD, and a SHOULD here would contradict step 2's MUST NOT (c7) for the same rule; left as its restatement |
| c27 | 522 | **How to improve this.** `inbound_refs` is a static approximation — an identifier-occurre… | plain (0.36, 0.16) | plain |  |
| c28 | 171 | 3. **Confirm with the user before writing anything.** … This is a hard gate — you MUST NOT proceed past it unconfirmed, unless `check-grant --root <repo> --action local_reversible` exits 0 …; then you MAY proceed without asking, and MUST name the grant id and action class in the report. | not judged (new in 1.4.0, autonomy-grant Task 7) | MUST | the step-3 write gate (design spec §5): was plain "do not proceed past it unconfirmed"; keyworded to match Invariant 5 (c24) now that it carries the grant clause; counted once at MUST, the MAY being the option the grant opens; eval checks 53-54 grade both places. Final fix wave (M-c): the fallback reads "Any other exit (3 ASK/NONE, 2 INVALID, 1 usage error) means ask as usual"; plain, counts unchanged |
| c29 | 548 | This skill follows [skill-contract v1](…). It consumes autonomy grants, which only lift step 3's confirmation, … | not judged (new in 1.4.0, autonomy-grant Task 7) | plain | describes the skill-contract adoption, like crafting-self-prompting-loops c10; the rule it points at is carried by c24 and c28 |

## world-model-ledger (11 candidates · 7 MUST · 0 SHOULD · 0 MAY · 4 plain · 6 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 93 | \| **PostToolUse** → `hooks/posttooluse-observe.sh` \| after **every** tool call \| the univ… | MUST (0.82, 0.79) | plain | the "never" describes what the PostToolUse hook reads (tool input, not output); Invariants 2 and 3 carry the rule |
| c2 | 99 | The model maintains itself: the universal `PostToolUse` hook captures entities and behavi… | MAY (0.41, 0.28) | plain | the only never/always sentence ("A model never guesses facts inside a hook") describes the hook design; Invariant 2 carries the MUST NOT |
| c3 | 107 | 0. **(Usually automatic) Seed the model repo-wide.** `SessionStart` auto-seeds a fresh re… | plain (0.31, 0.28) | plain |  |
| c4 | 161 | 1. **Code observation MUST NOT raise normative confidence.** A hook may set… | MUST (0.96, 0.85) | MUST |  |
| c5 | 164 | 2. **No invented facts in hooks.** Hooks MUST capture only what is deterministically… | SHOULD (0.34, 0.71) | MUST | harm 0.71: a model summarizing inside a hook would put invented facts in the ledger; one of the invariants the skill says not to weaken |
| c6 | 168 | 3. **Trusted channel only.** `tool_result` / `tool_use` content MUST NOT be harvested… | MUST (0.90, 0.86) | MUST | (no departure) note: the factory-trust fix (world-model-ledger 1.2.0) adds the CLI path to this invariant (`wm validate` and `wm refute` MUST refuse a `human` kind; `--assert-valid` MUST record `agent_assert`); the candidate and its level are unchanged |
| c7 | 175 | 4. **Append-only evidence: evidence MUST be soft-invalidated, not hard-deleted.**… | SHOULD (0.72, 0.61) | MUST | harm 0.61 and soft-invalidation is asserted by the install-gate suite (test_derive_skips_invalidated, test_prune_soft_invalidates_vanished_edges); "confidence is always derived" describes the derivation and stays plain backfill 2026-09-25 (prompt-audit L3): the clause "and MUST NOT be hard-deleted" restated the MUST before it; Jev on the clause: MUST (0.33, harm 0.73), a 0.50/0.50 tie with plain. Decided ourselves (confidence < 0.6): plain, "soft-invalidated, not hard-deleted"; soft-invalidation is the only permitted removal, so the MUST carries it. Row level unchanged |
| c8 | 178 | 5. **Every triple MUST be ontology-checked before it enters the ledger.** Predicates are… | SHOULD (0.51, 0.70) | MUST | the write-boundary contract: harm 0.70, and the eval (ontology rejects a hallucinated predicate / an impossible triple) and unit tests enforce it; "hooks never break" describes the hooks and stays plain backfill 2026-09-25 (prompt-audit L3): two clauses restating the MUST before them are now plain: "and MUST NOT be silently stored" (Jev plain 0.44, harm 0.71; confidence < 0.6, decided ourselves: the MUST reject already forbids storing) becomes "not silently stored", and "and MUST NOT happen as a side effect of a marker" (Jev plain 0.51, harm 0.70; same reasoning) becomes "not happen as a side effect of a marker". Row level unchanged |
| c9 | 185 | When a situation genuinely needs an exception to an invariant, you MUST surface it to the… | SHOULD (0.88, 0.50) | MUST | silently working around an invariant is the harm the invariants exist to prevent (harm 0.50); a SHOULD would permit it. Deliberate meaning clarification: "Prefer these defaults" is dropped because the heading says "do not weaken these" and the invariants are MUST (controller ruling; Jev 0.97 for keeping MUST) |
| c10 | 189 | You MUST confirm the gate passed: `python3 test_world_model.py` (116 tests — the two-axis… | MUST (0.59, 0.71) | MUST |  |
| c11 | 162 | This is the whole point: it lets the model say "observed, but unverified." | plain (0.65, 0.63) | plain | backfill 2026-09-25: was lowercase "the model must be able to say"; rationale for Invariant 1, whose MUST NOT (c4) carries the rule and the harm |

## crafting-self-prompting-loops (12 candidates · 6 MUST · 1 SHOULD · 0 MAY · 5 plain · 4 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 26 | Your job with this skill: turn a fuzzy "make it keep going until it's good" request into … | MUST (0.49, 0.85) | plain | the never/always list describes the properties of a sound loop; step 4 non-negotiables carry the MUSTs |
| c2 | 34 | Ask the user (or infer, then state your assumption): *what is this loop trying to achieve… | MUST (0.69, 0.74) | plain | "can never legitimately stop" is rationale for the done test; the imperative is a workflow step |
| c3 | 49 | \| LSC-2 \| Stop condition (primary) \| how the model signals "done" (a flag/token the harne… | MUST (0.79, 0.85) | MUST |  |
| c4 | 63 | These are the constraints loops most often skip and most often die on: | plain (0.91, 0.80), re-asked; first run MUST (0.81, 0.84) | plain | backfill 2026-09-25 (prompt-audit CL4): "You MUST NOT ship a loop without them" restated, with its own keyword, the three bullets below it, each of which carries its own MUST (c5, c6, c7). Re-asked as a restatement, Jev says plain; the harm (0.80) is real but c5-c7 carry it, so the sentence now only introduces them |
| c5 | 63 | - **A mandatory backstop (LSC-3).** Model self-termination (LSC-2) *can fail* — the model… | MUST (0.95, 0.89) | MUST |  |
| c6 | 64 | - **The two-channel boundary (LSC-7).** Anything the model produces, a tool returns, or c… | MUST (0.87, 0.88) | MUST |  |
| c7 | 65 | - **A human gate where it matters (LSC-8).** Any irreversible or externally-visible actio… | MUST (0.70, 0.79) | MUST | (no departure) note: the output-only exemption in the same block carries MAY, a genuine option. Changed in 1.4.0 (autonomy-grant Task 7, now line 67): "…MUST wait for explicit human approval, unless `check-grant --root <repo> --action <the action's class>` exits 0 at the moment of the action; then the loop MAY proceed, and MUST name the grant id and action class … A grant can never cover merge, deploy, spend, external messages or deletes … a push MUST send only the current branch to the remote branch of the same name." Still MUST; the carve-out is A8, and the push rule is spec-first-planning c16's. Fix round 1: the grantable set is now a closed list of class tokens ("Under LSC-8 only `push_branch` … and `open_pr` … are grantable; `merge`, `deploy`, `spend`, `external_message` and `delete` always wait for the human, and so does any action you cannot place exactly in `push_branch` or `open_pr`"), the push rule adds "and never force-push", and the checker path points at the `$SKILL_DIR` resolution; still MUST, counts unchanged. Final fix wave (I2): one plain sentence adds that a push or pull request whose commits change CI configuration counts as `deploy` (`check-grant` answers ASK `ci-config`) and waits for the human; it restates skill-contract SPEC's rule inside the same MUST, so counts unchanged |
| c8 | 72 | 2. **A runnable scaffold** — in the user's target runtime. For Claude Code, that's the re… | MUST (0.27, 0.62) | MUST |  |
| c9 | 80 | If the user has a loop already and it misbehaves, run steps 3–6 as a *checklist audit*: s… | plain (0.57, 0.51) | plain |  |
| c10 | 127 | This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/… | MUST (0.33, 0.42) | plain | describes the skill-contract adoption, not a rule (harm 0.42); the section and its json block are left untouched. 1.4.0 (autonomy-grant Task 7) adds autonomy-grant/v1 to `consumes` and one descriptive sentence; still plain |
| c11 | 144 | You SHOULD structure the result like this: | plain (0.34, 0.51) | SHOULD | report format, not machine-consumed; aligned with verifier-installer c4 |
| c12 | 100 | 1. **Check first.** Run `python3 "$SKILL_DIR/assets/contract_check.py" check-envelope …` … | not judged (new in 1.4.0, autonomy-grant Task 7) | MUST | accepting a handoff under a grant (design spec §5, skill-contract commandment 10 as amended); counted once at MUST — the MAY is the option the grant opens, the MUSTs are the report and the fallback. Final fix wave (M-a): "and action class" added to the report, and the fallback ask keyworded; one rule, still counted once backfill 2026-09-25: re-quoted to the start of its block (step 1, Check first), where the sentence now sits; level unchanged |

## mockstar-mock (17 candidates · 11 MUST · 1 SHOULD · 0 MAY · 5 plain · 3 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 21 | The defining rule: **no fabricated endpoints.** Every mock traces back to a fetche… | MUST (0.92, 0.83) | plain | restates Invariant 1 (no fabricated endpoints), which carries the MUST NOT; one keyword per rule |
| c2 | 46 | \`bunx @dhaneshpurohit/mockstar\` available (install: \`bun add -g @dhaneshpurohit/mock… | MUST (0.74, 0.80) | MUST |  |
| c3 | 62 | 1. **No fabricated endpoints.** Every endpoint in the output MUST trace to a fetched or… | MUST (0.96, 0.82) | MUST |  |
| c4 | 64 | 2. **Prefer native tooling.** You SHOULD use `bunx @dhaneshpurohit/mockstar import` for… | SHOULD (0.28, 0.48) | SHOULD | harm 0.48 caps it below MUST; a strong default with a legitimate exception (the hand-authored path used elsewhere for non-liftable inputs) |
| c5 | 65 | 3. **Schema-valid output.** Verification (Stage 5) boots the server. A mock proje… | MUST (0.84, 0.74) | MUST |  |
| c6 | 68 | 4. **No silent truncation.** When \`--max-endpoints\` caps the inventory, every drop… | MUST (0.87, 0.76) | MUST |  |
| c7 | 73 | 5. **Read-only inputs.** You MUST NOT modify source spec files, HAR archives, or… | MUST (0.56, 0.74) | MUST | low confidence, but Invariants heading + real data-loss harm (0.74) support MUST |
| c8 | 81 | \`--runtime auto\|local\|docker\` — selects how mockstar is invoked (default: \`auto\`)… | MUST (0.15, 0.74) | plain | describes what each \`--runtime\` value does; not a directive to the agent (confidence 0.15) |
| c9 | 103 | - Hand subagents the literal absolute `$EXTRACT` and `$SMOKE` values. You MUST NOT hand… | MUST (0.71, 0.82) | MUST | the SKILL.md \`$SKILL_DIR\` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c10 | 177 | For **documentation URLs**, fetch the content first with \`curl -L\` (or WebFetch),… | MUST (0.77, 0.82) | MUST |  |
| c11 | 216 | **Prose** — extract from code fences, Markdown tables, and inline backtick refer… | MUST (0.75, 0.81) | MUST |  |
| c12 | 253 | \`webhookHints[]\` → \`webhooks[]\` on the triggering entry. When a hint carries \`sig… | MUST (0.82, 0.82) | MUST |  |
| c13 | 379 | **Consequence for this skill:** if \`--tenant\` is not \`default\`, every consumer —… | MUST (0.80, 0.82) | plain | the only always ("the CLI serve path always enables path + header modes") describes default CLI config, not a directive; not under a hard-rule heading backfill 2026-09-25: the lowercase "must use a selector" in this block is now MUST, recorded separately as c15; this row still covers only the descriptive always |
| c14 | 467 | The skill's asset helpers (`assets/extract_text.py`, `assets/smoke.sh`) live in this… | plain (0.67, 0.71), re-asked; first run MUST (0.81, 0.81) | plain | backfill 2026-09-25 (prompt-audit M5): the Subagent dispatch note repeated c9's rule (resolve and pass absolute helper paths, MUST NOT pass a relative form) with its own MUST and MUST NOT. Re-asked as a restatement, Jev says plain; the note now points back to Locating asset helpers, where c9 carries the MUST NOT, so the harm (0.71) stays covered |
| c15 | 383 | if `--tenant` is not `default`, every consumer — the smoke test, the coverage-report examples, and whoever calls the mock — MUST use a selector. | MUST (0.75, 0.69) | MUST | backfill 2026-09-25: was lowercase "must"; a named tenant is reachable only through a selector, so a bare-path consumer gets no mock |
| c16 | 397 | The container binds `0.0.0.0:3000` and is crash-only — the orchestrator MUST set a restart policy | MUST (0.72, 0.65) | MUST | backfill 2026-09-25: was lowercase **must**; the container exits on a crash and does not restart itself |
| c17 | 331 | Write `MOCKSTAR-COVERAGE.md` to the output directory root, following the template in `references/coverage-report.md`, with these sections: | plain (0.25, 0.45) | plain | backfill 2026-09-25: was lowercase "The report must include:". Confidence 0.25 < 0.6 (SHOULD 0.41), decided ourselves: a workflow step (spec §2: steps stay plain imperatives); harm 0.45 < 0.5 |

## clean-code (34 candidates · 0 MUST · 13 SHOULD · 0 MAY · 21 plain · 7 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 29 | A working guide to Robert C. Martin's principles. The aim is not to recite rules — … | plain (0.94, 0.16) | plain |  |
| c2 | 34 | But *clean* means **as simple as the problem allows**, never as elaborate as possi… | plain (0.57, 0.20) | plain |  |
| c3 | 39 | This file (L1) is always active. Apply it to every function, class, or module you … | MUST (0.44, 0.23) | plain | describes the skill's always-loaded scope; harm 0.23 <0.5, no gate — the imperative that follows is a workflow step |
| c4 | 74 | 1. Run the suite. Green? Refactor. Red? Fix or report the failure first — you SHOULD NOT… | MUST (0.55, 0.43) | SHOULD | harm 0.43 < 0.5 caps it below MUST; no gate enforces it; 'fix or report' is the stated alternative |
| c5 | 142 | The **principle and the shape** are the point, never the syntax. A snippet in one … | plain (0.80, 0.16) | plain |  |
| c6 | 153 | \| Rule \| One-line \| Violation signal \| | plain (0.68, 0.23) | plain | table header, not a sentence |
| c7 | 154 | \|---\|---\|---\| | plain (0.83, 0.16) | plain | table separator, not a sentence |
| c8 | 157 | \| **Meaningful names** \| Names SHOULD reveal intent \| `d`, `tmp`, `data`, `obj` \| | SHOULD (0.18, 0.22) | SHOULD |  |
| c9 | 158 | \| **Small functions** \| A function SHOULD do ONE thing, and do it well \| Function > ~20 lines \| | SHOULD (0.20, 0.34) | SHOULD |  |
| c10 | 159 | \| **No side effects** \| A function either DOES or ANSWERS — it SHOULD NOT do both \|… | SHOULD (0.24, 0.28) | SHOULD |  |
| c11 | 160 | \| **DRY** \| You SHOULD NOT repeat yourself \| Copy-paste with minor edits \| | SHOULD (0.35, 0.37) | SHOULD |  |
| c12 | 161 | \| **No magic numbers** \| Literals SHOULD be named \| `if (x > 86400)` \| | SHOULD (0.25, 0.44) | SHOULD |  |
| c13 | 162 | \| **Fail fast** \| Code SHOULD validate early and throw exceptions, not codes \|… | SHOULD (0.20, 0.29) | SHOULD |  |
| c14 | 163 | \| **Boy Scout Rule** \| You SHOULD leave code cleaner than you found it \| No cleanup before commit \| | SHOULD (0.32, 0.30) | SHOULD |  |
| c15 | 167 | 1. **Small** — functions SHOULD NOT exceed ~20 lines; aim for 5–10. | SHOULD (0.38, 0.25) | SHOULD |  |
| c16 | 168 | 2. **Do one thing** — if you can extract a sub-function with a name that isn't just a… | plain (0.87, 0.25), re-asked; first run SHOULD (0.34, 0.28) | plain | backfill 2026-09-25 (prompt-audit F2, keyword on a second copy): the rewrite had made the heading "A function SHOULD do one thing", a second SHOULD on the L1 table's Small-functions rule (c9). Re-asked as a restatement, Jev says plain; the heading reads "Do one thing" again and c9 carries the SHOULD |
| c17 | 170 | 3. **One level of abstraction per function** — you SHOULD NOT mix high-level policy with… | SHOULD (0.31, 0.28) | SHOULD |  |
| c18 | 172 | 4. **No flag arguments** — `render(true)` hides two behaviors; you SHOULD split into… | SHOULD (0.35, 0.29) | SHOULD |  |
| c19 | 172 | 5. **Fewer arguments** — 0 is best, 1 good, 2 fine, 3 needs justification. More th… | SHOULD (0.33, 0.31) | SHOULD |  |
| c20 | 176 | 6. **Command-Query Separation** — change state *or* return a value, not both. | plain (0.83, 0.27), re-asked; first run SHOULD (0.32, 0.29) | plain | backfill 2026-09-25 (prompt-audit F2): "a function SHOULD change state or return a value, not both" was a second SHOULD on the L1 table's No-side-effects rule (c10). Re-asked as a restatement, Jev says plain; the item is a plain imperative again and c10 carries the SHOULD NOT |
| c21 | 178 | Name reveals intent (\`elapsedTimeInDays\`, not \`d\`). | plain (0.75, 0.15) | plain |  |
| c22 | 179 | Pronounceable and searchable; avoid cryptic abbreviations and disinformation. | SHOULD (0.41, 0.20) | plain | style checklist (spec §2: style is plain); uniform with siblings c21/c23/c24 |
| c23 | 180 | Classes are nouns (\`Customer\`, \`Account\`); methods are verbs (\`postPayment\`, \`save… | plain (0.70, 0.16) | plain |  |
| c24 | 182 | No type encodings (\`strName\`, \`iCount\`), no noise words (\`theData\`, \`aInfo\`). | plain (0.50, 0.15) | plain |  |
| c25 | 183 | One word per concept across the codebase — pick \`get\` *or* \`fetch\` *or* \`retrieve… | SHOULD (0.34, 0.19) | plain | style checklist (spec §2: style is plain); uniform with siblings c21/c23/c24 |
| c26 | 188 | A design is simple to the extent that it: | plain (0.94, 0.19) | plain |  |
| c27 | 189 | 1. **Passes all the tests** — it works, verifiably. | MUST (0.53, 0.51) | plain | defines Kent Beck's four rules descriptively, like siblings c26/c28-31; low confidence (0.53) and no gate enforces this restatement (the project's own test suite already governs whether tests pass) |
| c28 | 190 | 2. **Reveals intent** — names and structure say what it does. | plain (0.43, 0.25) | plain |  |
| c29 | 191 | 3. **Has no duplication** — one fact in one place (DRY). | plain (0.69, 0.20) | plain |  |
| c30 | 192 | 4. **Minimizes elements** — no more classes, methods, or abstraction than rules 1–… | plain (0.82, 0.18) | plain |  |
| c31 | 195 | Rules 2–4 are applied by refactoring once it works. **Rule 4 is the guard against … | plain (0.82, 0.16) | plain |  |
| c32 | 278 | \| **Data clumps** — fields always travel together \| extract a class \| | plain (0.77, 0.19) | plain |  |
| c33 | 77 | 2. Make one structural change. Behaviour SHOULD NOT change. | MUST (0.59, 0.45) | SHOULD | backfill 2026-09-25: was lowercase "must not". Confidence 0.59 < 0.6, decided ourselves; harm 0.45 < 0.5 caps it below MUST, as for c4, step 1 of the same loop; step 3 names the alternative (revert or repair) |
| c34 | 126 | ✅ Proportionate: one well-named function that takes the rows (or a path) and returns the total; if it needs to be testable, pass the source in as a parameter. | SHOULD (0.33, 0.17) | plain | backfill 2026-09-25: was lowercase "must". Confidence 0.33 < 0.6 (plain 0.42), decided ourselves: the word sat in a condition inside a labelled illustrative example, not a directive; harm 0.17. Now "needs to be" |

## feynman-walkthrough (5 candidates · 1 MUST · 0 SHOULD · 0 MAY · 4 plain · 0 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 28 | Walk a learner — the user, or an agent onboarding onto an unfamiliar system — thro… | plain (0.69, 0.43) | plain |  |
| c2 | 36 | **Locating this skill's helpers (do this first).** The steps below run bundled sc… | MUST (0.79, 0.73) | MUST | the SKILL.md \`$SKILL_DIR\` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c3 | 98 | 4. **Check understanding — after the walkthrough, never before.** A few targeted … | plain (0.47, 0.33) | plain | Workflow step 4; out of scope per the spec's "workflow steps stay plain imperatives" |
| c4 | 136 | **STALE** → the source moved (new commits, revised doc). Say so before relying on… | plain (0.28, 0.53) | plain | "never trips STALE" describes \`okf.py\`'s diff-detection behaviour, not a directive |
| c5 | 182 | the **reference explainer** — standalone, revisitable, sharable; useful to the le… | plain (0.63, 0.29) | plain | "never saw this session" describes a hypothetical colleague, not a directive |

## tmux-agent-herdr-lite (6 candidates · 1 MUST · 1 SHOULD · 0 MAY · 4 plain · 2 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 14 | **Locating this skill's helpers (do this first).** The steps below run bundled s… | MUST (0.76, 0.78) | MUST | the SKILL.md \`$SKILL_DIR\` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c2 | 45 | **The human never runs a launcher.** The installed zsh hook (\`agent-shell-hook.zs… | plain (0.46, 0.48) | plain |  |
| c3 | 49 | **Humans use tmux only.** They just run their agent (\`claude\`, \`codex\`, …) in an… | plain (0.28, 0.38) | SHOULD | harm 0.38 <0.5 caps below MUST; a genuine behavioural default keeping the human surface pure tmux (per the skill's own description promise), with an alternative named in the same sentence |
| c4 | 54 | The cockpit deliberately claims exactly **one** prefix key (\`prefix a\`) and puts … | MUST (0.32, 0.54) | plain | describes the key-table design's non-collision guarantee, not a directive to the agent |
| c5 | 105 | 5. Verify real outcomes separately — a \`done\` state means the pane looks finish… | plain (0.21, 0.29) | plain | Recommended agent workflow step 5; out of scope per the spec's "workflow steps stay plain imperatives" |
| c6 | 119 | Detection is two-layered, ported from Herdr's manifests: panes with a known age… | plain (0.51, 0.55) | plain |  |

## security-posture-audit (10 candidates · 3 MUST · 0 SHOULD · 0 MAY · 7 plain · 5 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 19 | The division of labor is the point: \`assets/audit_posture.py\` finds candidate de… | plain (0.53, 0.66) | plain |  |
| c2 | 24 | **Locating this skill's helpers (do this first).** The steps below run bundled s… | MUST (0.79, 0.74) | MUST |  |
| c3 | 41 | You MUST state these in the report so it cannot be over-read: | MUST (0.32, 0.53) | MUST |  |
| c4 | 41 | - **Not a CVE scanner.** No advisory database, no network, no version-vulnerabil… | MUST (0.45, 0.54) | plain | describes what the scanner does not do (no advisory DB, no network); not a directive |
| c5 | 43 | - **Not SAST.** No dataflow, taint, or injection analysis. | MUST (0.45, 0.68) | plain | describes what the scanner does not do (no dataflow/taint analysis); not a directive |
| c6 | 44 | - **Not a secrets-content scanner.** It flags credential-*shaped files by name* … | MUST (0.59, 0.69) | plain | describes the scanner's name-only behaviour ("it never reads for secret values"); the pairing tip is advice |
| c7 | 47 | - **Not norms/claims verification** (that is \`base-in-reality\`) and **not an age… | MUST (0.80, 0.80) | plain | names sibling skills for out-of-scope work; a scope description, not a directive |
| c8 | 52 | You MUST audit only repositories the user owns or is explicitly authorized to review; if… | MUST (0.90, 0.87) | MUST |  |
| c9 | 117 | \| \`credential-file\` (.env*/.pem/id_* presence, content never read) \| HIGH \| | MUST (0.61, 0.59) | plain | table row describing the check's behaviour (content never read), not a directive |
| c10 | 142 | - **Unreadable/malformed manifests** surface as `parse-error` entries, never crashes —… | plain (0.95, 0.35), re-asked; first run MUST (0.50, 0.56) | plain | backfill 2026-09-25 (prompt-audit F2, second copy): "you MUST report them as audit blind spots" repeated step 2's plain "do not drop findings silently" for parse-error entries. Re-asked as a restatement, Jev says plain; now a plain instruction |

## base-in-reality (8 candidates · 8 MUST · 0 SHOULD · 0 MAY · 0 plain · 0 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 21 | The defining rule: **no fabricated authority.** Every finding MUST be tied to a source… | MUST (0.90, 0.80) | MUST |  |
| c2 | 35 | 1. **You MUST NOT edit code.** `--annotate` inserts comment markers only — never logic.… | MUST (0.89, 0.76) | MUST |  |
| c3 | 39 | 2. **No fabricated citations.** You MUST cite only URLs/DOIs fetched this session, and… | MUST (0.93, 0.82) | MUST |  |
| c4 | 43 | 3. **Adversarial gate.** A `VIOLATION`/`DEVIATION` MUST NOT be reported unless it survives a refutation pass. | MUST (0.96, 0.73) | MUST |  |
| c5 | 45 | 4. **No silent truncation.** If `--max-claims` caps extraction, you MUST list what was… | MUST (0.87, 0.44) | MUST |  |
| c6 | 76 | - You MUST hand subagents the literal absolute `$FETCH` value — they MUST NOT receive a… | MUST (0.70, 0.80) | MUST |  |
| c7 | 112 | 6. **Synthesize.** Before filling the report, lint the merged findings array wit… | MUST (0.68, 0.80) | MUST |  |
| c8 | 102 | It MUST hold ≥1 fetched source before asserting anything stronger than `UNCONFIRMED`. | MUST (0.91, 0.66) | MUST | backfill 2026-09-25: was lowercase "must"; the subagent-side grounding rule, which `report_lint.py` enforces by rejecting an ungrounded VIOLATION/DEVIATION |

## verifier-installer (10 candidates · 6 MUST · 1 SHOULD · 0 MAY · 3 plain · 0 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 32 | **Locating this skill's helpers (do this first).** The steps below run bundled s… | MUST (0.81, 0.77) | MUST |  |
| c2 | 73 | 1. **Detect the stack.** Run \`python3 "$SKILL_DIR/assets/detect_stack.py" <repo>… | plain (0.61, 0.49) | plain |  |
| c3 | 88 | 3. **Install per the playbook.** For each approved missing rail, follow the matc… | MUST (0.35, 0.64) | MUST |  |
| c4 | 123 | Step 5 hands back this report: | plain (0.95, 0.38), re-asked; first run MUST (0.70, 0.48) | plain | backfill 2026-09-25 (prompt-audit L2): "You SHOULD end with this report" was a weaker second copy of step 5 ("Deliver the summary below") and read as optional on a required deliverable. Re-asked as a restatement, Jev says plain; now descriptive ("Step 5 hands back this report:"), and step 5 stays the instruction |
| c5 | 158 | - **Read-only until step 2's confirmation (or a covering grant)** — detection never… | MUST (0.92, 0.65) | MUST | (no departure) changed in 1.2.0 (autonomy-grant Task 7, now line 152): "(or a covering grant)" … installs MUST happen only after the user approves the plan, or after `check-grant` exits 0 for `local_reversible` as step 2 describes. Still MUST |
| c6 | 161 | - **One ground truth.** Local `verify` and CI MUST run the same commands; when in doubt,… | MUST (0.41, 0.58) | MUST |  |
| c7 | 134 | - **Prove, don't presume.** A rail counts as installed when it was watched faili… | MUST (0.58, 0.55) | MUST |  |
| c8 | 137 | - **Stay off the style battlefield.** Wire checks for whatever formatter/tooling… | SHOULD (0.29, 0.38) | SHOULD |  |
| c9 | 92 | 2. **Confirm the plan with the user.** … You MUST NOT write anything before this confirmation, unless `check-grant --root <repo> --action local_reversible` exits 0 …; then you MAY proceed, and MUST name the grant id and action class in the report. | not judged (new in 1.2.0, autonomy-grant Task 7) | MUST | the step-2 write gate (design spec §5): was plain "Do not write anything before this confirmation."; keyworded now that it carries the grant clause, matching guardrail c5; counted once at MUST, the MAY being the option the grant opens; the eval grades the step's text. Fix round 1: the same step adds "Under a grant you MUST install only the plan's proposals for the missing rails, with GitHub Actions as the CI provider, and MUST report manifest errors … instead of fixing them. The grant lifts this confirmation and nothing more …"; one rule, still counted once at MUST. Final fix wave: "Any other exit (3 ASK/NONE, 2 INVALID, 1 usage error) means ask as usual" (M-c), and a plain sentence that the workflow written under a grant stays local and pushing it asks (`ci-config`, I2); no new keyword, counts unchanged |
| c10 | 166 | This skill follows [skill-contract v1](…). It consumes autonomy grants, which only lift step 2's confirmation, … | not judged (new in 1.2.0, autonomy-grant Task 7) | plain | describes the skill-contract adoption, like crafting-self-prompting-loops c10; the rule it points at is carried by c5 and c9 |

## agent-ready-rails (7 candidates · 4 MUST · 1 SHOULD · 0 MAY · 2 plain · 3 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 20 | **Locating this skill's helpers (do this first).** The steps below run bundled s… | MUST (0.86, 0.74) | MUST |  |
| c2 | 41 | **Tier 1 — Build rails (author → merge).** Always audited. These decide whether … | MUST (0.55, 0.70) | plain | "Always audited" describes Tier 1's scope; the scoping directive itself is step 1, a plain workflow step |
| c3 | 63 | Each rail scores **0 (absent) / 1 (partial) / 2 (agent-grade)**. You MUST report the two… | MUST (0.80, 0.74) | MUST |  |
| c4 | 67 | Follow these steps in order. Steps 1–4 MUST stay read-only and always run; step 5 MUST… | MUST (0.38, 0.65) | MUST |  |
| c5 | 79 | It walks the target repo read-only and emits sorted JSON evidence per Tier-1 rai… | MUST (0.86, 0.71) | plain | describes what collect_evidence.py does (collects and flags, never scores; settings-error instead of crashes); not a directive |
| c6 | 81 | Then, for each rail in scope, verify and extend the collector's leads against th… | MUST (0.95, 0.79) | MUST |  |
| c7 | 103 | You SHOULD structure the scorecard like this: | MUST (0.73, 0.51) | SHOULD | report format, not machine-consumed; aligned with verifier-installer c4 |

## context-hygiene-kit (6 candidates · 4 MUST · 0 SHOULD · 0 MAY · 2 plain · 1 departure)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 52 | Memory is **always per-project** — even a global install keeps each repo's \`.con… | MUST (0.43, 0.51) | plain | the always/never sentence describes the kit's per-project storage (memories never bleed across repos); the restart reminder is an ordinary install step |
| c2 | 87 | 1. **The token budget MUST be a HARD cap (anti-bloat).** `curate()` asserts … | MUST (0.95, 0.82) | MUST |  |
| c3 | 86 | 2. **Two-channel boundary (LSC-7).** The **load-bearing** prompt-injection contr… | MUST (0.94, 0.88) | MUST |  |
| c4 | 87 | 3. **Deterministic capture only.** No model summarises the session. The harveste… | MUST (0.91, 0.81) | MUST |  |
| c5 | 100 | You MUST confirm the gate passed: `python3 test_context_ledger.py` (27 tests — budget… | MUST (0.94, 0.84) | MUST |  |
| c6 | 88 | if untrusted content ever has to reach a tool-capable downstream model | plain (0.73, 0.51) | plain | backfill 2026-09-25: was lowercase "must" in the condition of c3's SHOULD; the condition describes a situation, not a rule. The SHOULD after it stays with c3 |

## starlight-handbook-kit (2 candidates · 2 MUST · 0 SHOULD · 0 MAY · 0 plain · 0 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 23 | **Locating this skill's helpers (do this first).** The steps below run bundled … | MUST (0.77, 0.78) | MUST | |
| c2 | 115 | 4. Diagrams MUST use the `<Mermaid code={\`...\`} />` component, not a fenced ````… | MUST (0.34, 0.46) | MUST | low confidence (0.34) and harm 0.46 < 0.5, but mechanically enforced by the `verify:mermaid` gate; backfill 2026-09-25 (prompt-audit F3): the second keyword on the same rule, "A fenced ```mermaid block MUST NOT be used", is now the plain clause "not a fenced ```mermaid block" (Jev on the clause: plain 0.68, harm 0.66; the MUST and the gate carry the harm). Row level unchanged |

## okf-site-kit (6 candidates · 4 MUST · 1 SHOULD · 0 MAY · 1 plain · 2 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 35 | **Locating this skill's helpers (do this first).** The steps below run bundled s… | MUST (0.75, 0.76) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c2 | 52 | - **The bundle is read-only.** The generator MUST NOT mutate the source bundle. When the… | MUST (0.60, 0.59) | MUST | |
| c3 | 53 | - **Tolerance over rejection.** Per the OKF spec's consumer rules, unknown types… | MUST (0.80, 0.77) | MUST | |
| c4 | 57 | - **Spec tracking.** The generator targets OKF v0.1. If the spec has moved (che… | MUST (0.38, 0.64) | SHOULD | fix round 1: confidence 0.38 < 0.6, decided ourselves; `references/okf-spec.md:44-51` itself allows generating without updating — "Before generating ... when network access exists — and always when a bundle declares an okf_version other than 0.1, check the spec URL" — and its own invariant is "Never silently emit ... the WARN: report exists so nothing is dropped without a trace", not "always update the generator first". The check is conditional (network access, or a mismatched okf_version), so a blanket MUST would overclaim; SHOULD fits a strong default with that legitimate condition |
| c5 | 103 | - **Producer-extended bundles** (e.g. `feynman-walkthrough`'s explainers): arbit… | MUST (0.31, 0.61) | plain | describes the generator's rendering behavior for extra frontmatter keys; a descriptive never, not a directive (confidence 0.31 < 0.6) |
| c6 | 76 | 3. **Build and verify.** `cd <dir> && npm install && npm run build` MUST complete with zero errors; | MUST (0.81, 0.52) | MUST | backfill 2026-09-25: was lowercase "must"; a site that does not build is not a deliverable |

## spec-first-planning (22 candidates · 12 MUST · 0 SHOULD · 0 MAY · 10 plain · 1 departure)

Line numbers refreshed in the factory-conductor final fix wave (2026-09-23, spec-first-planning 2.2.0). That wave added no keyword here: the `[cmd: …]` guidance, the factory-conductor handoff sentence (I4) and the run-branch example (c18) are plain text or edits inside existing rows.

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 36 | **Locating this skill's helpers (do this first).** The steps below run bundled … | MUST (0.67, 0.72) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c2 | 51 | - **The spec** — drafted from `references/spec-template.md`. It MUST include these… | MUST (0.79, 0.64) | MUST | enforced by `assets/spec_lint.py`'s missing-section FAIL; backfill 2026-09-25: the lowercase "must" quoted in this block is now inline code (c19) |
| c3 | 59 | - **The plan** — one or more tasks per requirement, each carrying *what* to cha… | MUST (0.87, 0.70) | MUST | enforced by `assets/spec_to_tasks.py`'s UNCOVERED gate |
| c4 | 68 | The exact grammar, lint rules, JSON schema, and exit codes live in `references/… | plain (0.32, 0.37) | plain | |
| c5 | 202 | This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills… | plain (0.53, 0.66) | plain | describes the contract's shape (predicateType, schema, claim status); the MUSTs of skill-contract commandments 8/10 are carried by workflow step 6 (recorded as c10), not by this descriptive section |
| c6 | 222 | - **Not the executor.** This skill ends at the handoff; implementation belongs… | MUST (0.40, 0.65) | plain | confidence 0.40 < 0.6, decided ourselves; describes the skill's scope boundary, not a directive — matches the security-posture-audit Boundaries precedent |
| c7 | 224 | - **Not a project-management tool.** No estimates, sprints, assignees, or stat… | plain (0.42, 0.29) | plain | |
| c8 | 226 | - **Complements heavier PRD workflows.** When a full PRD process is in play, u… | plain (0.55, 0.29) | plain | |
| c9 | 229 | - **Not a code-quality judge.** "How should I structure this service?" asked a… | plain (0.63, 0.27) | plain | |
| c10 | 138 | 6. **Hand off through skill-contract.** … If it names one, propose the handoff … | not extracted (no never/always, not a hard-rule heading) | MUST / MUST NOT | fix round 1, correcting 0f5e62a: this workflow step directly implements skill-contract commandment 10 ("A producer MUST propose each handoff and wait for a yes") and commandment 8's "MUST NOT fail when none exists"; the earlier commit's rationale for leaving it plain was wrong — the creed's own MUSTs apply here, not just to the `## Contract` section |
| c11 | 90 | 0. **Pick the mode.** … the full loop MUST run … You MUST tell the user which depth you are using … you MUST offer two switches … and you MUST NOT escalate unless the user asks. | not judged (new in 2.0.0, autonomy-grant Task 6) | MUST / MUST NOT | mode escalation (design spec §4, A6): unattended is opt-in, and nothing escalates without the user asking; the eval checks the lint modes the depths map to. Final fix wave (T6 M3): the switches MUST is scoped "In attended mode, at each checkpoint …"; still one candidate |
| c12 | 113 | Each pass of the full loop adds one line to the Iterations section; after 5 iterations without convergence, you MUST stop and ask the user how to proceed. | not judged (new in 2.0.0, autonomy-grant Task 6) | MUST | the iteration cap (design spec §4 convergence); `spec_lint.py --converged` fails a sixth `I<n>` with "iteration cap exceeded — stop and ask the user" backfill 2026-09-25: re-quoted in full (the row began with an ellipsis, so it matched no text) |
| c13 | 147 | If it exits 0 (`GRANT: COVERED`), you MAY hand off without asking, and you MUST name the grant id and class in your report. Otherwise … you MUST propose the handoff … and MUST wait for the user's yes … | not judged (new in 2.0.0, autonomy-grant Task 6) | MUST | the handoff under a grant (skill-contract commandment 10 as amended, design spec §5); counted once at MUST — the MAY is the option the grant opens, the MUSTs are the report and the fallback; c10 still carries the no-grant propose-and-wait rule. Final fix wave: the MAY holds "only when the envelope you hand off is the plan the grant pins (one of its subjects)", which `check-grant --subject <envelope path>` enforces (M-g), and the fallback reads "any other exit: 3 ASK/NONE, 2 INVALID, 1 usage error" (M-c); still counted once at MUST |
| c14 | 163 | You MUST wait for the user's explicit yes before running: `write_grant.py …` | not judged (new in 2.0.0, autonomy-grant Task 6) | MUST | a grant is the user's recorded yes (design spec §4 unattended mode); writing one without it is the forgery the threat model names; eval checks the text |
| c15 | 177 | Tell the user plainly that `merge`, `deploy`, `spend`, `external_message` and `delete` are never covered by a grant and will always ask: you MUST ask the user right before any of them, whatever the grant says. | not judged (new in 2.0.0, autonomy-grant Task 6) | MUST | irreversible actions always ask (A8); the checker makes any non-`ask` gate on these five INVALID and `write_grant.py` refuses one; eval checks the text and the refusal |
| c16 | 183 | Under a grant, you MUST push only the current branch, to the remote branch of the same name, and MUST NOT force-push. | not judged (new in 2.0.0, autonomy-grant Task 6) | MUST | the push rule (controller ruling on the Task 1 review, M8): `check-grant` checks the local branch, not the push target, so the rule carries that gap. Final fix wave (M-b): "…of the same name, and MUST NOT force-push" (the same rule, one candidate), plus a plain sentence that a CI-config push counts as `deploy` (I2) backfill 2026-09-25: re-quoted to the sentence as it now reads |
| c17 | 170 | `--accepted-by` is the name the user gives you: if you don't know it, you MUST ask the user for it rather than taking it from git config or inventing one. | not judged (new in the final fix wave, T6 M4) | MUST | the grant records who said yes; a name taken from git config or invented is a forged acceptance. Worded as MUST ask … rather than MUST NOT take, so PP-5 stays at 6 absolutist directives (not newly advisory) |
| c18 | 180 | Before the handoff check you MUST be on a branch matching `branch_pattern` (e.g. `git switch -c factory/work` — any name but `factory/<plan-slug>`, which factory-conductor creates as its run branch). | not judged (new in the final fix wave, T6 M2) | MUST | without it, `check-grant` answers ASK `branch` or `default-branch` and the grant the user just approved does nothing; the checker enforces the floor, the rule tells the agent how to meet it |
| c19 | 54 | Requirements (numbered R1..Rn, each a single testable `must` statement) | plain (0.83, 0.47) | plain | backfill 2026-09-25: was a quoted lowercase "must", a mention of the modal token `spec_lint.py` requires in each requirement; now inline code |
| c20 | 62 | A requirement can also carry `[after: R2, R3]`, naming the other requirements it comes after; | plain (0.52, 0.30) | plain | backfill 2026-09-25: was lowercase "it must follow", describing ordering; confidence 0.52 < 0.6, decided ourselves, plain as Jev chose |
| c21 | 76 | *Anchor* works back from the outcome, asking "what has to be TRUE?" | plain (0.98, 0.24) | plain | backfill 2026-09-25: was the quoted question "what must be TRUE?", describing the Anchor stage |
| c22 | 103 | Write each requirement as one testable `must` statement | plain (0.61, 0.47) | plain | backfill 2026-09-25: was a quoted lowercase "must", a mention of the token `spec_lint.py` requires; now inline code. The step stays a plain imperative |

## bug-autopsy (4 candidates · 2 MUST · 1 SHOULD · 0 MAY · 1 plain · 1 departure)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 32 | **Locating this skill's helpers (do this first).** The steps below run bundled … | MUST (0.66, 0.66) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c2 | 49 | - **Evidence or inference, labeled.** Every timeline entry and every "why" MUST cite its… | MUST (0.36, 0.51) | MUST | confidence 0.36 < 0.6, decided ourselves; mechanically enforced by `assets/postmortem_lint.py`'s evidence-citation FAIL check |
| c3 | 56 | - **Blameless, structurally.** Root causes SHOULD be systemic — a missing guardrail, an… | plain (0.42, 0.34) | SHOULD | fix round 1: `references/five-whys.md:50` and `references/postmortem-template.md:50` both assert "never a person" as the skill's own standing rule, but `assets/postmortem_lint.py`'s blame-phrasing check is an advisory WARN, not a FAIL — no gate enforces it, so it lands at SHOULD/SHOULD NOT, consistent with knowledge-gardener's c2 |
| c4 | 55 | - **Boundaries.** This skill explains failures that already happened. Live deb… | plain (0.38, 0.44) | plain | scope statement; plain like every other skill's boundary (plan ruling reversed at final review) |

## knowledge-gardener (2 candidates · 1 MUST · 1 SHOULD · 0 MAY · 0 plain · 1 departure)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 31 | **Locating this skill's helpers (do this first).** The steps below run bundled … | MUST (0.81, 0.77) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c2 | 106 | - **You SHOULD NOT silently rely on a stale explainer** — the whole point of the pins is… | MUST (0.16, 0.41) | SHOULD NOT | confidence 0.16 < 0.6, decided ourselves; harm 0.41 < 0.5 caps it below MUST — no gate or test can enforce a conversational disclosure step |

## repo2skill (1 candidates · 1 MUST · 0 SHOULD · 0 MAY · 0 plain · 0 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 29 | **Locating this skill's helpers (do this first).** The steps below run bundled … | MUST (0.81, 0.73) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |

## ai-migration-operating-model (4 candidates · 3 MUST · 0 SHOULD · 0 MAY · 1 plain · 0 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 32 | **Locating this skill's helpers (do this first).** The steps below run bundled … | MUST (0.81, 0.75) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked |
| c2 | 131 | - **Not the bulk executor.** This skill ends when the machine is built and the … | plain (0.50, 0.49) | plain | describes the skill's scope boundary, not a directive — matches the security-posture-audit/spec-first-planning Boundaries precedent |
| c3 | 136 | - **Honest about "no".** If no judge can be built, the answer MUST be "don't migrate yet"… | MUST (0.39, 0.63) | MUST | confidence 0.39 < 0.6, decided ourselves; harm 0.63 justifies MUST on its own — a false "go" verdict on an unverifiable migration is the harm this doctrine exists to prevent |
| c4 | 136 | - **Parity over aesthetics.** For money movement, fees, schedules, reconciliat… | MUST (0.76, 0.85) | MUST | |

## factory-conductor (20 candidates · 13 MUST · 1 SHOULD · 1 MAY · 5 plain · 1 departure)

New skill (factory-conductor 1.0.0, plan Task 6, 2026-09-23), written with BCP 14 from the start. Jev did not judge these rows; each level follows the rule it carries and names what enforces it, as the spec-first-planning 2.0.0 rows do. Line numbers were refreshed in the final fix wave (2026-09-23), and again for the Q3 gaps (2026-09-25): G1 (resume prints `NEXT:` lines), G2 (a derived dispatch cap) and G3 (the integration re-run before the push). That work added no candidate: c3 was reworded in place (below), and the new resume, integration and cost text is plain. Line numbers refreshed again for the Q3 review fixes (2026-09-25: `NEXT: run ask`/`run done`/`verify`, resume's dispatch spending, the gated and retryable integration re-run, the push pinned to the verified head); those edits added no keyword.

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 35 | **Locating this skill's helpers (do this first).** The steps below run bundled … | not judged (new skill) | MUST | the SKILL.md `$SKILL_DIR` convention is linted by asset-paths.sh; the subagent hand-off itself is not gate-checked (same block and level as every other adopter) |
| c2 | 66 | You MUST have all three before you run `conductor init`: | not judged (new skill) | MUST | design spec §2.1; `init` enforces all three (C3–C7 and C6 on verify commands exit 2, `check-grant` ASK exits 3, branch and run-branch pattern ASK exit 3); the rule stops the agent from routing around a refusal. Fix round 1 (I1): the grant check names the plan as `--subject`, since `conductor gate` before `init` has no run and so no subject; worded as MUST have rather than MUST NOT start (same rule) to keep PP-5 non-advisory backfill 2026-09-25: re-quoted exactly (the row paraphrased the list that follows) |
| c3 | 89 | When neither the grant nor `init --budget` sets `max_dispatches`, `init` derives it by default as tasks × 2 × (1 + `max_repairs_per_task`) … and `status` marks it derived; you SHOULD tell the user that cap before the run starts. | not judged (new in fix round 1, ruling D; reworded for the Q3 gaps, G2) | SHOULD | ruling D left `max_dispatches` unset by default, and this row told the agent to warn that cost was unbounded. G2 (2026-09-25) makes `init` derive tasks × 2 × (1 + `max_repairs_per_task`) when nothing sets it, so cost is always bounded; the row now asks the agent to name that cap. The tool enforces the cap itself; telling the user is a recommendation, since the run is bounded either way. Same candidate, same level |
| c4 | 102 | `--budget '<json>'` MAY tighten the grant's budget; it cannot loosen a key the grant sets (exit 2). | not judged (new skill) | MAY | an option the agent or user may take; the "cannot loosen" half is enforced by `init` (a looser value exits 2) and stated as fact, not as a keyword |
| c5 | 120 | The reviewer MUST be a fresh subagent, not the executor that wrote the code. | not judged (new skill) | MUST | design spec C3 (an independent review is half the proof) and §7a (the reviewer is a named mitigation for a hostile executor); the tool cannot see who reviewed, so the rule carries it |
| c6 | 155 | When the run stops for any reason but `grant_ask`, you MUST stop or wait for the subagents still working, then run `conductor finish` … | not judged (new skill) | MUST | design spec §3 ("the run stops, then finishes with whatever is proven"); without it no run-result, push or PR exists and the parked work is invisible. Fix round 1 (M11): stop or await in-flight subagents first, so none commits after `finish` parks its task. Q3 fix: a `grant_ask` stop waits for a renewed grant (`NEXT: run ask`) instead, so the user stays in control |
| c7 | 178 | Every step goes through `conductor`: you MUST NOT merge, push, force-push, open the PR or edit code yourself, even when a gate asks. | not judged (new skill) | MUST | design spec §1 ("it never edits code itself") and §3 (every consequential action is gated); a hand-run push or merge skips the gate, the proof and the log. Fix round 1 (I4): names force-push explicitly |
| c8 | 181 | You MUST NOT edit, delete or recreate `state.json`, `autonomy-log.jsonl` or anything under `.skill-contract/`; report a mismatch instead. | not judged (new in fix round 1, I4) | MUST | the state and log are the run's evidence and `log_sha256` pins the log; §7a leaves same-user forgery to review and CI, so the conductor itself must never be the one rewriting them |
| c9 | 240 | You MUST NOT dispatch subagents, push, merge or switch branches. (inside the executor-brief template) | not judged (new skill) | MUST | addressed to the executor, inside a fenced template, so PP-7 does not scan it; kept as a keyword because the brief is sent verbatim, nested dispatches bypass `max_dispatches`, and a push, merge or branch switch escapes the gates. Fix round 1 (M7): the push/merge/switch clause was plain "do not" |
| c10 | 260 | You MUST NOT dispatch subagents or edit any file. (inside the reviewer-brief template) | not judged (new in fix round 1, M7) | MUST | addressed to the reviewer, inside a fenced template; a reviewer that edits the worktree changes the commit it was asked to judge, and nested dispatches bypass `max_dispatches` |
| c11 | 276 | On a `NEEDS_DECISION` report you MUST run `conductor decision <task> --question "…"` and carry on with the other tasks. You MUST NOT answer a human-decision question yourself, even when the answer looks obvious. | not judged (new skill) | MUST / MUST NOT | design spec §3 (a decision the grant does not cover goes to the human); `decision` parks with `new_human_decision` and the run goes on; the tool cannot tell an answered question from an asked one, so the rule carries it; one candidate |
| c12 | 305 | The report MUST state that `max_tokens` and `max_usd` were recorded, not enforced, as `conductor status` does. | not judged (new skill) | MUST | design spec C5 and §3 ("the SKILL.md and the run report MUST say so"); `status`, the run-result `budget_note` and the PR body carry the same note |
| c13 | 361 | This skill follows [skill-contract v1](…). It consumes a task-plan/v1 envelope … and an autonomy-grant/v1 … It provides a run-result/v1 envelope … | not judged (new skill) | plain | describes the adoption and the claim status, like spec-first-planning c5; the rules it relies on are carried by c2 and by the checker |
| c14 | 379 | - **Not the planner.** The spec, the plan and the grant come from spec-first-planning. | not judged (new skill) | plain | scope boundary, plain like every other skill's |
| c15 | 380 | - **Not the loop designer.** Designing a loop, or auditing one, is crafting-self-prompting-loops. | not judged (new skill) | plain | scope boundary |
| c16 | 381 | - **Not a merger.** … You MUST leave merging the run branch or the PR into the default branch to the human; a grant cannot cover `merge`. | not judged (new skill) | MUST | A8 (merge is never grantable) and design spec §7a (merging stays with the human is the last defence against a hostile same-user executor); `check-grant` asks on `merge`, the rule covers a merge run outside the conductor. Fix round 1: worded as MUST leave … to the human (was MUST NOT merge; same rule) to keep PP-5 non-advisory |
| c17 | 384 | - **Not a spend governor.** Token and dollar caps are recorded, not enforced. | not judged (new skill) | plain | scope boundary; the reporting rule is c12 |
| c18 | 179 | You MUST NOT pass a `--pr-cmd` or `--push-cmd` that does anything but push the run branch or open the PR; they exist for stubs and for hosts without `gh`, so by default pass neither. | not judged (new in the final fix wave, M1) | MUST | `finish` runs these argv lists after the `push_branch` and `open_pr` gates, and the push allowlist forces the push's shape, but a `--pr-cmd` is any program: a command that does something else would run under a gate that approved only a PR. The tool cannot see intent, so the rule carries it; "by default pass neither" keeps PP-5 non-advisory |
| c19 | 171 | An exit 2 from `finish` (for example, the plan envelope was edited after `init`, or the grant file is gone) means report its stderr and stop: you MUST leave restoring the plan or the grant to a human. | MUST (0.93, 0.63) | MUST | backfill 2026-09-25: was lowercase "a human must restore"; worded as MUST leave … to a human, like c16, so PP-5's count is unchanged. Preconditions already say not to repair a plan or grant yourself |
| c20 | 322 | dependency directories such as `node_modules` or `.venv` are absent, so a command that needs them fails unless it installs them or uses tooling installed globally | MUST (0.31, 0.63) | plain | backfill 2026-09-25: was lowercase "a command must install its dependencies". Confidence 0.31 < 0.6, decided ourselves: the section describes what the proof covers, and the conductor never writes verify commands (spec-first-planning derives them from `[cmd: …]`), so for this reader it is a consequence, not a directive; reworded as the failure it causes |

## jev-agent-setup (3 candidates · 2 MUST · 0 SHOULD · 0 MAY · 1 plain · 0 departures)

New section, backfill 2026-09-25: the skill (jev-agent-setup 1.x, PR #62) shipped with keywords but no register section. Candidates per spec §2 (no hard-rule heading; every never/always sentence and every keyword sentence), judged by Jev in one batch.

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 26 | The block governs decisions only. It never lets Jev approve or override a permission decision. | plain (0.65, 0.46) | plain | describes what the installed block does; the permission rule itself lives in that block, not in this skill's workflow |
| c2 | 46 | If your harness refuses writes to an agent instruction file (Claude Code auto mode treats `~/.claude/CLAUDE.md` as self-modification), you MUST NOT route around it. | MUST (0.83, 0.74) | MUST |  |
| c3 | 49 | You MUST NOT ask for the key in chat or write it anywhere. | MUST (0.99, 0.77) | MUST |  |
