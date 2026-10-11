# idea-validation: a falsifiable gate before planning (roadmap step 6A)

**Status:** Designed unattended on 2026-10-11. The owner asked for the rest of the factory roadmap to run unattended, with Jev for decisions and the Opus advisor for review. Every design decision below has its Jev score. The owner reviews this spec in the PR.

**Context:** The factory now covers spec → build → verify → release → operate → planning. After ops-intake (#77) and the hardening PR (#79), Jev judged:
- Q1 (the repo is a software factory) **partly 0.89**;
- Q3 (unattended mode works) **yes 0.78**.

The stage table in the README still lists **Idea validation: missing**. Roadmap step 6 is the business loop: idea validation, support/feedback, growth/monetise, and a spend governor. Jev picked idea validation first (0.70 over support triage 0.24; confidence 0.60, which is at the rule-6 edge, so this choice is logged as borderline). The advisor agreed: it opens the loop and hands off to the existing planner.

**Goal:** Before the factory builds an idea, the human states what must be true for the idea to be worth building, and how to find out cheaply. The skill turns a raw idea into a **validation brief**. Each riskiest assumption in the brief carries a test, a metric, a pass threshold, a kill threshold and a deadline. The human registers the brief before any result comes in. The tool records results and computes a verdict from the registered thresholds. When the human decides `go`, the skill hands the idea to spec-first-planning as an `idea-brief/v1` envelope.

The tool runs no command, opens no network connection and calls no model. It never runs an outward experiment.

## Decisions made unattended

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| D1 | **Build idea validation first** in step 6. | 0.70 (conf. 0.60, borderline) | support triage 0.24; spend governor 0.06; growth 0.00 |
| D2 | **The brief is Markdown** with fixed `##` sections and one structured line per assumption, linted like a spec-first-planning spec. Results live in a separate append-only ledger. | 0.95 | one JSON file edited only by the tool (0.05) |
| D3 | **The tool computes the verdict** from recorded results against the registered thresholds. The human decides; a decision that differs from the computed verdict is an override, and the handoff says so. | 0.94 | human declares (0.06); agent judges (0.00) |
| D4 | **`go` needs every critical assumption to pass.** Open non-critical assumptions go into the handoff as open risks. | 0.91 | all assumptions (0.09); majority (0.00) |
| D5 | **Desk research is weak evidence.** The agent's own research is kind `desk` and untrusted. It can kill an idea or pass a non-critical assumption. It cannot pass a critical assumption alone; that needs a human-run experiment. | 0.99 | full weight (0.00); none (0.01) |
| D6 | **A new `idea-brief/v1` envelope** goes to spec-first-planning, which gains one input path, the same shape as `intake-item/v1`. | 1.00 (lopsided) | plain text to paste (0.00) |
| D7 | **The skill never runs an outward experiment** (landing page, ads, outreach, posts), even with the user present. It writes the plan and the copy. The human runs it and reports the result. | P(run them) = 0.13 | in-session yes (rejected) |
| D8 | **The name is `idea-validation`.** | 0.97 | idea-gate, idea-validator |

The advisor added three design rules. They are not judgment calls, so Jev did not score them:
- **Pre-registration.** Thresholds are fixed before results arrive. A threshold change after a result makes that result stale. This stops the goalposts moving after the data is in.
- **No self-certification.** An experiment result names the human who reported it. The agent records only `desk` results under its own name. This is the same hole that the world-model-ledger `--by human` fix closed.
- **Outward work is never grantable.** Experiments are `spend` or `external_message` class (A8), so no autonomy grant covers them.

## 1. Components and data

### 1.1 The skill

`idea-validation/` follows the repo anatomy: `SKILL.md`, `README.md`, `references/`, `assets/`, `eval/`.

The tool is `assets/ideas.py`. It is stdlib-only, Python 3.10+, POSIX. Every command takes `--root R` before the command name.

| Command | Effect |
|---|---|
| `new <slug> --title TEXT` | Writes `ideas/<slug>.md` from the template. It refuses a slug that exists. |
| `lint <brief>` | Checks the brief grammar (1.2). It prints `LINT: ok` or one `PROBLEM:` line for each fault. |
| `register <brief> --by NAME` | Lints the brief, then snapshots it to `.skill-contract/ideas/<slug>/registered/<sha256>.md` and logs the registration. Results can be recorded only against a registered brief. |
| `record <slug> <A-id> --value N --kind experiment\|desk --source TEXT --observed YYYY-MM-DD --by NAME [--evidence-file F]` | Appends one result to the ledger, bound to the current registration. |
| `status <slug>` | Prints each assumption's status and the computed verdict. Read-only. |
| `decide <slug> --verdict go\|kill\|pivot --by NAME --reason TEXT` | Records the human's decision. |
| `handoff <slug> --by NAME` | Writes the `idea-brief/v1` envelope when the latest decision is `go`. |
| `list` | Lists every idea with its computed verdict and its decision. |

Storage:
- **The brief** is `ideas/<slug>.md`. The human chooses whether to commit it. It is the human's own idea, not customer data.
- **The ledger, the snapshots, the log and the envelopes** are under `.skill-contract/ideas/`, which is git-ignored, because results can quote interviews and sign-up data. `ideas.py` checks that the directory is ignored before its first write and stops with exit 2 when it is not.
- **Writes:** atomic replace for state files, append-only for `results.jsonl` and `log.jsonl`, and one repo-wide lock. The lock and atomic-write code are copies of ops-intake's, with a cross-reference comment.

### 1.2 Brief grammar

A brief is one Markdown file:

- **Title:** `# Idea: <title>`.
- **Required sections**, matched case-insensitively, in any order: `Problem`, `Target user`, `Value proposition`, `Riskiest assumptions`, `Experiments`, `Out of scope`.
- **Assumption lines** in `## Riskiest assumptions`:

  ```
  - A<n> [critical|normal] [problem|demand|solution|channel|pricing|feasibility]: <statement> (test: <how>; metric: <what is counted>; pass: <op> <number>; kill: <op> <number>; by: YYYY-MM-DD)
  ```

  `<op>` is `>=`, `>`, `<=` or `<`.
- **Lint rules:**
  - Ids run A1..An with no gaps or repeats.
  - At least one assumption is `critical`.
  - Every field is present and non-empty.
  - `pass` and `kill` point in opposite directions and do not overlap. For example, `pass: >= 30` with `kill: < 10` is valid, and `pass: >= 30` with `kill: < 40` is not. A value between them is **inconclusive**.
  - `by` is a real date.
  - A `NOTE:` (not a fault) when no critical assumption is `problem` or `demand`, because most ideas fail on demand.
- **`## Experiments`** is prose. Each experiment names its assumption ids. An outward experiment (anything that publishes, sends, spends or contacts a person) is marked `[outward]`. The lint fails an experiment that names no assumption id.

### 1.3 Results and statuses

A result is `{assumption, value, kind, source, observed, by, evidence, registration, recorded_at}`.
- `kind: experiment` needs a `by` that does not start with `agent:`.
- `kind: desk` is any research the agent did. Its `by` is `agent:<name>`, or a human's name when the human did the research.
- `evidence` is optional quoted text from `--evidence-file`, capped at 4 KB, and always untrusted.

**Stale results.** A result counts only while its assumption's `test`, `metric`, `pass`, `kill` and `by` fields are the same in the current registration as in the registration it was recorded under. A change to the assumption's statement alone does not make it stale. `status` shows stale results and does not count them.

**Assumption status**, computed from the latest counted result by `observed` date:

| Status | When |
|---|---|
| `open` | no counted result, and the deadline is today or later |
| `pass` | the value meets `pass`, the result was observed on or before the deadline, and for a critical assumption the result is `experiment` kind |
| `needs-experiment` | a critical assumption whose latest passing result is `desk` only |
| `fail` | the value meets `kill`, at any date and of any kind |
| `inconclusive` | the value is between `pass` and `kill` |
| `expired` | the deadline has passed and the status is not `pass` or `fail` |

A late passing result does not count as `pass`, because a deadline is part of the test.

**Computed verdict:**
1. `KILL` if any critical assumption is `fail`.
2. `GO` if every critical assumption is `pass`.
3. `STALLED` if any critical assumption is `expired`.
4. `PENDING` otherwise.

"Today" comes from the clock, or from `--today` for tests.

### 1.4 Decisions

`decide` records `{verdict, by, reason, computed, override, at}`. `override` is true when the decision differs from the computed verdict (`go` against anything but `GO`, or `kill` against `GO`). `pivot` closes the brief. The human then starts a new brief that names the old slug under `## Out of scope` or in prose. Version 1 does not link briefs.

The agent MUST NOT run `decide` or `handoff` for the human. Choosing to build or kill is the human's decision.

### 1.5 The handoff

`handoff` refuses unless the latest decision is `go` and the brief has not changed since the last registration. It writes the `idea-brief/v1` envelope to `.skill-contract/ideas/envelopes/`. The envelope's one subject is the registered snapshot. Its payload is:

```
{slug, title, problem, target_user, value_proposition, out_of_scope,
 computed_verdict, decision: {verdict, by, reason, override, at},
 assumptions: [{id, critical, category, statement, test, metric, pass, kill, by, status,
                results: [{value, kind, source, observed, by, evidence}]}],
 open_risks: [assumption ids that are not pass]}
```

It prints `NEXT: run spec-first-planning with <envelope path>`.

`docs/skill-contract/SPEC.md` registers the kind. The payload schema is `idea-validation/assets/schemas/idea-brief.v1.json`.

**spec-first-planning** gains one input path, the same shape as the intake path:
- `assets/idea_request.py <envelope>` prints a request skeleton:
  - `# Spec: idea <slug>`;
  - `## Idea` with the slug;
  - `## Problem` and `## Users` seeded from the brief;
  - `## Open risks` listing the open assumptions;
  - `## External evidence (untrusted)` holding every result's evidence in its own fence.
- `spec_lint.py` checks `## Idea` (one slug, kebab-case).
- The task plan payload gains an optional `ideas` list (schema change, optional, so existing plans stay valid).
- spec-first-planning's version goes up by one minor version.
- An override `go` is shown at the top of the skeleton, so the plan approval sees it.

## 2. Trust boundary

1. **Results are claimed, not proven.** The tool cannot see the experiment. A human's `by` is a claim, and a shell-capable agent can type any name. The SKILL says this in its honesty section. The `experiment` versus `desk` split and the human decision are the controls. The envelope marks every result `claimed`.
2. **External text is data.** Evidence, sources and desk research are untrusted. The tool shows them only as code spans or fenced blocks, with hidden characters written as `\uXXXX` (the `code()` copy from ops-intake). spec-first-planning keeps them in the untrusted block. Its copy tripwire and its verbatim `CHECK_COMMAND:` listing then apply as they do for intake.
3. **No outward action.** The tool sends nothing. The agent MUST NOT publish, send, post, buy ads or contact anyone for an experiment, because those actions are `spend` or `external_message` class and no grant covers them. The agent writes the copy and the plan, and the human runs them.
4. **No model in the tool.** Thresholds and verdicts are code. The agent MAY offer Jev to rank assumptions by risk only after the user agrees, and the tool never calls it.
5. **Customer data stays local.** The ledger and the envelopes are git-ignored. The agent tells the user before it commits any file that quotes evidence.

## 3. Flow

When the human says "I have an idea", "is this worth building?" or "validate this":

1. `new`, then the agent drafts the brief with the human: problem, user, value, and the 3–7 riskiest assumptions, with the critical ones first. `lint` until it is clean.
2. The agent MAY do desk research and record it as `desk` results (D5).
3. The human reviews the thresholds. The agent asks the human to confirm the brief, then runs `register --by <human>` with the name the human gives. Unattended mode asks this question upfront with the other decisions.
4. The human runs the experiments and reports each result. The agent records it with the human's name.
5. `status`, then the human runs `decide`. On `go`, `handoff` writes the envelope. spec-first-planning takes over.

Exit codes: 0 ok; 2 for refused input, a bad brief, an unignored state directory or a stale handoff; 3 when the lock is held. Machine lines: `LINT:`, `PROBLEM:`, `NOTE:`, `ASSUMPTION: <id> <status> <latest value>`, `VERDICT:`, `NEXT:`.

## 4. Testing, gates, acceptance

### 4.1 Tests

Offline, stdlib-only `unittest` suites in `assets/test_*.py`:
- **Lint:** every rule in 1.2 with a passing and a failing fixture, including overlapping thresholds and a missing assumption id in an experiment.
- **Statuses:** every row of the 1.3 table, a late pass, a stale result after a threshold edit, and a statement-only edit that keeps results.
- **Verdict:** each of the four outcomes, and that a desk-only pass on a critical assumption is never `GO`.
- **Self-certification:** `record --kind experiment --by agent:x` is refused.
- **Decisions:** an override is flagged both ways; `handoff` refuses without a `go`, and refuses after the brief changed.
- **No I/O:** a test patches `socket` and `subprocess` to raise, and every command still works.
- **Hostile evidence:** a heading, an `@mention`, a backtick fence and `curl … | sh` appear only as code or fenced text in `status` and in the envelope.
- **spec-first-planning:** `idea_request.py` accepts a valid envelope, refuses a wrong kind, and fences hostile evidence; `spec_lint` checks `## Idea`; a spec without `## Idea` gives byte-identical plan output.

### 4.2 Eval

`eval/run_eval.py` runs in under 60 s. The positive case: a fixture idea goes `new` → `register` → `record` → `decide go` → `handoff`, and spec-first-planning's `idea_request.py` accepts the envelope. Negatives:
- a desk-only critical pass does not give `GO`;
- a threshold edit after a result makes the result stale;
- an agent-named experiment result is refused;
- `handoff` without a `go` decision is refused;
- the tool opens no socket and runs no subprocess.

### 4.3 Gates

- `make gate` is green.
- idea-validation is a skill-contract adopter. It provides `idea-brief/v1`. spec-first-planning consumes it.
- BCP 14 register rows exist for the new SKILL.md and for the changed spec-first-planning lines.
- The README catalog lists the skill. The Software factory stage table moves Idea validation from missing to partly.
- A/B rows exist for each guard, under a new `SINCE_IDEA_VALIDATION` constant.

### 4.4 Acceptance criteria

| AC | Done when |
|---|---|
| AC1 | The brief lint catches every rule in 1.2, with fixtures. |
| AC2 | The verdict follows 1.3, and a critical assumption never passes on desk evidence alone. |
| AC3 | A threshold edit after a result makes that result stale. |
| AC4 | An experiment result named `agent:*` is refused; `decide` and `handoff` are documented as human-only. |
| AC5 | A `go` handoff produces an `idea-brief/v1` envelope that spec-first-planning turns into a spec skeleton with the evidence fenced as untrusted. |
| AC6 | The tool opens no socket and runs no subprocess. |
| AC7 | `make gate` and `make ab-validate` are green: 0 WORSE, 0 UNPROVEN. |
| AC8 | After merge, Jev re-judges Q1, and the score goes into the factory assessment. |

## 5. Out of scope for version 1

- running any experiment (D7);
- linking a pivot to its parent brief;
- market-size calculators or survey tooling;
- pulling results from analytics or a form tool (that belongs to the growth step);
- the spend governor (step 6, later; making `spend` grantable stays an owner decision).

## Errata (build)

The build changed the design in these places. The code, the tests and `idea-validation/references/brief-format.md` follow the build.

- **Mixed evidence.** §1.3 did not say how desk and experiment results combine. The rule: desk research can kill an assumption at any time. A desk pass or an in-between desk value does not rescue an experiment kill and does not undo an experiment pass. When the latest result is a desk result that does not meet `kill`, the latest result that is an experiment result or a desk kill decides; with no such result, the desk result decides. So a later desk pass or in-between desk value also does not undo an earlier desk kill (final review: the first build fell back to the latest experiment result and so erased a desk kill that came after an experiment pass). A later experiment result does decide over an earlier desk kill.
- **Two more handoff refusals.** §1.5 refused a handoff without a `go` and after a brief change. `handoff` also refuses when the `go` decision was made before the latest registration, and when the computed verdict changed after the `go` decision (new results, or a deadline that passed). The override flag describes the verdict the human saw, so a moved verdict needs a new decision. `handoff` also refuses a registered snapshot that is missing or does not match its sha256.
- **Pivot closes the brief.** After `pivot`, `record`, `register` and `decide` refuse. §1.4 said "closes"; the build makes it a hard stop.
- **No future `--observed`.** `record` refuses an `--observed` date later than today.
- **Truncated flag.** Evidence is cut at 4096 bytes on a UTF-8 boundary. The result and the payload carry `truncated: true`, the `RESULT:` line says so, and spec-first-planning prints "evidence truncated".
- **`ideas` has one item.** The task-plan schema's optional `ideas` list has `maxItems: 1`, because a spec has one `## Idea` slug.
- **Evidence-section pairing.** spec_lint accepts `## External evidence (untrusted)` only in a spec that has `## Intake` or `## Idea`. An idea spec without the evidence section fails.
- **Dropped critical assumptions (the goalpost flag).** The design fixed thresholds by pre-registration, but a human could still demote a failed critical assumption to `normal`, or delete it, and register again. The owner-proxy decision went to Jev: **flag it** (0.97), not refuse the handoff (0.01) and not mark its results stale (0.02). The tool tracks each id that was critical in an earlier registration and is normal or deleted now. `status`, `decide` and `handoff` print `NOTE: dropped-critical` lines, and the payload always carries `dropped_critical: [{id, statement, last_critical_registration, had_results, reason}]`. spec-first-planning requires the field and prints a `> **Dropped critical assumptions:**` line under the H1. The flag does not change the verdict. Tracking is by id. Final review added a `reason` to each entry (`demoted`, `deleted` or `replaced`). `replaced` covers an id that is still critical, but whose statement and all of test, metric, pass, kill and by changed from the latest earlier registration where it was critical. Without it, swapping a failed critical A1 with a normal A3 made the kill result stale and flagged nothing. A partial edit, or a deletion followed by renumbering that keeps a field, still reads as an edit, and a replacement registered again with a small edit is compared with the replacement and loses the flag. `had_results` is true only for a result recorded under a registration where the id was critical (Jev 0.76 at confidence 0.52, below the 0.6 bar, so this was decided by reasoning: a looser rule gives false positives after renumbering).
- **Unparsed assumption lines.** A line under `## Riskiest assumptions` that has `A<n> [` but does not parse as an assumption is a lint fault. Before the final review, `+ A4 [critical] …`, `1. A4 [critical] …` and a bare `A4 [critical] …` were dropped with no message, so the assumption was missing from the registration and from the verdict. Bullets now also start with `+`, `1.` or `1)`, in `## Riskiest assumptions` and in `## Experiments`.
- **Payload self-check.** spec-first-planning's `idea_request.py` exits 2 when the payload contradicts itself: `decision.override` must equal "`computed_verdict` is not `GO`", `open_risks` must be exactly the ids whose status is not `pass`, and `computed_verdict` must follow from the critical statuses (KILL if one failed, else GO if every one passed, else STALLED if one expired, else PENDING). A hand-edited envelope can no longer hide a KILL behind `override: false`.
- **Negated ignores.** `ideas.py` refuses (exit 2, no write) a `.skill-contract/ideas/.gitignore` with a line that starts with `!`. Such a line can un-ignore results that quote interviews. The tool does not overwrite the file, because someone added the negation on purpose.
- **Reserved slugs.** `envelopes` and `specs` are directory names under `.skill-contract/ideas/`, so every command that takes a slug refuses them, and `list` skips them.
