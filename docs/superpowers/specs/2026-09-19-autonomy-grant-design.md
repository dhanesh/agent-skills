# Autonomy grant and manifold-style planning (factory roadmap step 3A)

**Status:** The owner approved every section in the brainstorm on 2026-09-19. The implementation plan comes next.
**Context:** Factory roadmap step 3, sub-project A. Sub-project B (task `depends_on` and waves) and step 4 (the conductor) are separate specs.
**Goal:** The owner's acceptance test Q3 (see `docs/factory/2026-09-19-assessment.md` §6). When the user asks for unattended development:
- the skills ask every question that needs a human decision upfront, during requirements and design;
- the user's answers and approval become an `autonomy-grant/v1`;
- skills that stop to ask today proceed within that grant.

The user keeps full control. Unattended mode is always opt-in, and the grant can be revoked with one command.
**Decision support:** Jev (`jev-1.13.0`) was consulted on each choice below. The owner decided every one.

## Owner decisions

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| A1 | **Split step 3.** A comes first: the grant, decision closure and the planning method. B comes next: `depends_on` and waves. | 0.80 | One spec for both; B first |
| A2 | **Provenance: honest labelling plus optional signing.** A grant is labelled as accepted by the user, with the threat model stated. The user MAY sign it with `ssh-keygen -Y sign`. The checker reports `UNSIGNED`, `SIGNED` or `SIGNED_HW`. | 0.91 | Signing always required; honest label only; a user-terminal accept command |
| A3 | **Reach.** spec-first-planning's handoff and the existing human gates in crafting-self-prompting-loops, verifier-installer and test-safety-net honour the grant. | 0.89 | Only spec-first-planning; also build the log, budgets and stop rules now |
| A4 | **Architecture.** The grant is a skill-contract envelope kind, checked by the reference checker's `check-grant`. The action-class table is normative in SPEC.md. | — | A separate grant skill; a plain policy file |
| A5 | **The planning method follows manifold:** constrain → tension → anchor (backward reasoning to required truths) → choose, iterated until it converges. | — | — |
| A6 | **Loop depth follows the mode.** Unattended runs the full loop. Attended runs a light pass, and the user can switch to the full loop, or to unattended, at any point. | 0.96 | The full loop always (owner's first choice, revised after spec review); attended mode unchanged |
| A7 | **Checker-held floors that no grant can lower** (added 2026-09-19, after Task 1 showed that `require_signature` inside a grant cannot stop a forged grant that omits it): (a) `merge`, `deploy`, `spend`, `external_message` and `delete` are covered only when the grant is `SIGNED_HW`; (b) a grant may live at most 7 days, so `expires_at − generatedAtTime > 7d` is INVALID and `expires_at > now + 7d` is ASK; (c) a grant never covers an action on the repo's default branch (from `origin/HEAD`, else `main`/`master`). Unsigned grants still cover `read_only`, `local_reversible`, `push_branch` and `open_pr`. | 0.98 | The same floors but any signature for the irreversible classes; no floors, documented only |

## 1. The grant: `autonomy-grant/v1`

**predicateType:** `https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1`.

**Subjects:** the spec file and the task-plan envelope it was approved for, each pinned by `sha256` (C5). If either changes, the grant is stale.

**Payload:**

| Field | Contents |
|---|---|
| `scope` | `repo` (".") and `branch_pattern` (a glob, e.g. `factory/*`) |
| `decisions[]` | `{id: "D<n>", question, answer, source}` |
| `defaults[]` | `{when, rule}`, for low-stakes judgement calls |
| `gate_policy` | a map from action class (§2) to `auto`, `grant` or `ask` |
| `require_signature` | a map from action class to `SIGNED` or `SIGNED_HW` (optional) |
| `budget` | `wall_clock_min`, `max_tokens`, `max_usd`, `max_repairs_per_task`. Recorded now; step 4 enforces it. |
| `stop_on[]` | Recorded now; step 4 enforces it. |
| `expires_at` | an RFC 3339 timestamp |
| `system_one` | `{allowed, decision_kinds[], data_sent}` |
| `revoked` | a boolean, `false` on a fresh grant |

**Assertion:** exactly one, `{test: "grant-accepted", assertedBy: {"human": "<name>"}, result: {outcome: "passed"}}`. A grant whose acceptance is not attributed to a human is INVALID.

**Signature:** optional. It lives beside the envelope at `<id>.json.sig`. It is created with `ssh-keygen -Y sign -n skill-contract-grant`, which the user runs, and verified against an allowed-signers file:
- `$SKILL_CONTRACT_ALLOWED_SIGNERS`, else
- git's `gpg.ssh.allowedSignersFile`, else
- `~/.config/skill-contract/allowed_signers`.

The signature level:
- `SIGNED_HW` when the verifying key type starts with `sk-` (a FIDO key that needs a physical touch);
- `SIGNED` for any other key that verifies;
- `UNSIGNED` otherwise, including when `ssh-keygen` is not installed.

**Revocation:** `revoke-grant <id>` writes a revision (`wasRevisionOf: <id>`) with `revoked: true`. The user runs it; no grant ever covers it. A revocation only tightens, so it needs no human attribution: its `assertions` list is empty. Anyone may revoke.

## 2. Action classes (normative, in SPEC.md)

| Class | Examples | Reversibility | Most permissive gate |
|---|---|---|---|
| `read_only` | read files, run read-only checks | none needed | `auto` |
| `local_reversible` | edit the working tree, commit on a local branch, hand an envelope to a local skill | local | `auto` |
| `push_branch` | push a non-default branch | remote, reversible | `grant` |
| `open_pr` | open or update a pull request | remote, reversible | `grant` |
| `merge` | merge to a default or protected branch | irreversible or externally visible | `grant`, only if named explicitly |
| `deploy` | release, publish, deploy | irreversible or externally visible | `grant`, only if named explicitly |
| `spend` | any paid API or resource beyond the budget | irreversible | `grant`, only if named explicitly |
| `external_message` | email, chat, issue comments to others | externally visible | `grant`, only if named explicitly |
| `delete` | delete branches, files outside the working tree, data | irreversible | `grant`, only if named explicitly |

- A class absent from `gate_policy` is `ask`.
- `auto` on any class other than `read_only` and `local_reversible` makes the grant INVALID (the table's "most permissive gate" column).

## 3. `contract_check.py check-grant`

```
contract_check.py check-grant [<grant.json>] --root <repo> --action <class>
```

- **Without a path**, it uses the newest grant under `.skill-contract/envelopes/` that has not been superseded by a revision.
- **It runs these checks in order:**
  1. envelope validity (C3–C6, C9);
  2. human attribution, skipped for a revoked revision;
  3. the gate-policy floors;
  4. not revoked;
  5. not superseded, meaning no revision names this grant in `wasRevisionOf`. This stops an explicit path to an old grant from bypassing a revocation;
  6. not expired;
  7. subjects not stale;
  8. the current git branch matches `branch_pattern` (the check is skipped outside git);
  9. the class's gate is `auto` or `grant`;
  10. the required signature level is met: the higher of the grant's `require_signature` and the checker's floor, which is `SIGNED_HW` for `merge`, `deploy`, `spend`, `external_message` and `delete`.

  **Floors (A7), which no grant can lower:**
  - a grant living more than 7 days (`expires_at − generatedAtTime`) is INVALID;
  - a grant whose `expires_at` is more than 7 days after now is ASK with `reason=lifetime`;
  - a grant is ASK with `reason=default-branch` when the current branch is the repo's default branch (`origin/HEAD`, else `main` or `master`);
  - the signature floor above gives ASK with `reason=signature`.
- **Outputs:**
  - `GRANT: COVERED id=… class=… gate=auto|grant signed=UNSIGNED|SIGNED|SIGNED_HW`, exit 0;
  - `GRANT: ASK id=… reason=<first failing check>`, exit 3;
  - `GRANT: INVALID …`, exit 2;
  - `GRANT: NONE`, exit 3.
- **A caller proceeds only on exit 0.**

## 4. spec-first-planning 2.0.0

**The planning loop.** The method is manifold's: constrain → tension → anchor → choose. Its terms are used verbatim so the two map one to one.

**How deep the loop goes depends on the mode:**
- **Unattended:** the full loop MUST run. All four steps run, and iteration continues to convergence, because no human will be present during implementation.
- **Attended:** a light pass by default. Constrain (typed constraints, no pre-mortem) and Anchor (required truths) run once, and the spec carries the Constraints and Required truths sections. Tension and Choose run only when two constraints visibly conflict or there is more than one real option.
- **Changing course:** in attended mode the skill says which depth it is using, and offers two switches at each checkpoint (after the spec draft and after the plan): *go deeper* (run the full loop) and *go unattended* (run the full loop plus the decision sweep, then the grant). The user can take either switch at any point. Nothing escalates without the user asking.

The steps:
1. **Constrain.**
   - Typed constraints: `invariant`, `goal` or `boundary`.
   - Categories with ID prefixes: B business, T technical, U UX, S security, O operational.
   - A pre-mortem asks for three failure stories. Each one becomes a constraint or an accepted risk, recorded as a Decision.
2. **Tension.**
   - Check constraint pairs for `trade_off`, `resource_tension` and `hidden_dependency`.
   - Resolve each one by Prioritize, Partition, Transform, Accept or Invalidate, with a propagation check (TIGHTENED / LOOSENED / VIOLATED).
   - A resolution that needs the user becomes a Decision.
3. **Anchor.** Work backwards from the outcome, asking "what must be TRUE?" recursively. Each required truth RT-n carries:
   - a `parent` (the OUTCOME or another RT);
   - `maps_to` (constraint ids);
   - `confidence` in [0, 1];
   - a status: `SATISFIED`, `PARTIAL`, `NOT_SATISFIED` or `SPECIFICATION_READY`;
   - at least one runnable check.
4. **Choose.**
   - Give 2–4 options. Each lists the RTs it satisfies, its gaps, a complexity (Low/Medium/High) and a reversibility (`TWO_WAY`, `REVERSIBLE_WITH_COST` or `ONE_WAY`).
   - **The pragmatic rule:** among the options that satisfy every invariant and every RT, pick the lowest complexity, then the most reversible. A tie becomes a Decision.
   - After choosing, validate that the choice reopens no resolved tension.

**Convergence** applies to the full loop, so to unattended mode or when the user asks for it. The spec has converged when:
- every tension is resolved, or accepted as a Decision;
- every RT is `SPECIFICATION_READY` or `SATISFIED`;
- every constraint maps to at least one RT, and every RT maps to at least one requirement with a runnable acceptance criterion;
- the Open questions list is empty;
- a recommended option with a rationale exists.

Each iteration is recorded in an Iterations section. The cap is 5; after that the skill stops and asks the user.

**Unattended mode, opt-in.**
- When the user asks for it, step 1 adds the decision sweep from `references/unattended.md`: dependencies, public API and schema changes, new services, style and naming defaults, a gate per action class, expiry and budget, and System One use. All of it is asked in one batched round.
- The spec gains a Decisions section (D1..Dn).
- After `TASKS_RESULT: PASS` and the plan envelope, the skill shows a grant summary and MUST wait for the user's explicit yes before running `assets/write_grant.py`.
- It then offers the signing command, for the user to run.

**Tooling (stdlib only):**
- `spec_lint.py` checks the light sections on every spec: typed Constraints and Required truths, and their traceability. `--converged` adds the full-loop rules: Tensions, Solution options, Iterations and convergence. `--unattended` implies `--converged` and also fails on any Open question, or on a Decision without an answer.
- `spec_to_tasks.py` carries `decisions`, `constraints` and `required_truths` as optional task-plan payload fields. `task-plan/v1` stays v1, because the change is additive.
- `write_grant.py` builds the grant from the spec, the plan envelope and the user's answers.
- The template and `references/spec-format.md` are updated.

**Handoff.** Step 6 runs `check-grant --action local_reversible` first.
- On exit 0, it hands off and names the grant id and class in its report.
- Otherwise, it proposes and waits, as today.

**Honesty.** The linter checks structure and traceability, not the quality of the reasoning. The SKILL.md says so, as manifold's README does.

## 5. Gated skills adopt the contract

verifier-installer and test-safety-net become adopters. Each:
- declares `metadata.skill-contract: "1"`;
- adds a `## Contract` block that consumes `autonomy-grant/v1`;
- vendors `contract_check.py` byte-identical.

crafting-self-prompting-loops, already an adopter, adds `autonomy-grant/v1` to what it consumes. spec-first-planning adds it to what it provides. That makes 4 adopters, up from 2.

**Each gate gets the same rule:** the skill MUST NOT write or hand off before the user confirms, unless `check-grant --root <repo> --action <class>` exits 0. Then it MAY proceed, and MUST name the grant id and class in its report.

| Skill | Gate | Class |
|---|---|---|
| spec-first-planning | step 6, the handoff | `local_reversible` |
| crafting-self-prompting-loops | accepting a handoff | `local_reversible` |
| crafting-self-prompting-loops | LSC-8 | the action's own class |
| verifier-installer | step 2, write configs and the workflow | `local_reversible` |
| test-safety-net | step 3, write test files | `local_reversible` |

## 6. SPEC.md changes

- **Commandment 10:** "…unless a valid `autonomy-grant/v1` covers the action's class (`check-grant` exits 0)."
- **The §2 action-class table** becomes normative.
- **Human attribution:** a grant MUST be attributed to a human, and a skill-attributed grant is invalid.
- **A non-normative threat-model note:**
  - An unsigned or software-signed grant can be forged by an agent with a shell on the same machine.
  - Only `SIGNED_HW` shows that a physical touch happened.
  - The same limit applies to transcript roles.

## 7. Evidence and acceptance criteria

- **AC1. Reference checker vectors.** Valid gives COVERED. Each of these gives ASK or INVALID as §3 specifies:
  - expired;
  - stale;
  - branch mismatch;
  - unlisted class;
  - `merge: auto`, which is INVALID;
  - skill-attributed, which is INVALID;
  - revoked;
  - needs `SIGNED` but unsigned.

  `make contract` passes.
- **AC2. Signing, end to end.** An integration test with a throwaway ed25519 key yields `SIGNED`. It skips when `ssh-keygen` is absent. The `sk-` classifier is unit-tested, and the docs state that `SIGNED_HW` cannot be tested without hardware.
- **AC3. spec-first-planning tests.** Unit tests cover every new lint rule, `--converged` and `--unattended`. The eval has negatives for:
  - an attended spec missing Constraints or Required truths (light-pass rules);
  - an unattended spec with an Open question;
  - a non-converged spec;
  - an RT without a check;
  - a constraint that maps to no RT.
- **AC4. The end-to-end run in `make contract`:**
  1. spec → plan → grant, and the handoff proceeds under the grant;
  2. `revoke-grant`, and the same handoff check returns ASK;
  3. a skill-attributed grant is INVALID.
- **AC5. The four gated skills.** Each skill's eval checks its gate text: it proceeds only on `check-grant` exit 0 and names the grant. `make gate` is green with 4 adopters, and `contract-vendor` shows no drift.
- **AC6. A/B rows under `SINCE_AUTONOMY_GRANT`:**
  - skills whose human gate honours a grant, 0 → 4, IMPROVED;
  - contract adopters, 2 → 4, IMPROVED;
  - forged or skill-attributed grants accepted, 0 → 0, as a guard.
- **AC7. Versions and BCP 14.**
  - spec-first-planning goes to 2.0.0, with an upgrade note for existing specs.
  - The other four skills get a minor bump.
  - Every new hard rule carries a BCP 14 keyword and a classification-doc row.
  - PP-7 passes, and PP-5 is not newly advisory.
- **AC8. Jev re-judges Q3** of the acceptance test after merge. The result is recorded in the assessment doc.

## 8. Risks

- **Grant forgery.** An agent can write an UNSIGNED grant.
  - Mitigations: honest labelling, the per-class `require_signature`, `SIGNED_HW` for high-risk classes, and floors that keep irreversible classes at `ask` unless named explicitly.
  - Residual: unsigned grants are only as trustworthy as the machine.
- **The light pass may miss a conflict.** An attended spec that skips Tension can overlook one. Mitigations: the skill names its depth, offers *go deeper* at each checkpoint, and unattended mode always runs the full loop.
- **Breaking spec format.** Existing specs fail lint. Mitigation: the 2.0.0 bump and the upgrade note.
- **Vendored checker drift across 4 adopters.** Mitigation: `contract-vendor` plus the byte-identity gate.
- **Model judgement quality isn't checked.** The linter checks structure only. Mitigations: the model trial protocol (docs) and the review.

## Out of scope

- Sub-project B: `depends_on` and waves.
- Step 4: the conductor, the executor and verifier, the durable autonomy log, and enforcing budgets and stop rules.
- Reading or writing manifold's `.manifold/` files.
- Any use of Jev inside the skills beyond the existing C10 opt-in.
