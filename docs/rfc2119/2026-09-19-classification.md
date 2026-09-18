# BCP 14 classification, 2026-09-19

Spec: [`docs/superpowers/specs/2026-09-19-bcp14-skills-design.md`](../superpowers/specs/2026-09-19-bcp14-skills-design.md) §2.

**What a candidate is.** Every block under a hard-rule heading (Invariants, Rules, Safety, Boundaries, Trust, Guardrails, Contract, Non-negotiables), plus every sentence containing never or always. Code blocks and inline code are excluded.

**Where the levels come from.** Jev (`jev-1.13.0`) judged each candidate's level and, separately, whether ignoring it causes harm (RFC 2119 §6). A rewriter then set the final level. **The final level is the one in force.** Any departure from Jev carries a reason.

**Levels:**
- **MUST** includes MUST NOT.
- **SHOULD** includes SHOULD NOT.
- **MAY** is an option.
- **plain** means no keyword; the sentence is left as prose.

Skills appear in rewrite order.

## test-safety-net (27 candidates · 10 MUST · 0 SHOULD · 0 MAY · 17 plain · 16 departures)

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
| c11 | 259 | - **Never keep a test reported `ok` from a run that exited nonzero.** | MUST (0.69, 0.78) | MUST |  |
| c12 | 283 | **Copy the whole block.** `<package>` is the package directory (`./internal/billing`), an… | MUST (0.60, 0.75) | plain | restates Invariant 1 (append, never overwrite), which already carries the MUST NOT; one keyword per rule |
| c13 | 324 | **Copy the whole block.** Run it from the crate root, adding `-p <package>` for a workspa… | MUST (0.52, 0.73) | plain | restates Invariant 1 (append, never overwrite), which already carries the MUST NOT; one keyword per rule |
| c14 | 369 | - A unit the ranker tiers 3 as *not reachable* or *binary-only* is reported, never tested. | MUST (0.51, 0.76) | plain | states the triage outcome for an unreachable unit; a test for one cannot be proven (Invariant 3 carries the MUST NOT) |
| c15 | 380 | - A compile error is exit 5, never RED; cargo exits 101 for both. | MUST (0.47, 0.79) | plain | defines the exit mapping (compile error is exit 5, never RED); descriptive |
| c16 | 382 | - a dependency not in the local cargo cache (run `cargo fetch`, or build the tests once;… | MUST (0.52, 0.80) | plain | "the proof never downloads anything" describes the guard |
| c17 | 397 | - The guard never passes `--nocapture`, and do not add it: under it the default panic hoo… | MUST (0.67, 0.84) | MUST |  |
| c18 | 406 | - **Tier 2 candidate:** blocks the uncontrollable groups always, plus every controllable… | MUST (0.64, 0.81) | plain | "blocks ... always" describes the Tier 2 guard; the "name all of it" imperative is a workflow step |
| c19 | 412 | The guard patches the **lowest** layer reachable, which for CPython is the `os` primitive… | MUST (0.87, 0.83) | plain | "never routes through" describes CPython internals |
| c20 | 467 | 1. **Never modifies source.** Only creates test files; **appends** to an existing test fi… | MUST (0.90, 0.85) | MUST |  |
| c21 | 470 | 2. **Never writes a test that performs real I/O.** Enforced by the tier-aware runtime gua… | MUST (0.94, 0.83) | MUST |  |
| c22 | 473 | 3. **Never ships an unproven test.** A test that did not go RED is discarded and listed u… | MUST (0.88, 0.66) | MUST |  |
| c23 | 475 | 4. **Never leaves the suite red.** End state is a green suite plus suspected bugs in the… | MUST (0.92, 0.77) | MUST |  |
| c24 | 477 | 5. **Hard gate before writing** — step 3's confirmation happens before any test file is t… | MUST (0.86, 0.76) | MUST |  |
| c25 | 484 | > Emit every captured value with `repr()`. Never build a test's expected value by string… | MUST (0.97, 0.82) | MUST |  |
| c26 | 519 | **Promoted units** get a line in the report naming the ranker's original tier, the tier u… | MUST (0.73, 0.44) | plain | harm 0.44 caps it at SHOULD, and a SHOULD here would contradict step 2's MUST NOT (c7) for the same rule; left as its restatement |
| c27 | 522 | **How to improve this.** `inbound_refs` is a static approximation — an identifier-occurre… | plain (0.36, 0.16) | plain |  |

## world-model-ledger (10 candidates · 7 MUST · 0 SHOULD · 0 MAY · 3 plain · 6 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 93 | \| **PostToolUse** → `hooks/posttooluse-observe.sh` \| after **every** tool call \| the univ… | MUST (0.82, 0.79) | plain | the "never" describes what the PostToolUse hook reads (tool input, not output); Invariants 2 and 3 carry the rule |
| c2 | 99 | The model maintains itself: the universal `PostToolUse` hook captures entities and behavi… | MAY (0.41, 0.28) | plain | the only never/always sentence ("A model never guesses facts inside a hook") describes the hook design; Invariant 2 carries the MUST NOT |
| c3 | 107 | 0. **(Usually automatic) Seed the model repo-wide.** `SessionStart` auto-seeds a fresh re… | plain (0.31, 0.28) | plain |  |
| c4 | 151 | 1. **Code observation never raises normative confidence.** A hook may set `observed_conf`… | MUST (0.96, 0.85) | MUST |  |
| c5 | 154 | 2. **No invented facts in hooks.** Hooks capture only what is deterministically parseable… | SHOULD (0.34, 0.71) | MUST | harm 0.71: a model summarizing inside a hook would put invented facts in the ledger; one of the invariants the skill says not to weaken |
| c6 | 158 | 3. **Trusted channel only.** `tool_result` / `tool_use` content is never harvested into f… | MUST (0.90, 0.86) | MUST |  |
| c7 | 163 | 4. **Append-only evidence; soft-invalidate, never hard-delete.** Superseded facts get `in… | SHOULD (0.72, 0.61) | MUST | harm 0.61 and soft-invalidation is asserted by the install-gate suite (test_derive_skips_invalidated, test_prune_soft_invalidates_vanished_edges); "confidence is always derived" describes the derivation and stays plain |
| c8 | 166 | 5. **Every triple is ontology-checked before it enters the ledger.** Predicates are a clo… | SHOULD (0.51, 0.70) | MUST | the write-boundary contract: harm 0.70, and the eval (ontology rejects a hallucinated predicate / an impossible triple) and unit tests enforce it; "hooks never break" describes the hooks and stays plain |
| c9 | 173 | Prefer these defaults; when a situation genuinely needs an exception, surface it to the u… | SHOULD (0.88, 0.50) | MUST | silently working around an invariant is the harm the invariants exist to prevent (harm 0.50); a SHOULD would permit it. Deliberate meaning clarification: "Prefer these defaults" is dropped because the heading says "do not weaken these" and the invariants are MUST (controller ruling; Jev 0.97 for keeping MUST) |
| c10 | 178 | Always confirm the gate passed: `python3 test_world_model.py` (108 tests — the two-axis i… | MUST (0.59, 0.71) | MUST |  |

## crafting-self-prompting-loops (11 candidates · 7 MUST · 0 SHOULD · 0 MAY · 4 plain · 4 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 26 | Your job with this skill: turn a fuzzy "make it keep going until it's good" request into … | MUST (0.49, 0.85) | plain | the never/always list describes the properties of a sound loop; step 4 non-negotiables carry the MUSTs |
| c2 | 34 | Ask the user (or infer, then state your assumption): *what is this loop trying to achieve… | MUST (0.69, 0.74) | plain | "can never legitimately stop" is rationale for the done test; the imperative is a workflow step |
| c3 | 49 | \| LSC-2 \| Stop condition (primary) \| how the model signals "done" (a flag/token the harne… | MUST (0.79, 0.85) | MUST |  |
| c4 | 61 | These are the constraints loops most often skip and most often die on. Never ship a loop … | MUST (0.81, 0.84) | MUST |  |
| c5 | 63 | - **A mandatory backstop (LSC-3).** Model self-termination (LSC-2) *can fail* — the model… | MUST (0.95, 0.89) | MUST |  |
| c6 | 64 | - **The two-channel boundary (LSC-7).** Anything the model produces, a tool returns, or c… | MUST (0.87, 0.88) | MUST |  |
| c7 | 65 | - **A human gate where it matters (LSC-8).** Any irreversible or externally-visible actio… | MUST (0.70, 0.79) | MUST | (no departure) note: the output-only exemption in the same block carries MAY, a genuine option |
| c8 | 72 | 2. **A runnable scaffold** — in the user's target runtime. For Claude Code, that's the re… | MUST (0.27, 0.62) | MUST |  |
| c9 | 80 | If the user has a loop already and it misbehaves, run steps 3–6 as a *checklist audit*: s… | plain (0.57, 0.51) | plain |  |
| c10 | 127 | This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/… | MUST (0.33, 0.42) | plain | describes the skill-contract adoption, not a rule (harm 0.42); the section and its json block are left untouched |
| c11 | 137 | ALWAYS structure the result like this: | plain (0.34, 0.51) | MUST | the named deliverable format and its already-absolute ALWAYS; harm 0.51, and the template carries the mandatory backstop and <data> slots |

## mockstar-mock (14 candidates · 10 MUST · 1 SHOULD · 0 MAY · 3 plain · 3 departures)

| id | line | sentence | Jev level (conf, harm) | final | departure reason |
|---|---|---|---|---|---|
| c1 | 21 | The defining rule: **no fabricated endpoints.** Every mock traces back to a fetche… | MUST (0.92, 0.83) | plain | restates Invariant 1 (no fabricated endpoints), which carries the MUST NOT; one keyword per rule |
| c2 | 46 | \`bunx @dhaneshpurohit/mockstar\` available (install: \`bun add -g @dhaneshpurohit/mock… | MUST (0.74, 0.80) | MUST |  |
| c3 | 60 | 1. **No fabricated endpoints.** Every endpoint in the output must trace to a fetc… | MUST (0.96, 0.82) | MUST |  |
| c4 | 62 | 2. **Prefer native tooling.** Use \`bunx @dhaneshpurohit/mockstar import\` for OpenAP… | SHOULD (0.28, 0.48) | SHOULD | harm 0.48 caps it below MUST; a strong default with a legitimate exception (the hand-authored path used elsewhere for non-liftable inputs) |
| c5 | 65 | 3. **Schema-valid output.** Verification (Stage 5) boots the server. A mock proje… | MUST (0.84, 0.74) | MUST |  |
| c6 | 68 | 4. **No silent truncation.** When \`--max-endpoints\` caps the inventory, every drop… | MUST (0.87, 0.76) | MUST |  |
| c7 | 70 | 5. **Read-only inputs.** Never modify source spec files, HAR archives, or documen… | MUST (0.56, 0.74) | MUST | low confidence, but Invariants heading + real data-loss harm (0.74) support MUST |
| c8 | 81 | \`--runtime auto\|local\|docker\` — selects how mockstar is invoked (default: \`auto\`)… | MUST (0.15, 0.74) | plain | describes what each \`--runtime\` value does; not a directive to the agent (confidence 0.15) |
| c9 | 100 | Hand subagents the literal absolute \`$EXTRACT\` and \`$SMOKE\` values — never a rela… | MUST (0.71, 0.82) | MUST | mechanically enforced by \`scripts/gates/asset-paths.sh\` |
| c10 | 177 | For **documentation URLs**, fetch the content first with \`curl -L\` (or WebFetch),… | MUST (0.77, 0.82) | MUST |  |
| c11 | 216 | **Prose** — extract from code fences, Markdown tables, and inline backtick refer… | MUST (0.75, 0.81) | MUST |  |
| c12 | 253 | \`webhookHints[]\` → \`webhooks[]\` on the triggering entry. When a hint carries \`sig… | MUST (0.82, 0.82) | MUST |  |
| c13 | 379 | **Consequence for this skill:** if \`--tenant\` is not \`default\`, every consumer —… | MUST (0.80, 0.82) | plain | the only always ("the CLI serve path always enables path + header modes") describes default CLI config, not a directive; not under a hard-rule heading |
| c14 | 464 | The skill's asset helpers (\`assets/extract_text.py\`, \`assets/smoke.sh\`) live in t… | MUST (0.81, 0.81) | MUST | mechanically enforced by \`scripts/gates/asset-paths.sh\` |
