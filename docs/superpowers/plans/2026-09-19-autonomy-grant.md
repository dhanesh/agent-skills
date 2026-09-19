# Autonomy Grant and Manifold-Style Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user asks for unattended development, spec-first-planning runs manifold's planning loop to convergence, asks every human decision in one round, and writes an `autonomy-grant/v1` envelope after the user's yes. Four skills' human gates then proceed only when `contract_check.py check-grant` says the grant covers the action.

**Architecture:**
- The grant is a skill-contract envelope kind. The reference checker (`docs/skill-contract/reference/contract_check.py`) gains `check-grant` and `revoke-grant`, and is vendored byte-identical into every adopter.
- spec-first-planning's `spec_lint.py` gains three levels of rules: light, which always apply; `--converged`; and `--unattended`. A new `write_grant.py` builds the grant.
- The gated skills call the vendored checker and never parse a grant themselves.

**Tech Stack:** Python 3.10+ stdlib only, POSIX sh gates, `make`. No signing (owner decision A8).

**Spec:** `docs/superpowers/specs/2026-09-19-autonomy-grant-design.md` (read §1–§7 before any task).

## Global Constraints

- Branch `feat/autonomy-grant`. No push, no amend, no subagents inside a task.
- Tooling is **Python stdlib only**: no pip, no network.
- Tests are `assets/test_*.py` (or `docs/skill-contract/reference/test_*.py`) using unittest.
- Evals are `eval/run_eval.py`: model-free, offline, and they MUST include NEGATIVE fixtures.
- Never weaken an existing check. If a fixture must change, keep the check at least as strict and say why in the commit.
- **TDD:** write the failing test first, see it fail, then implement.
- **The reference checker is the single source.** After any change to `docs/skill-contract/reference/contract_check.py`, run `make contract-vendor`, so every adopter's `assets/contract_check.py` stays byte-identical. The `skill-contract` gate fails on drift.
- **Grant lifetime (owner decision A7, which no grant can lower):**
  - A grant lives at most 7 days: `expires_at − generatedAtTime` must be ≤ 7 days, and `expires_at` ≤ now + 7 days.
  - Every test, eval or A/B fixture that builds a grant at run time MUST use these module-level helpers (stdlib):

    ```python
    from datetime import datetime, timedelta, timezone
    def _now_z(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    def _in_one_day(): return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ```

    They set `generatedAtTime = _now_z()` and `expires_at = _in_one_day()`. `write_grant.py` sets `generatedAtTime` itself.
- **More A7 floors:**
  - **A8:** `merge`, `deploy`, `spend`, `external_message` and `delete` are never grantable; any gate other than `ask` on them makes a grant INVALID. There is no signing: no `require_signature`, no `.sig` and no signature levels;
  - a grant never covers the repo's default branch. Test repos made with `git init -b main` therefore get `reason=default-branch`, so use a non-default branch such as `git checkout -q -b factory/x` whenever a covered result is expected inside git.
- **Kind URI (verbatim):** `https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1`.
- **Action classes (verbatim, in this order):** `read_only`, `local_reversible`, `push_branch`, `open_pr`, `merge`, `deploy`, `spend`, `external_message`, `delete`.
  - `auto` is allowed only on `read_only` and `local_reversible`.
  - `auto` on any other class makes a grant INVALID.
  - A class absent from `gate_policy` is `ask`.
- **`check-grant` exit codes:** 0 `COVERED`; 3 `ASK` or `NONE`; 2 `INVALID`; 1 usage error.
- **SKILL.md files keep their one-line BCP 14 declaration** and pass PP-7.
  - Only MUST, MUST NOT, SHOULD, SHOULD NOT and MAY appear in capitals.
  - Keywords go on hard rules only.
  - Every new keyworded hard-rule sentence gets a row in `docs/rfc2119/2026-09-19-classification.md` for that skill, and the header counts are updated (one per candidate, at its final level).
  - PP-5 MUST NOT become newly advisory for any skill. Check with `sh scripts/gates/prompting-playbook.sh <skill> | grep PP-5` before and after.
- **Versions (final):**
  - spec-first-planning `2.0.0`, including `SKILL_VERSION` in `assets/spec_to_tasks.py`;
  - verifier-installer `1.2.0`;
  - test-safety-net `1.4.0`;
  - crafting-self-prompting-loops `1.4.0`.
- `make gate-skill SKILL=<dir>` must be all PASS before each commit that touches a skill, and `make contract` must pass before each commit that touches the reference.
- Every commit message ends with exactly:
```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X
```

## File map

| File | Responsibility | Task |
|---|---|---|
| `docs/skill-contract/reference/contract_check.py` | grant validation, `check_grant`, `latest_grant`, `revoke_grant`, `signature_level`, CLI | 1, 2 |
| `docs/skill-contract/reference/build_vectors.py` | `grant_cases()`, which emits `vectors/c10/{valid,invalid}` | 1 |
| `docs/skill-contract/reference/test_contract_check.py` | the grant vector runner, CLI and unit tests, signature tests | 1, 2 |
| `docs/skill-contract/reference/test_e2e.py` | spec → plan → grant → covered → revoke → ASK | 8 |
| `docs/skill-contract/SPEC.md` | commandment 10's "unless", the action-class table, human attribution, threat note | 1 |
| `spec-first-planning/assets/spec_lint.py` | parse the new sections; light, `--converged` and `--unattended` rules | 3, 4 |
| `spec-first-planning/assets/spec_to_tasks.py`, `assets/schemas/task-plan.v1.json` | optional `constraints`, `required_truths` and `decisions` payload fields | 5 |
| `spec-first-planning/assets/write_grant.py` (new) | builds and writes the grant envelope | 5 |
| `spec-first-planning/SKILL.md`, `references/{spec-format,spec-template,unattended}.md`, `eval/run_eval.py` | the method, the modes, the handoff under a grant | 6 |
| `verifier-installer/`, `test-safety-net/`, `crafting-self-prompting-loops/` (SKILL.md, assets/contract_check.py, eval) | adopt the contract; the gates call `check-grant` | 7 |
| `scripts/ab-validate.py`, `README.md`, `docs/factory/2026-09-19-assessment.md` | A/B rows and an honest status | 9 |

---

### Task 1: Reference checker — grant validation, `check-grant`, `revoke-grant`, C10 vectors, SPEC

**Files:**
- Modify: `docs/skill-contract/reference/contract_check.py`: add the constants after `BARE_PYTHON_RE`, a new section before `# ── CLI`, the parser entries and the `main` branches, and the docstring usage lines.
- Modify: `docs/skill-contract/reference/build_vectors.py`: add `grant_cases()` and include it in `cases()`.
- Modify: `docs/skill-contract/reference/test_contract_check.py`: add the `grant` runner to `RUNNERS`, add `"c10"` to `required_commandments()`, and add a `GrantTests` class.
- Modify: `docs/skill-contract/SPEC.md`.
- Regenerate: `docs/skill-contract/vectors/` with `python3 build_vectors.py`.
- Then run `make contract-vendor`, which updates spec-first-planning's and crafting-self-prompting-loops' `assets/contract_check.py`.

**Interfaces:**
- Produces, in `contract_check.py`:
  - `GRANT_KIND: str`
  - `ACTION_CLASSES: tuple[str, ...]`
  - `LOCAL_CLASSES: frozenset` = {"read_only", "local_reversible"}
  - `SIG_LEVELS: tuple` = ("UNSIGNED", "SIGNED", "SIGNED_HW")
  - `grant_violations(st: dict) -> list[str]`: grant-specific problems; [] means valid.
  - `latest_grant(root: str) -> str | None`: the path of the newest head grant.
  - `is_superseded(root: str, grant_id: str) -> bool`
  - `current_branch(root: str) -> str | None`
  - `signature_level(path: str, env: dict | None = None) -> str`: in Task 1 it returns `"UNSIGNED"` unless `<path>.sig` exists; Task 2 fills in the rest.
  - `check_grant(root: str, action: str, path: str | None = None, now: datetime | None = None, branch: str | None = None, env: dict | None = None) -> dict` with the keys `status` ("COVERED"/"ASK"/"INVALID"/"NONE"), `id`, `reason`, `gate`, `signed`, `path` and `violations` (list[str]).
  - `revoke_grant(root: str, grant_id: str | None = None, now: datetime | None = None) -> str`: the path of the revocation envelope.
  - CLI: `check-grant [FILE] --root DIR --action CLASS [--json]` and `revoke-grant [ID] --root DIR`.

- [ ] **Step 1: Write the vector cases (the failing tests).** Add this to `build_vectors.py`, after `claims_cases()`:

```python
GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"
GRANT_ID = "autonomy-grant-v1-20260919T120000Z-a1b2c3"
PLAN_TEXT = '{"plan": 1}\n'
NOW = "2026-09-19T13:00:00Z"


def grant(policy=None, attributed=None, revoked=False, expires="2026-09-20T12:00:00Z",
          require=None, branch_pattern="factory/*", spec_text=SPEC, gid=GRANT_ID, rev=None,
          assertions=None):
    a = {"test": "grant-accepted", "assertedBy": attributed or {"human": "Dana"},
         "result": {"outcome": "passed"},
         "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                     "--unattended", "docs/spec.md"],
         "subject": [{"name": "docs/spec.md", "digest": {"sha256": sha(spec_text)}}]}
    payload = {"scope": {"repo": ".", "branch_pattern": branch_pattern},
               "decisions": [{"id": "D1", "question": "Add deps?", "answer": "no",
                              "source": "sweep"}],
               "defaults": [], "gate_policy": policy if policy is not None else
               {"read_only": "auto", "local_reversible": "grant"},
               "require_signature": require or {}, "budget": {"wall_clock_min": 60},
               "stop_on": ["new_human_decision"], "expires_at": expires,
               "system_one": {"allowed": False}, "revoked": revoked}
    return {"_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "docs/spec.md", "digest": {"sha256": sha(spec_text)}},
                        {"name": "plan.json", "digest": {"sha256": sha(PLAN_TEXT)}}],
            "predicateType": GRANT_KIND,
            "predicate": {"skillContract": "1", "id": gid,
                          "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
                          "generatedAtTime": "2026-09-19T12:00:00Z", "wasRevisionOf": rev,
                          "payload": payload,
                          "assertions": [a] if assertions is None else assertions}}


def grant_vector(st, action, status, reason=None, files=None, branch="factory/x", others=()):
    inp = {"type": "grant", "grant": st, "action": action, "now": NOW, "branch": branch,
           "files": files if files is not None else {"docs/spec.md": SPEC, "plan.json": PLAN_TEXT},
           "others": list(others)}
    exp = {"status": status}
    if reason is not None:
        exp["reason"] = reason
    return {"input": inp, "expect": exp}


def grant_cases():
    g = grant()
    return [
        ("c10", "valid", "covered-grant", grant_vector(g, "local_reversible", "COVERED")),
        ("c10", "valid", "covered-auto", grant_vector(g, "read_only", "COVERED")),
        ("c10", "valid", "unlisted-class-asks", grant_vector(g, "push_branch", "ASK", "gate-ask")),
        ("c10", "valid", "expired-asks",
         grant_vector(grant(expires="2026-09-19T12:30:00Z"), "local_reversible", "ASK", "expired")),
        ("c10", "valid", "stale-asks",
         grant_vector(g, "local_reversible", "ASK", "stale",
                      files={"docs/spec.md": SPEC_EDITED, "plan.json": PLAN_TEXT})),
        ("c10", "valid", "branch-mismatch-asks",
         grant_vector(g, "local_reversible", "ASK", "branch", branch="main")),
        ("c10", "valid", "revoked-asks",
         grant_vector(grant(revoked=True, assertions=[]), "local_reversible", "ASK", "revoked")),
        ("c10", "valid", "superseded-asks",
         grant_vector(g, "local_reversible", "ASK", "superseded",
                      others=[grant(revoked=True, assertions=[], rev=GRANT_ID,
                                    gid="autonomy-grant-v1-20260919T121000Z-d4e5f6")])),
        ("c10", "valid", "signature-required-asks",
         grant_vector(grant(require={"local_reversible": "SIGNED"}), "local_reversible",
                      "ASK", "signature")),
        ("c10", "invalid", "merge-auto",
         grant_vector(grant(policy={"merge": "auto"}), "merge", "INVALID")),
        ("c10", "invalid", "push-auto",
         grant_vector(grant(policy={"push_branch": "auto"}), "push_branch", "INVALID")),
        ("c10", "invalid", "skill-attributed",
         grant_vector(grant(attributed={"skill": "spec-first-planning"}), "local_reversible",
                      "INVALID")),
        ("c10", "invalid", "no-acceptance",
         grant_vector(grant(assertions=[]), "local_reversible", "INVALID")),
        ("c10", "invalid", "unknown-gate",
         grant_vector(grant(policy={"local_reversible": "yes"}), "local_reversible", "INVALID")),
    ]
```

  Change `cases()` to `return skill_cases() + envelope_cases() + discovery_cases() + claims_cases() + grant_cases()`.

  Add the runner to `test_contract_check.py`, next to `run_discovery_vector`, and register it:

```python
def run_grant_vector(inp, tmp):
    root = os.path.join(tmp, "root")
    for rel, text in inp["files"].items():
        _write(os.path.join(root, *rel.split("/")), text)
    edir = cc.envelope_dir(root)
    for st in [inp["grant"]] + inp.get("others", []):
        _write(os.path.join(edir, st["predicate"]["id"] + ".json"), json.dumps(st))
    path = os.path.join(edir, inp["grant"]["predicate"]["id"] + ".json")
    now = datetime.strptime(inp["now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    rep = cc.check_grant(root, inp["action"], path=path, now=now, branch=inp["branch"], env={})
    return {"status": rep["status"], "reason": rep["reason"]}


RUNNERS = {"skill": run_skill_vector, "envelope": run_envelope_vector,
           "discovery": run_discovery_vector, "grant": run_grant_vector}
```

  Also add `from datetime import datetime, timezone` to the test imports, and `"c10"` to `required_commandments()`.

  Add a `GrantTests` class for the pieces vectors can't reach:

```python
class GrantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc-grant-")
        _write(os.path.join(self.tmp, "docs", "spec.md"), build_vectors.SPEC)
        _write(os.path.join(self.tmp, "plan.json"), build_vectors.PLAN_TEXT)
        self.now = datetime(2026, 9, 19, 13, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, st):
        path = os.path.join(cc.envelope_dir(self.tmp), st["predicate"]["id"] + ".json")
        _write(path, json.dumps(st))
        return path

    def test_no_grant_is_none(self):
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch="factory/x", env={})
        self.assertEqual(rep["status"], "NONE")

    def test_latest_head_is_used_without_a_path(self):
        self.put(build_vectors.grant())
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch="factory/x", env={})
        self.assertEqual(rep["status"], "COVERED")

    def test_revoke_makes_the_next_check_ask(self):
        self.put(build_vectors.grant())
        out = cc.revoke_grant(self.tmp, now=self.now)
        self.assertTrue(os.path.isfile(out))
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch="factory/x", env={})
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"))

    def test_explicit_path_to_a_revoked_grant_still_asks(self):
        p = self.put(build_vectors.grant())
        cc.revoke_grant(self.tmp, now=self.now)
        rep = cc.check_grant(self.tmp, "local_reversible", path=p, now=self.now,
                             branch="factory/x", env={})
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "superseded"))

    def test_unknown_action_is_a_usage_error(self):
        rc = cc.main(["check-grant", "--root", self.tmp, "--action", "launch"])
        self.assertEqual(rc, 1)

    def test_cli_exit_codes(self):
        self.put(build_vectors.grant(expires="2999-01-01T00:00:00Z"))
        codes = {}
        for action in ("local_reversible", "merge"):
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                codes[action] = (cc.main(["check-grant", "--root", self.tmp, "--action", action]),
                                 buf.getvalue())
        self.assertEqual(codes["local_reversible"][0], 0)
        self.assertIn("GRANT: COVERED", codes["local_reversible"][1])
        self.assertEqual(codes["merge"][0], 3)
        self.assertIn("GRANT: ASK", codes["merge"][1])
```

  The CLI test runs outside git and with the real clock. `current_branch` returns None outside git, which skips the branch check. The fixture `expires_at` is 2026-09-20, so if the test runs after that date, expiry breaks it. Make `grant()` in the CLI test pass `expires="2999-01-01T00:00:00Z"`: use `self.put(build_vectors.grant(expires="2999-01-01T00:00:00Z"))` in `test_cli_exit_codes`. Add `import contextlib, io, shutil` if they aren't imported yet.

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Run: `cd docs/skill-contract/reference && python3 build_vectors.py && python3 -I test_contract_check.py 2>&1 | tail -5`
  - Expected: errors such as `AttributeError: module 'contract_check' has no attribute 'check_grant'`.

- [ ] **Step 3: Implement in `contract_check.py`.**

  Add after `BARE_PYTHON_RE`:

```python
GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"
ACTION_CLASSES = ("read_only", "local_reversible", "push_branch", "open_pr", "merge",
                  "deploy", "spend", "external_message", "delete")
LOCAL_CLASSES = frozenset({"read_only", "local_reversible"})
GATES = ("auto", "grant", "ask")
SIG_LEVELS = ("UNSIGNED", "SIGNED", "SIGNED_HW")
GRANT_NAMESPACE = "skill-contract-grant"
```

  Add `import fnmatch` to the imports.

  Add this section before `# ── CLI`:

```python
# ── Commandment 10: the autonomy grant ─────────────────────────────────────
def grant_violations(st):
    """Grant-specific problems (after check_statement passed). [] = valid."""
    out = []
    if st.get("predicateType") != GRANT_KIND:
        return ["predicateType must be %s" % GRANT_KIND]
    pred = st["predicate"]
    p = pred.get("payload") or {}
    scope = p.get("scope")
    if not (isinstance(scope, dict) and isinstance(scope.get("repo"), str)
            and isinstance(scope.get("branch_pattern"), str) and scope["branch_pattern"]):
        out.append("payload.scope must be {repo, branch_pattern}")
    for d in p.get("decisions") if isinstance(p.get("decisions"), list) else [None]:
        if not (isinstance(d, dict) and all(isinstance(d.get(k), str) and d[k].strip()
                                            for k in ("id", "question", "answer"))):
            out.append("each decision must carry non-empty id, question and answer")
            break
    policy = p.get("gate_policy")
    if not isinstance(policy, dict):
        out.append("payload.gate_policy must be an object")
        policy = {}
    for cls, gate in policy.items():
        if cls not in ACTION_CLASSES:
            out.append("gate_policy names unknown action class %r" % cls)
        elif gate not in GATES:
            out.append("gate_policy[%r] must be one of auto, grant, ask" % cls)
        elif gate == "auto" and cls not in LOCAL_CLASSES:
            out.append("gate_policy[%r] may not be auto; at most grant" % cls)
    req = p.get("require_signature", {})
    if not (isinstance(req, dict) and all(c in ACTION_CLASSES and v in SIG_LEVELS[1:]
                                          for c, v in req.items())):
        out.append("require_signature maps action classes to SIGNED or SIGNED_HW")
    if not TIME_RE.match(str(p.get("expires_at", ""))):
        out.append("payload.expires_at must be RFC 3339 UTC (YYYY-MM-DDThh:mm:ssZ)")
    if not isinstance(p.get("revoked"), bool):
        out.append("payload.revoked must be a boolean")
    if not st.get("subject"):
        out.append("a grant must pin the spec and plan it was approved for")
    if p.get("revoked") is True:
        return out
    accepted = [a for a in pred.get("assertions") or [] if a.get("test") == "grant-accepted"]
    if len(accepted) != 1:
        out.append("a grant needs exactly one grant-accepted assertion")
    elif not (isinstance(accepted[0].get("assertedBy"), dict)
              and isinstance(accepted[0]["assertedBy"].get("human"), str)
              and accepted[0]["assertedBy"]["human"].strip()
              and accepted[0].get("result", {}).get("outcome") == "passed"):
        out.append("grant-accepted must be asserted by a human with outcome passed")
    return out


def _grant_envelopes(root):
    d = envelope_dir(root)
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            st, err = load_envelope(os.path.join(d, f))
            if st is not None and not err and isinstance(st, dict) \
                    and st.get("predicateType") == GRANT_KIND and not check_statement(st):
                out.append((os.path.join(d, f), st))
    return out


def is_superseded(root, grant_id):
    return any(st["predicate"].get("wasRevisionOf") == grant_id for _, st in _grant_envelopes(root))


def latest_grant(root):
    envs = _grant_envelopes(root)
    revised = {st["predicate"].get("wasRevisionOf") for _, st in envs}
    heads = [(st["predicate"]["generatedAtTime"], st["predicate"]["id"], p)
             for p, st in envs if st["predicate"]["id"] not in revised]
    return max(heads)[2] if heads else None


def current_branch(root):
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def signature_level(path, env=None):
    """UNSIGNED unless <path>.sig verifies (Task 2 implements verification)."""
    return "UNSIGNED"


def _parse_time(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check_grant(root, action, path=None, now=None, branch=None, env=None):
    rep = {"status": "NONE", "id": None, "reason": None, "gate": None, "signed": None,
           "path": None, "violations": []}
    path = path or latest_grant(root)
    if path is None:
        rep["reason"] = "no-grant"
        return rep
    rep["path"] = path
    st, err = load_envelope(path)
    viol = ["C%d: %s" % v for v in (err or check_statement(st))]
    if not viol:
        viol = grant_violations(st)
    if viol:
        rep.update(status="INVALID", reason="invalid", violations=viol)
        return rep
    pred, p = st["predicate"], st["predicate"]["payload"]
    rep["id"] = pred["id"]

    def ask(reason):
        rep.update(status="ASK", reason=reason)
        return rep

    if p["revoked"]:
        return ask("revoked")
    if is_superseded(root, pred["id"]):
        return ask("superseded")
    if (now or utc_now()) >= _parse_time(p["expires_at"]):
        return ask("expired")
    if stale_names(root, st["subject"]):
        return ask("stale")
    branch = branch if branch is not None else current_branch(root)
    if branch is not None and not fnmatch.fnmatchcase(branch, p["scope"]["branch_pattern"]):
        return ask("branch")
    gate = p["gate_policy"].get(action, "ask")
    rep["gate"] = gate
    if gate == "ask":
        return ask("gate-ask")
    rep["signed"] = signature_level(path, env)
    need = (p.get("require_signature") or {}).get(action)
    if need and SIG_LEVELS.index(rep["signed"]) < SIG_LEVELS.index(need):
        return ask("signature")
    rep["status"] = "COVERED"
    return rep


def revoke_grant(root, grant_id=None, now=None):
    """Write a revision with revoked: true. Tightening is always allowed: no human needed."""
    if grant_id is None:
        path = latest_grant(root)
    else:
        path = os.path.join(envelope_dir(root), grant_id + ".json")
    st, err = load_envelope(path) if path and os.path.isfile(path) else (None, [(3, "no such grant")])
    if st is None or err or st.get("predicateType") != GRANT_KIND:
        raise ValueError("no grant to revoke under %s" % envelope_dir(root))
    if is_superseded(root, st["predicate"]["id"]):
        raise ValueError("%s is superseded; revoke the newest revision" % st["predicate"]["id"])
    now = now or utc_now()
    pred = st["predicate"]
    rev = {"_type": STATEMENT_TYPE, "subject": st["subject"], "predicateType": GRANT_KIND,
           "predicate": {"skillContract": CONTRACT_VERSION, "id": new_id(GRANT_KIND, now),
                         "wasAttributedTo": pred["wasAttributedTo"],
                         "generatedAtTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "wasRevisionOf": pred["id"],
                         "payload": dict(pred["payload"], revoked=True), "assertions": []}}
    return write_envelope(root, rev)
```

  In `build_parser()`, add:

```python
    p = sub.add_parser("check-grant", help="commandment 10: does a grant cover this action")
    p.add_argument("file", nargs="?")
    p.add_argument("--root", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("revoke-grant", help="commandment 10: revoke the newest (or named) grant")
    p.add_argument("id", nargs="?")
    p.add_argument("--root", required=True)
```

  In `main()`, before the final `return 1`, add:

```python
    if a.cmd == "check-grant":
        if a.action not in ACTION_CLASSES:
            print("usage: --action must be one of %s" % ", ".join(ACTION_CLASSES), file=sys.stderr)
            return 1
        rep = check_grant(a.root, a.action, path=a.file)
        if a.json:
            print(json.dumps(rep, sort_keys=True))
        for v in rep["violations"]:
            print("FAIL: %s" % v)
        if rep["status"] == "COVERED":
            print("GRANT: COVERED id=%s class=%s gate=%s signed=%s"
                  % (rep["id"], a.action, rep["gate"], rep["signed"]))
            return 0
        if rep["status"] == "INVALID":
            print("GRANT: INVALID %s" % (rep["path"],))
            return 2
        if rep["status"] == "NONE":
            print("GRANT: NONE")
            return 3
        print("GRANT: ASK id=%s reason=%s" % (rep["id"], rep["reason"]))
        return 3
    if a.cmd == "revoke-grant":
        try:
            print("REVOKED: %s" % revoke_grant(a.root, a.id))
            return 0
        except (ValueError, OSError) as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 1
```

  Add these lines to the module docstring's usage block:

```
    contract_check.py check-grant [FILE] --root DIR --action CLASS [--json]    C10
    contract_check.py revoke-grant [ID] --root DIR                             C10

check-grant exits 0 COVERED, 3 ASK or NONE, 2 INVALID, 1 usage; its last line is GRANT: ...
```

- [ ] **Step 4: Update SPEC.md.**
  - Replace commandment 10's first sentence with: "A producer MUST propose each handoff and wait for a yes, unless a valid `autonomy-grant/v1` (below) covers the action's class, which the reference checker reports as `check-grant` exiting 0."
  - After the commandments, add a normative section **"The autonomy grant (commandment 10)"** containing:
    - the kind URI;
    - the action-class table from the design spec §2, verbatim;
    - the rules:
      - "A class absent from `gate_policy` is `ask`."
      - "`auto` is allowed only on `read_only` and `local_reversible`; `auto` on any other class makes the grant invalid."
      - "A grant MUST carry exactly one `grant-accepted` assertion whose `assertedBy` names a human; a grant attributed to a skill is invalid."
      - "A receiver MUST treat a revoked, superseded, expired or stale grant as not covering anything."
  - Add a *Non-normative* paragraph: "An agent with a shell on the same machine can write an unsigned grant, or sign one with a software key it creates. Only a signature by a hardware-backed (`sk-`) key shows that a person physically touched a key. The same limit applies to transcript roles."

- [ ] **Step 5: Regenerate the vectors, re-vendor, and run everything.**
  - Run: `cd docs/skill-contract/reference && python3 build_vectors.py && python3 -I test_contract_check.py && python3 -I test_e2e.py; cd - && make contract-vendor && make contract`
  - Expected: all tests pass, `VECTORS_WRITTEN: …`, and `make contract` passes.
  - Then run `make gate-skill SKILL=spec-first-planning` and `make gate-skill SKILL=crafting-self-prompting-loops`. Expected: all PASS, with the vendored copies byte-identical.

- [ ] **Step 6: Commit.**

```bash
git add docs/skill-contract spec-first-planning/assets/contract_check.py crafting-self-prompting-loops/assets/contract_check.py
git commit -m "feat(skill-contract): autonomy-grant/v1 — check-grant, revoke-grant, C10 vectors

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X"
```

---

### Task 2: Reference checker — signature levels

**Files:**
- Modify: `docs/skill-contract/reference/contract_check.py` (replace the `signature_level` stub).
- Modify: `docs/skill-contract/reference/test_contract_check.py` (add a `SignatureTests` class).
- Then run `make contract-vendor`.

**Interfaces:**
- Consumes: `check_grant`, `GRANT_NAMESPACE` and `SIG_LEVELS` from Task 1.
- Produces:
  - `allowed_signers_path(env: dict | None = None) -> str | None`;
  - `level_for_key_type(key_type: str) -> str`, which returns "SIGNED_HW" when the upper-cased type ends with "-SK" or starts with "SK-", and "SIGNED" otherwise;
  - `signature_level(path, env=None) -> str`.

- [ ] **Step 1: Write the failing tests.**

```python
class SignatureTests(unittest.TestCase):
    def test_key_type_classifier(self):
        self.assertEqual(cc.level_for_key_type("ED25519-SK"), "SIGNED_HW")
        self.assertEqual(cc.level_for_key_type("sk-ssh-ed25519@openssh.com"), "SIGNED_HW")
        self.assertEqual(cc.level_for_key_type("ECDSA-SK"), "SIGNED_HW")
        self.assertEqual(cc.level_for_key_type("ED25519"), "SIGNED")
        self.assertEqual(cc.level_for_key_type("RSA"), "SIGNED")

    def test_no_sig_file_is_unsigned(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "g.json")
            _write(p, "{}")
            self.assertEqual(cc.signature_level(p, env={}), "UNSIGNED")

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
    def test_software_key_signature_is_signed(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = os.path.join(tmp, "k")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
            with open(key + ".pub", encoding="utf-8") as f:
                pub = f.read().split()
            signers = os.path.join(tmp, "allowed_signers")
            _write(signers, "dana@example %s %s\n" % (pub[0], pub[1]))
            p = os.path.join(tmp, "g.json")
            _write(p, '{"grant": 1}\n')
            subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", key, "-n", cc.GRANT_NAMESPACE, p],
                           check=True, capture_output=True)
            env = {"SKILL_CONTRACT_ALLOWED_SIGNERS": signers}
            self.assertEqual(cc.signature_level(p, env=env), "SIGNED")
            _write(p, '{"grant": 2}\n')  # tampered after signing
            self.assertEqual(cc.signature_level(p, env=env), "UNSIGNED")

    def test_missing_ssh_keygen_degrades_to_unsigned(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "g.json")
            _write(p, "{}")
            _write(p + ".sig", "x")
            with mock.patch.object(cc.shutil, "which", return_value=None):
                self.assertEqual(cc.signature_level(p, env={"SKILL_CONTRACT_ALLOWED_SIGNERS": p}),
                                 "UNSIGNED")
```

  `ssh-keygen -Y sign` writes `<file>.sig`. Add `from unittest import mock` and `import subprocess` if they're missing.

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Run: `cd docs/skill-contract/reference && python3 -I test_contract_check.py 2>&1 | tail -3`
  - Expected: FAIL/ERROR (`level_for_key_type` doesn't exist, and SIGNED isn't returned).

- [ ] **Step 3: Implement.** Replace the stub with:

```python
def allowed_signers_path(env=None):
    env = os.environ if env is None else env
    p = env.get("SKILL_CONTRACT_ALLOWED_SIGNERS")
    if p:
        return p if os.path.isfile(p) else None
    try:
        r = subprocess.run(["git", "config", "--get", "gpg.ssh.allowedSignersFile"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            cand = os.path.expanduser(r.stdout.strip())
            if os.path.isfile(cand):
                return cand
    except (OSError, subprocess.SubprocessError):
        pass
    cand = os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config", "skill-contract",
                        "allowed_signers")
    return cand if os.path.isfile(cand) else None


def level_for_key_type(key_type):
    t = key_type.upper()
    return "SIGNED_HW" if t.endswith("-SK") or t.startswith("SK-") else "SIGNED"


def signature_level(path, env=None):
    """UNSIGNED | SIGNED | SIGNED_HW. Never raises: any failure is UNSIGNED."""
    sig = path + ".sig"
    keygen = shutil.which("ssh-keygen")
    signers = allowed_signers_path(env) if os.path.isfile(sig) else None
    if not (keygen and signers):
        return "UNSIGNED"
    try:
        r = subprocess.run([keygen, "-Y", "find-principals", "-s", sig, "-f", signers],
                           capture_output=True, text=True, timeout=20)
        principal = r.stdout.splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else None
        if not principal:
            return "UNSIGNED"
        with open(path, "rb") as f:
            r = subprocess.run([keygen, "-Y", "verify", "-f", signers, "-I", principal,
                                "-n", GRANT_NAMESPACE, "-s", sig], stdin=f,
                               capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError, IndexError):
        return "UNSIGNED"
    if r.returncode != 0:
        return "UNSIGNED"
    m = re.search(r"with (\S+) key", r.stdout + r.stderr)
    return level_for_key_type(m.group(1)) if m else "SIGNED"
```

- [ ] **Step 4: Run and re-vendor.**
  - Run: `cd docs/skill-contract/reference && python3 -I test_contract_check.py; cd - && make contract-vendor && make contract`
  - Expected: PASS. The software-key test is skipped only where ssh-keygen is missing.
  - In the commit message, state that `SIGNED_HW` is covered by the classifier unit test only, because it needs hardware.

- [ ] **Step 5: Commit.**

```bash
git add docs/skill-contract/reference spec-first-planning/assets/contract_check.py crafting-self-prompting-loops/assets/contract_check.py
git commit -m "feat(skill-contract): grant signature levels via ssh-keygen (UNSIGNED/SIGNED/SIGNED_HW)

SIGNED_HW is verified by the key-type classifier test only; it needs a FIDO key.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X"
```

---

### Task 3: spec_lint — light rules: typed Constraints and Required truths (always on)

**Files:**
- Modify: `spec-first-planning/assets/spec_lint.py`:
  - extend `parse_spec`;
  - add the parse helpers;
  - add `lint_light(spec)`, called from `lint`;
  - add `"Constraints"` and `"Required truths"` to `REQUIRED_SECTIONS`.
- Modify: `spec-first-planning/assets/test_spec_lint.py`.
- Modify: `spec-first-planning/references/spec-template.md` and `references/spec-format.md` (the grammar and rules 6–9).
- Modify every existing spec fixture in `test_spec_lint.py`, `test_spec_to_tasks.py` and `eval/run_eval.py` so it carries valid Constraints and Required truths sections. Add them to the fixtures; do not delete checks.

**The grammar (verbatim; put it in spec-format.md):**
- `## Constraints` bullets: `- <ID> [<type>]: <statement>`.
  - `<ID>` matches `(B|T|U|S|O)[0-9]+`.
  - `<type>` is `invariant`, `goal` or `boundary`.
- `## Required truths` bullets: `- RT<n> [<status>]: <statement> (parent: <OUTCOME|RT<k>>; maps_to: <ids>; reqs: <R ids>; confidence: <0..1>; check: <runnable check>)`.
  - `<status>` is `SATISFIED`, `PARTIAL`, `NOT_SATISFIED` or `SPECIFICATION_READY`.
  - `check:` MUST be the last field. Everything after `check:` up to the final `)` is the check.

**Interfaces:**
- Produces: `parse_spec(text)` also returns:
  - `constraints`: list of dict {id, type, text};
  - `malformed_constraints`: list[str];
  - `truths`: list of dict {id, num, status, text, parent, maps_to: list[str], reqs: list[int], confidence: float|None, check: str};
  - `malformed_truths`: list[str].
- Produces: `CONSTRAINT_TYPES`, `TRUTH_STATUSES` and `lint_light(spec) -> list[str]`.

- [ ] **Step 1: Write the failing tests** in `test_spec_lint.py`:

```python
LIGHT = """# Spec: Export

## Problem
Users cannot export rows.

## Users
- analysts

## Goals
- export works

## Non-goals
- PDF

## Constraints
- B1 [invariant]: No row is lost.
- T1 [boundary]: Export finishes within 10 s for 10000 rows.

## Required truths
- RT1 [SPECIFICATION_READY]: Every row reaches the file. (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: python3 -m pytest -k rows)
- RT2 [NOT_SATISFIED]: The writer streams. (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.6; check: python3 bench.py --max 10)

## Requirements
- R1: The export must include every row.

## Acceptance criteria
- R1: run `python3 -m pytest -k rows`, expect exit 0.

## Open questions
"""


class LightRules(unittest.TestCase):
    def issues(self, text):
        return spec_lint.lint(text)

    def test_light_spec_is_clean(self):
        self.assertEqual(self.issues(LIGHT), [])

    def test_missing_constraints_section_fails(self):
        t = LIGHT.replace("## Constraints\n- B1 [invariant]: No row is lost.\n- T1 [boundary]: Export finishes within 10 s for 10000 rows.\n\n", "")
        self.assertTrue(any("Constraints" in i for i in self.issues(t)))

    def test_bad_constraint_type_fails(self):
        t = LIGHT.replace("B1 [invariant]", "B1 [wish]")
        self.assertTrue(any("B1" in i and "type" in i for i in self.issues(t)))

    def test_unmapped_constraint_fails(self):
        t = LIGHT.replace("maps_to: T1;", "maps_to: B1;")
        self.assertTrue(any("T1" in i and "no required truth" in i for i in self.issues(t)))

    def test_truth_unknown_constraint_fails(self):
        t = LIGHT.replace("maps_to: B1;", "maps_to: B9;")
        self.assertTrue(any("RT1" in i and "B9" in i for i in self.issues(t)))

    def test_truth_unknown_requirement_fails(self):
        t = LIGHT.replace("reqs: R1; confidence: 0.8", "reqs: R7; confidence: 0.8")
        self.assertTrue(any("RT1" in i and "R7" in i for i in self.issues(t)))

    def test_truth_without_check_fails(self):
        t = LIGHT.replace("; check: python3 -m pytest -k rows)", ")")
        self.assertTrue(any("RT1" in i and "check" in i for i in self.issues(t)))

    def test_bad_parent_fails(self):
        t = LIGHT.replace("parent: RT1;", "parent: RT5;")
        self.assertTrue(any("RT2" in i and "parent" in i for i in self.issues(t)))

    def test_no_outcome_root_fails(self):
        t = LIGHT.replace("parent: OUTCOME;", "parent: RT2;")
        self.assertTrue(any("OUTCOME" in i for i in self.issues(t)))

    def test_confidence_out_of_range_fails(self):
        t = LIGHT.replace("confidence: 0.8", "confidence: 1.4")
        self.assertTrue(any("RT1" in i and "confidence" in i for i in self.issues(t)))

    def test_bad_status_fails(self):
        t = LIGHT.replace("RT1 [SPECIFICATION_READY]", "RT1 [DONE]")
        self.assertTrue(any("RT1" in i and "status" in i for i in self.issues(t)))
```

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Run: `python3 spec-first-planning/assets/test_spec_lint.py 2>&1 | tail -3`
  - Expected: FAIL. Existing fixtures may also fail on the missing sections; that's expected until Step 4.

- [ ] **Step 3: Implement.** Add the constants and parsers to `spec_lint.py`, and call `lint_light` at the end of `lint()` (`issues += lint_light(spec)`):

```python
CONSTRAINT_TYPES = ("invariant", "goal", "boundary")
TRUTH_STATUSES = ("SATISFIED", "PARTIAL", "NOT_SATISFIED", "SPECIFICATION_READY")
_CONSTRAINT_RE = re.compile(r"^(?:\*\*)?([BTUSO][0-9]+)(?:\*\*)?\s*\[([A-Za-z_]+)\]\s*:\s*(.+)$")
_TRUTH_RE = re.compile(r"^(?:\*\*)?RT([0-9]+)(?:\*\*)?\s*\[([A-Za-z_]+)\]\s*:\s*(.*?)\s*\((.*)\)\s*$")
_ID_LIST_RE = re.compile(r"[A-Za-z]+[0-9]+")


def _fields(raw):
    """Split 'a: x; b: y; check: anything; even; semicolons' into a dict."""
    head, sep, check = raw.partition("check:")
    out = {"check": check.strip() if sep else ""}
    for part in head.split(";"):
        k, s, v = part.partition(":")
        if s:
            out[k.strip().lower()] = v.strip()
    return out


def _parse_constraints(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Constraints"):
        m = _CONSTRAINT_RE.match(b)
        if m:
            good.append({"id": m.group(1), "type": m.group(2).lower(), "text": m.group(3).strip()})
        else:
            bad.append(b)
    return good, bad


def _parse_truths(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Required truths"):
        m = _TRUTH_RE.match(b)
        if not m:
            bad.append(b)
            continue
        f = _fields(m.group(4))
        try:
            conf = float(f.get("confidence", ""))
        except ValueError:
            conf = None
        good.append({"id": "RT%s" % m.group(1), "num": int(m.group(1)),
                     "status": m.group(2).upper(), "text": m.group(3).strip(),
                     "parent": f.get("parent", "").strip(),
                     "maps_to": _ID_LIST_RE.findall(f.get("maps_to", "")),
                     "reqs": [int(n) for n in re.findall(r"R([0-9]+)", f.get("reqs", ""))],
                     "confidence": conf, "check": f.get("check", "")})
    return good, bad


def lint_light(spec):
    issues = []
    cons, truths = spec["constraints"], spec["truths"]
    for b in spec["malformed_constraints"]:
        issues.append("Constraints bullet is not '- <B|T|U|S|O><n> [type]: ...': '%s'" % b[:60])
    for b in spec["malformed_truths"]:
        issues.append("Required truths bullet is not '- RT<n> [status]: ... (parent: ...; "
                      "maps_to: ...; reqs: ...; confidence: ...; check: ...)': '%s'" % b[:60])
    seen = set()
    for c in cons:
        if c["id"] in seen:
            issues.append("constraint %s is defined twice" % c["id"])
        seen.add(c["id"])
        if c["type"] not in CONSTRAINT_TYPES:
            issues.append("constraint %s has type '%s'; use invariant, goal or boundary"
                          % (c["id"], c["type"]))
    known_c = {c["id"] for c in cons}
    known_r = {n for n, _ in spec["requirements"]}
    nums = [t["num"] for t in truths]
    if truths and nums != list(range(1, len(nums) + 1)):
        issues.append("required truth ids must be RT1..RT%d in order" % len(nums))
    known_t = {t["id"] for t in truths}
    for t in truths:
        if t["status"] not in TRUTH_STATUSES:
            issues.append("%s has status '%s'; use one of %s"
                          % (t["id"], t["status"], ", ".join(TRUTH_STATUSES)))
        if t["parent"] != "OUTCOME" and (t["parent"] not in known_t or t["parent"] == t["id"]):
            issues.append("%s parent '%s' must be OUTCOME or another RT" % (t["id"], t["parent"]))
        if not t["maps_to"]:
            issues.append("%s maps to no constraint" % t["id"])
        for cid in t["maps_to"]:
            if cid not in known_c:
                issues.append("%s maps to unknown constraint %s" % (t["id"], cid))
        if not t["reqs"]:
            issues.append("%s names no requirement (reqs: R<n>)" % t["id"])
        for r in t["reqs"]:
            if r not in known_r:
                issues.append("%s names unknown requirement R%d" % (t["id"], r))
        if t["confidence"] is None or not 0.0 <= t["confidence"] <= 1.0:
            issues.append("%s confidence must be a number from 0 to 1" % t["id"])
        if not t["check"]:
            issues.append("%s has no runnable check (end the fields with 'check: ...')" % t["id"])
    mapped = {cid for t in truths for cid in t["maps_to"]}
    for c in cons:
        if c["id"] not in mapped:
            issues.append("constraint %s has no required truth mapping to it" % c["id"])
    if truths and not any(t["parent"] == "OUTCOME" for t in truths):
        issues.append("no required truth has parent OUTCOME — anchor from the outcome")
    return issues
```

  Also in `parse_spec`, before its `return`, add `constraints, bad_c = _parse_constraints(sections)` and `truths, bad_t = _parse_truths(sections)`, and add the four keys to the returned dict.

  Add `"Constraints"` and `"Required truths"` to `REQUIRED_SECTIONS`, so rule 1 (present and non-empty) covers them.

- [ ] **Step 4: Update the fixtures, the template and the format doc.**
  - Add Constraints and Required truths sections to every existing spec fixture in `test_spec_lint.py`, `test_spec_to_tasks.py` and `eval/run_eval.py`.
  - Add them to `references/spec-template.md` (with a comment block showing the grammar) and to `references/spec-format.md` (the grammar bullets above, plus lint rules 6–9: constraint grammar and types; truth grammar, status and confidence; traceability of constraint → RT → requirement; the OUTCOME root).
  - Run: `python3 spec-first-planning/assets/test_spec_lint.py && python3 spec-first-planning/assets/test_spec_to_tasks.py && python3 spec-first-planning/eval/run_eval.py | tail -1`
  - Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add spec-first-planning
git commit -m "feat(spec-first-planning): light planning rules — typed constraints and required truths

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X"
```

---

### Task 4: spec_lint — `--converged` and `--unattended`

**Files:**
- Modify: `spec-first-planning/assets/spec_lint.py`: the parsers for Tensions, Solution options, Iterations and Decisions; `lint_converged(spec)`; `lint_unattended(spec)`; and the CLI flags.
- Modify: `spec-first-planning/assets/test_spec_lint.py` and `references/spec-format.md` (rules 10–16).

**The grammar (verbatim; put it in spec-format.md):**
- `## Tensions` bullets: `- TN<n> [<type>]: <text> (between: <ids>; status: <resolved|accepted>; strategy: <Prioritize|Partition|Transform|Accept|Invalidate>; decision: D<k>)`.
  - `<type>` is `trade_off`, `resource_tension` or `hidden_dependency`.
  - `decision` is REQUIRED when `status: accepted` or `strategy: Accept`, and optional otherwise.
  - The section may hold the single bullet `- none` when no tension exists.
- `## Solution options` bullets: `- OPT-<LETTER>: <text> (complexity: <Low|Medium|High>; reversibility: <TWO_WAY|REVERSIBLE_WITH_COST|ONE_WAY>; satisfies: <RT ids>)`.
  - 2–4 options.
  - Plus one non-bullet line: `Recommended: OPT-<LETTER> — <rationale>`. If the recommendation breaks a tie, append `(decision: D<k>)`.
- `## Iterations` bullets: `- I<n>: <what changed>`, numbered I1..In in order, with 1 ≤ n ≤ 5.
- `## Decisions` bullets: `- D<n>: <question> -> <answer> (source: <where>)`. `→` is accepted in place of `->`, and the answer MUST be non-empty.

**Rules:**
- `--converged` runs everything in the light pass, plus:
  - (10) the Tensions, Solution options and Iterations sections are present;
  - (11) tension grammar, types, known `between` ids (≥2), and a resolved status or a decision;
  - (12) every RT status is `SPECIFICATION_READY` or `SATISFIED`;
  - (13) option grammar, 2–4 options, the Recommended line exists and names an option;
  - (14) the recommended option satisfies every RT, and the pragmatic rule holds: no other option that satisfies every RT has a lower `(complexity rank, reversibility rank)`. Ranks: Low 0 < Medium 1 < High 2; TWO_WAY 0 < REVERSIBLE_WITH_COST 1 < ONE_WAY 2. An equal-rank alternative means the Recommended line must cite an existing decision;
  - (15) Iterations are I1..In with n ≤ 5, and the message for n > 5 says "iteration cap exceeded — stop and ask the user";
  - (16) the Open questions list is empty.
  - Every `D<k>` referenced must exist in Decisions.
- `--unattended` runs everything in `--converged`, plus: the Decisions section is present, non-empty, and every decision has an answer.

**Interfaces:**
- Consumes: `parse_spec` and `lint` from Task 3.
- Produces:
  - `parse_spec` also returns `tensions`, `options`, `recommended` (dict {id, decision} or None), `iterations` (list[int]) and `decisions` (list of dict {id, question, answer, source}), plus the `malformed_*` lists;
  - `lint(text, mode="light")`, where mode ∈ {"light", "converged", "unattended"};
  - CLI `spec_lint.py [--converged|--unattended] <spec.md>`, which prints `LINT_RESULT: PASS (… mode=<mode>)`.

- [ ] **Step 1: Write the failing tests.** Build `FULL` from Task 3's `LIGHT`:
  - set RT2 to `[SPECIFICATION_READY]`;
  - add these sections:

```python
FULL = LIGHT.replace("RT2 [NOT_SATISFIED]", "RT2 [SPECIFICATION_READY]") + """
## Tensions
- TN1 [trade_off]: Streaming vs. atomic write. (between: B1, T1; status: resolved; strategy: Partition)

## Solution options
- OPT-A: Stream rows to a temp file, rename at end. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)
- OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)
Recommended: OPT-A — satisfies every RT at the lowest complexity.

## Iterations
- I1: constrained, tensioned, anchored; chose OPT-A.

## Decisions
- D1: May the export add a dependency? -> no (source: sweep)
"""


class ConvergedRules(unittest.TestCase):
    def lint(self, t, mode):
        return spec_lint.lint(t, mode=mode)

    def test_full_spec_converges_and_is_unattended_ready(self):
        self.assertEqual(self.lint(FULL, "converged"), [])
        self.assertEqual(self.lint(FULL, "unattended"), [])

    def test_light_spec_does_not_converge(self):
        self.assertTrue(self.lint(LIGHT, "converged"))

    def test_not_ready_truth_blocks_convergence(self):
        t = FULL.replace("RT2 [SPECIFICATION_READY]", "RT2 [PARTIAL]")
        self.assertTrue(any("RT2" in i for i in self.lint(t, "converged")))

    def test_unresolved_tension_without_decision_fails(self):
        t = FULL.replace("status: resolved; strategy: Partition", "status: accepted; strategy: Accept")
        self.assertTrue(any("TN1" in i and "decision" in i for i in self.lint(t, "converged")))

    def test_recommending_the_less_pragmatic_option_fails(self):
        t = FULL.replace("OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)",
                         "OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1, RT2)")
        t = t.replace("Recommended: OPT-A", "Recommended: OPT-B")
        self.assertTrue(any("OPT-A" in i and "pragmatic" in i for i in self.lint(t, "converged")))

    def test_recommended_must_satisfy_every_truth(self):
        t = FULL.replace("Recommended: OPT-A", "Recommended: OPT-B")
        self.assertTrue(any("OPT-B" in i and "RT2" in i for i in self.lint(t, "converged")))

    def test_tie_needs_a_decision(self):
        t = FULL.replace("(complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)",
                         "(complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)")
        self.assertTrue(any("tie" in i for i in self.lint(t, "converged")))
        t2 = t.replace("at the lowest complexity.", "at the lowest complexity. (decision: D1)")
        self.assertEqual(self.lint(t2, "converged"), [])

    def test_iteration_cap(self):
        extra = "".join("- I%d: again\n" % n for n in range(2, 7))
        t = FULL.replace("- I1: constrained, tensioned, anchored; chose OPT-A.\n",
                         "- I1: constrained, tensioned, anchored; chose OPT-A.\n" + extra)
        self.assertTrue(any("iteration cap" in i for i in self.lint(t, "converged")))

    def test_open_question_blocks_convergence(self):
        t = FULL.replace("## Open questions\n", "## Open questions\n- Which delimiter?\n")
        self.assertTrue(any("Open questions" in i for i in self.lint(t, "converged")))

    def test_unattended_needs_answered_decisions(self):
        t = FULL.replace("-> no (source: sweep)", "-> (source: sweep)")
        self.assertTrue(any("D1" in i for i in self.lint(t, "unattended")))
        t2 = FULL.split("## Decisions")[0]
        self.assertTrue(any("Decisions" in i for i in self.lint(t2, "unattended")))

    def test_unknown_decision_reference_fails(self):
        t = FULL.replace("strategy: Partition)", "strategy: Partition; decision: D9)")
        self.assertTrue(any("D9" in i for i in self.lint(t, "converged")))

    def test_cli_modes(self):
        import subprocess, sys, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.md")
            with open(p, "w") as f:
                f.write(LIGHT)
            run = lambda *a: subprocess.run([sys.executable, SPEC_LINT, *a, p], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--converged").returncode, 1)
            self.assertEqual(run("--unattended").returncode, 1)
```

  `SPEC_LINT` is the existing path constant in the test module. If there is none, define `SPEC_LINT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_lint.py")`.

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Run: `python3 spec-first-planning/assets/test_spec_lint.py 2>&1 | tail -3`
  - Expected: FAIL. `lint()` has no `mode` yet.

- [ ] **Step 3: Implement.** Follow the Task 3 parser style:
  - Parse each section with an anchored regex and `_fields()`.
  - Parse `Recommended:` from the non-bullet lines of `## Solution options` with `^Recommended:\s*(OPT-[A-Z])\b(.*)$`; take `decision` from `\(decision:\s*(D[0-9]+)\)`.
  - Parse decisions with `^D([0-9]+)\s*:\s*(.*?)\s*(?:->|→)\s*(.*?)\s*\(source:\s*(.*)\)\s*$`. An answer that is empty after stripping is recorded as `""`.
  - Implement rules 10–16 exactly as listed above, with issue texts that contain the ids the tests look for.
  - Make `lint(text, mode="light")` run the light rules, then `lint_converged` for converged or unattended, then `lint_unattended` for unattended.
  - For rule 16, add an "Open questions" issue when any bullet is present in converged mode.
  - Update `main()` to accept at most one of `--converged`/`--unattended` before the path. Keep exit 1 for lint failures and exit 2 for usage or unreadable input.

- [ ] **Step 4: Run the tests and confirm they pass.** Update `references/spec-format.md` with the grammar and rules 10–16.
  - Run: `python3 spec-first-planning/assets/test_spec_lint.py && make gate-skill SKILL=spec-first-planning`
  - Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add spec-first-planning
git commit -m "feat(spec-first-planning): --converged and --unattended lint modes (manifold convergence)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X"
```

---

### Task 5: Plan payload fields and `write_grant.py`

**Files:**
- Modify: `spec-first-planning/assets/spec_to_tasks.py`:
  - `to_task_plan_payload` adds optional `constraints`, `required_truths` and `decisions` when the spec has them;
  - `SKILL_VERSION = "2.0.0"`.
- Modify: `spec-first-planning/assets/schemas/task-plan.v1.json` (the three optional properties; `required` is unchanged).
- Create: `spec-first-planning/assets/write_grant.py` and `spec-first-planning/assets/test_write_grant.py`.
- Modify: `spec-first-planning/assets/test_spec_to_tasks.py`.

**Interfaces:**
- Consumes:
  - `spec_lint.parse_spec` and `spec_lint.lint(text, mode)` (Tasks 3–4);
  - from the vendored `contract_check`: `build_statement`, `write_envelope`, `pin`, `grant_violations`, `check_statement`, `GRANT_KIND`, `utc_now`.
- Produces: the CLI `write_grant.py --root DIR --spec REL_SPEC --plan PLAN_ENVELOPE_PATH --answers ANSWERS.json --accepted-by NAME`.
  - Exit 0: prints `GRANT: <path>`.
  - Exit 1: refused, with a reason line `REFUSED: …`.
  - Exit 2: usage error.
- Produces: `build_grant(root, spec_rel, plan_rel, answers: dict, accepted_by: str, now=None) -> dict`, the statement (not yet written).
- Answers JSON keys:
  - `branch_pattern` (str, required);
  - `gate_policy` (dict, required);
  - `expires_at` (str, required);
  - `budget`, `stop_on`, `defaults` and `system_one` (optional, defaulting to `{}`, `[]`, `[]` and `{"allowed": false}`).

- [ ] **Step 1: Write the failing tests** in `test_write_grant.py`. Use a temp repo with `docs/spec.md` = Task 4's `FULL` (import it from `test_spec_lint`, or copy the constant) and a plan envelope produced by `spec_to_tasks.write_task_plan_envelope`. Cover these cases:
  1. `build_grant` + write gives an envelope for which `contract_check.check_grant(root, "local_reversible", branch="factory/x")` returns COVERED, with `gate_policy={"read_only": "auto", "local_reversible": "grant"}`, `expires_at=_in_one_day()`, `branch_pattern="factory/*"`.
  2. The decisions in the payload equal the spec's Decisions (D1).
  3. The single assertion is `grant-accepted`, `assertedBy {"human": "Dana"}`, with command `["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py", "--unattended", "docs/spec.md"]`.
  4. It is refused (exit 1, `REFUSED:`) when the spec fails `--unattended` (use `LIGHT`).
  5. It is refused when `gate_policy` has `{"merge": "auto"}`; the `grant_violations` message is echoed.
  6. It is refused when `--accepted-by` is empty or blank.
  7. The subjects pin both `docs/spec.md` and the plan envelope path.

  In `test_spec_to_tasks.py`, add: for `FULL`, the payload has `constraints` (2), `required_truths` (2) and `decisions` (1); for a spec without Decisions, the payload has no `decisions` key; and the version test now expects 2.0.0.

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Run: `python3 spec-first-planning/assets/test_write_grant.py 2>&1 | tail -3`
  - Expected: FAIL (no module `write_grant`).

- [ ] **Step 3: Implement `write_grant.py`.** Follow `spec_to_tasks.py`'s header and its lazy `contract_check` import style (the >=3.10 guard). Define `class GrantRefused(Exception): pass` at module level:

```python
def build_grant(root, spec_rel, plan_rel, answers, accepted_by, now=None):
    text = open(os.path.join(root, *spec_rel.split("/")), encoding="utf-8").read()
    issues = spec_lint.lint(text, mode="unattended")
    if issues:
        raise GrantRefused("spec is not decision-closed: " + issues[0])
    if not (isinstance(accepted_by, str) and accepted_by.strip()):
        raise GrantRefused("--accepted-by must name the human who said yes")
    spec = spec_lint.parse_spec(text)
    payload = {
        "scope": {"repo": ".", "branch_pattern": answers["branch_pattern"]},
        "decisions": [{"id": d["id"], "question": d["question"], "answer": d["answer"],
                       "source": d["source"]} for d in spec["decisions"]],
        "defaults": answers.get("defaults", []),
        "gate_policy": answers["gate_policy"],
        "budget": answers.get("budget", {}),
        "stop_on": answers.get("stop_on", []),
        "expires_at": answers["expires_at"],
        "system_one": answers.get("system_one", {"allowed": False}),
        "revoked": False,
    }
    accepted = {"test": "grant-accepted", "assertedBy": {"human": accepted_by.strip()},
                "result": {"outcome": "passed"},
                "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                            "--unattended", spec_rel],
                "subject": [contract_check.pin(root, spec_rel)]}
    st = contract_check.build_statement(contract_check.GRANT_KIND, "spec-first-planning",
                                        SKILL_VERSION, root, [spec_rel, plan_rel], payload,
                                        [accepted], now=now)
    viol = [f"C{n}: {d}" for n, d in contract_check.check_statement(st)] \
        or contract_check.grant_violations(st)
    if viol:
        raise GrantRefused(viol[0])
    return st
```

  Import `SKILL_VERSION` from `spec_to_tasks`. `main()` parses the flags, loads the answers JSON, makes `--plan` relative to `--root` (refusing if it is outside the root), calls `build_grant`, then `contract_check.write_envelope`, and prints `GRANT: <path>`. On `GrantRefused` it prints `REFUSED: <reason>` and exits 1.

  Update `spec_to_tasks.to_task_plan_payload`: when `spec["constraints"]` is non-empty, add `constraints`; do the same for `required_truths` (all truth fields except `num`) and `decisions`. Add the three optional properties to the schema, with object item schemas that require the fields listed above. Extend `payload_errors` to type-check them when present.

- [ ] **Step 4: Run.**
  - Run: `python3 spec-first-planning/assets/test_write_grant.py && python3 spec-first-planning/assets/test_spec_to_tasks.py && make gate-skill SKILL=spec-first-planning`
  - Expected: PASS. If the frontmatter version test fails because SKILL.md still says 1.1.1, bump `metadata.version` to `"2.0.0"` in this commit.

- [ ] **Step 5: Commit.**

```bash
git add spec-first-planning
git commit -m "feat(spec-first-planning): write_grant.py and optional constraints/truths/decisions in task-plan/v1

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X"
```

---

### Task 6: spec-first-planning SKILL.md 2.0.0, references and eval

**Files:**
- Modify: `spec-first-planning/SKILL.md`, `README.md`, `references/spec-template.md` and `references/spec-format.md` (already partly done in Tasks 3–4).
- Create: `spec-first-planning/references/unattended.md`.
- Modify: `spec-first-planning/eval/run_eval.py`.
- Modify: `docs/rfc2119/2026-09-19-classification.md`, spec-first-planning section.

**What SKILL.md must say.** Keep its structure, and keep the BCP 14 declaration and the `json skill-contract` block format.
1. **Description (frontmatter).** Mention the manifold-style planning loop, the opt-in unattended mode, and that it writes an autonomy grant after the user's yes. Keep it ≤ 1024 characters.
2. **The contract section.** List the spec's sections: add Constraints and Required truths as always required, and the full-loop sections as required only when running the full loop.
3. **Workflow.** Insert a numbered **Mode** step first:
   - attended (default) runs a light pass;
   - unattended runs only when the user asks for it.
   
   The skill MUST say which depth it is using. At each checkpoint (after the spec draft and after the plan) it MUST offer *go deeper* and *go unattended*, and it MUST NOT escalate unless the user asks.
4. **Planning loop.** Describe constrain → tension → anchor → choose in 6–10 lines, with the pragmatic rule and convergence. Send the detail to `references/unattended.md` (the decision sweep checklist, the loop and the pre-mortem) and `references/spec-format.md` (the grammar).
   - Lint commands: `spec_lint.py <spec>` (light), `--converged` (full) and `--unattended`.
   - Keep the lint-and-repair rule. After 5 iterations without convergence, the skill MUST stop and ask the user.
5. **Unattended.** After `TASKS_RESULT: PASS` and the plan envelope, show the grant summary (decisions, gate table, expiry, budget). The skill MUST wait for the user's explicit yes before running `python3 "$SKILL_DIR/assets/write_grant.py" --root <repo> --spec <spec> --plan <envelope> --answers <answers.json> --accepted-by "<user's name>"`. It then gives the revoke command: `python3 "$SKILL_DIR/assets/contract_check.py" revoke-grant --root <repo>`. It also states, plainly, that merge, deploy, spend, external messages and deletes are never covered by a grant and will always ask.
6. **Handoff (step 6).** Before proposing, run `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo-root> --action local_reversible`.
   - If it exits 0, you MAY hand off without asking, and you MUST name the grant id and class in your report.
   - Otherwise, the existing rule applies: you MUST propose, and MUST wait for the user's yes.
7. **Honesty line (plain).** The linter checks structure and traceability, not the quality of the reasoning. A grant is the user's recorded yes, but an agent with a shell could forge one, which is why grants cover only reversible actions.
8. **Contract.** Add `autonomy-grant/v1` to `provides` in the `json skill-contract` block, name the grant's claim, and update the sentence about claims.
9. **Upgrade note (plain).** Specs written for 1.x need Constraints and Required truths sections. Run `spec_lint.py` and add the sections it names.
10. **Version.** Set `metadata.version` to `"2.0.0"`.

**`references/unattended.md`** holds:
- the decision-sweep checklist: dependencies; public API and schema/migrations; new services and infra; style/naming defaults; a gate per action class (show the §2 table); expiry; budget; System One use and the data it may be sent;
- the pre-mortem prompt ("three stories of how this fails");
- the full loop and convergence criteria;
- a filled example of `answers.json`.

**Eval** (`eval/run_eval.py`). Add these checks, with NEGATIVE fixtures run as subprocesses against the real scripts.
- Put the fixtures `LIGHT` and `FULL` in the eval, copied verbatim from Tasks 3–4.
- Follow the eval's existing `check(name, ok, detail)` / `EVAL_RESULT` pattern, and read it first.
- `ASSETS` is the skill's `assets/` dir.

```python
def lint(text, *flags):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spec.md")
        open(p, "w", encoding="utf-8").write(text)
        return subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "spec_lint.py"), *flags, p],
                              capture_output=True, text=True, timeout=60).returncode


check("NEGATIVE: an Open question blocks --unattended",
      lint(FULL.replace("## Open questions\n", "## Open questions\n- Which delimiter?\n"), "--unattended") == 1, "")
check("NEGATIVE: a PARTIAL truth blocks --converged",
      lint(FULL.replace("RT2 [SPECIFICATION_READY]", "RT2 [PARTIAL]"), "--converged") == 1, "")
check("NEGATIVE: a truth with no check fails the light lint",
      lint(LIGHT.replace("; check: python3 -m pytest -k rows)", ")")) == 1, "")
check("NEGATIVE: a constraint no truth maps to fails the light lint",
      lint(LIGHT.replace("maps_to: T1;", "maps_to: B1;")) == 1, "")
check("FULL is unattended-ready", lint(FULL, "--unattended") == 0, "")


def write_grant(repo, policy):
    subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "spec_to_tasks.py"),
                    os.path.join(repo, "docs", "spec.md"), "--envelope", repo],
                   capture_output=True, text=True, timeout=60, check=True)
    plan = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(repo, ".skill-contract"))
            for f in fs if f.startswith("task-plan-")][0]
    ans = os.path.join(repo, "answers.json")
    json.dump({"branch_pattern": "*", "gate_policy": policy,
               "expires_at": _in_one_day()}, open(ans, "w"))
    return subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "write_grant.py"),
                           "--root", repo, "--spec", "docs/spec.md", "--plan", plan,
                           "--answers", ans, "--accepted-by", "Dana"],
                          capture_output=True, text=True, timeout=60)


for policy, want in (({"merge": "auto"}, 1), ({"local_reversible": "grant"}, 0)):
    with tempfile.TemporaryDirectory() as repo:
        os.makedirs(os.path.join(repo, "docs"))
        open(os.path.join(repo, "docs", "spec.md"), "w", encoding="utf-8").write(FULL)
        r = write_grant(repo, policy)
        check("write_grant %s -> exit %d" % (policy, want), r.returncode == want, r.stdout + r.stderr)
        if want == 0:
            c = subprocess.run([sys.executable, "-I", os.path.join(ASSETS, "contract_check.py"),
                                "check-grant", "--root", repo, "--action", "local_reversible"],
                               capture_output=True, text=True, timeout=60)
            check("the written grant covers local_reversible", c.returncode == 0, c.stdout)
skill_md = open(os.path.join(os.path.dirname(ASSETS), "SKILL.md"), encoding="utf-8").read()
check("SKILL.md's handoff calls check-grant --action local_reversible",
      "check-grant" in skill_md and "--action local_reversible" in skill_md, "")
```

**BCP 14 bookkeeping.**
- Add classification rows for each new keyworded sentence: mode escalation, the iteration cap, waiting for the yes before `write_grant.py`, and the handoff under a grant. Update the header counts.
- PP-5 MUST NOT become advisory: check it before and after.

- [ ] **Step 1:** Write the eval checks first. Run `python3 spec-first-planning/eval/run_eval.py | tail -3`. Expected: the SKILL.md text check FAILs (and any check that depends on docs).
- [ ] **Step 2:** Write `references/unattended.md`, then update SKILL.md and README.md as specified.
- [ ] **Step 3:** Run `make gate-skill SKILL=spec-first-planning` and `sh scripts/gates/prompting-playbook.sh spec-first-planning | grep -E 'PP-5|PP-7'`. Expected: all PASS, PP-7 PASS, and PP-5 not newly advisory.
- [ ] **Step 4:** Commit, with a message such as `feat(spec-first-planning)!: 2.0.0 — manifold-style planning loop, unattended mode, autonomy grant` and the trailer.

---

### Task 7: Gated skills adopt the contract and honour the grant

**Files:**
- `verifier-installer/SKILL.md`, `verifier-installer/eval/run_eval.py`, `verifier-installer/assets/contract_check.py` (new, vendored);
- `test-safety-net/SKILL.md`, `test-safety-net/eval/run_eval.py`, `test-safety-net/assets/contract_check.py` (new, vendored);
- `crafting-self-prompting-loops/SKILL.md`, `crafting-self-prompting-loops/eval/run_eval.py`;
- `docs/rfc2119/2026-09-19-classification.md`.

**Per skill:**
1. **Frontmatter.**
   - verifier-installer and test-safety-net add `  skill-contract: "1"` under `metadata:`.
   - Bump the versions: verifier-installer `1.2.0`, test-safety-net `1.4.0`, crafting-self-prompting-loops `1.4.0`.
   - Add one description sentence: "Honors a skill-contract autonomy grant at its write gate." Keep the description ≤ 1024 characters.
2. **`## Contract` block** (a new section; for crafting, extend the existing one):
   - verifier-installer and test-safety-net: `{"provides": [], "consumes": ["https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"]}`;
   - crafting: `consumes` = [task-plan/v1, autonomy-grant/v1].
   
   Add one sentence linking skill-contract v1.
3. **Vendor the checker:** run `make contract-vendor`. It copies to every skill whose SKILL.md carries `skill-contract:`.
4. **Gate text (BCP 14; keep the surrounding wording).**
   - **verifier-installer, step 2.** Replace "Do not write anything before this confirmation." with: "You MUST NOT write anything before this confirmation, unless `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo> --action local_reversible` exits 0 (an autonomy grant the user approved covers it); then you MAY proceed, and MUST name the grant id and action class in the report."
     - Update the "Read-only until step 2's confirmation" invariant bullet to add "(or a covering grant)".
   - **test-safety-net, step 3** (~:167, "This is a hard gate — do not proceed past it unconfirmed.") and **Invariant 5** (~:479). Add the same `unless … check-grant … --action local_reversible exits 0 … MUST name the grant id and class` clause. Do this in both places, keyworded consistently.
     - test-safety-net has no `$SKILL_DIR` resolution block for the checker, so reuse its existing helper-locating convention, or add the standard block. Check `scripts/gates/asset-paths.sh` compliance.
   - **crafting-self-prompting-loops:**
     - in "Receiving a skill-contract envelope", after step 1, add: if the handoff arrived without the user confirming it, the handoff MAY proceed only when `check-grant --action local_reversible` exits 0, naming the grant id;
     - the LSC-4 mapping bullet "LSC-8: the user has already confirmed the handoff itself" gets "…or a grant covered it";
     - the LSC-8 principle sentence (~:67) gets: "…MUST wait for explicit human approval, unless `check-grant --action <the action's class>` exits 0 at the moment of the action".
5. **Eval** (each skill). Add this helper and these three checks to each skill's `eval/run_eval.py`, following the file's existing `check(name, ok, detail)` / `EVAL_RESULT` pattern (read it first; adapt the names to it):

```python
import hashlib, json, subprocess, sys, tempfile

GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"


def _grant_fixture(root, assertedBy):
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    spec = "# Spec\n"
    with open(os.path.join(root, "docs", "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(spec)
    digest = hashlib.sha256(spec.encode()).hexdigest()
    gid = "autonomy-grant-v1-20260919T120000Z-a1b2c3"
    st = {"_type": "https://in-toto.io/Statement/v1",
          "subject": [{"name": "docs/spec.md", "digest": {"sha256": digest}}],
          "predicateType": GRANT_KIND,
          "predicate": {"skillContract": "1", "id": gid,
                        "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
                        "generatedAtTime": _now_z(), "wasRevisionOf": None,
                        "payload": {"scope": {"repo": ".", "branch_pattern": "*"},
                                    "decisions": [{"id": "D1", "question": "q", "answer": "a",
                                                   "source": "s"}],
                                    "defaults": [], "gate_policy": {"local_reversible": "grant"},
                                    "budget": {}, "stop_on": [],
                                    "expires_at": _in_one_day(),
                                    "system_one": {"allowed": False}, "revoked": False},
                        "assertions": [{"test": "grant-accepted", "assertedBy": assertedBy,
                                        "result": {"outcome": "passed"},
                                        "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                                                    "--unattended", "docs/spec.md"]}]}}
    d = os.path.join(root, ".skill-contract", "envelopes")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, gid + ".json"), "w", encoding="utf-8") as f:
        json.dump(st, f)


def _check_grant(root):
    checker = os.path.join(SKILL_ROOT, "assets", "contract_check.py")  # SKILL_ROOT: the eval's skill dir
    r = subprocess.run([sys.executable, "-I", checker, "check-grant", "--root", root,
                        "--action", "local_reversible"], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout


with tempfile.TemporaryDirectory() as t:
    rc, out = _check_grant(t)
    check("NEGATIVE: no grant -> the gate must ask", rc == 3 and "GRANT: NONE" in out, out)
with tempfile.TemporaryDirectory() as t:
    _grant_fixture(t, {"human": "Dana"})
    rc, out = _check_grant(t)
    check("a human-accepted grant covers local_reversible", rc == 0 and "GRANT: COVERED" in out, out)
with tempfile.TemporaryDirectory() as t:
    _grant_fixture(t, {"skill": "spec-first-planning"})
    rc, out = _check_grant(t)
    check("NEGATIVE: a skill-attributed grant is invalid", rc == 2 and "GRANT: INVALID" in out, out)
skill_md = open(os.path.join(SKILL_ROOT, "SKILL.md"), encoding="utf-8").read()
check("the gate text calls check-grant with an action class",
      "check-grant" in skill_md and "--action" in skill_md, "")
```

   The temp dirs are outside any git repo, so the branch check is skipped. Use the eval's own name for its skill-directory constant instead of `SKILL_ROOT` if it has one.
6. **BCP 14.** Add a classification row for each new or changed keyworded sentence, update the counts, and confirm that PP-5 is not newly advisory and PP-7 passes.

- [ ] **Step 1:** Add the eval checks for all three skills. Run each eval. Expected: FAIL, because there's no `assets/contract_check.py` or gate text yet.
- [ ] **Step 2:** Make the frontmatter, Contract and gate-text edits, then run `make contract-vendor`.
- [ ] **Step 3:** Run `make gate-skill SKILL=verifier-installer`, `make gate-skill SKILL=test-safety-net` and `make gate-skill SKILL=crafting-self-prompting-loops`. Expected: all PASS, including SKILL_CONTRACT (vendored copies match).
- [ ] **Step 4:** Commit, one commit per skill, e.g. `feat(verifier-installer): adopt skill-contract; write gate honours an autonomy grant`, each with the trailer.

---

### Task 8: End-to-end grant test

**Files:**
- Modify: `docs/skill-contract/reference/test_e2e.py`: add a `GrantE2ETests` class, reusing `HandoffTests`' setUp pattern (the universe copy, repo tmp and env).

**Interfaces:**
- Consumes the real scripts in the copied universe: `spec_to_tasks.py --envelope`, `write_grant.py` and `contract_check.py check-grant|revoke-grant`.

- [ ] **Step 1: Write the test.** Add this to `test_e2e.py`, after `HandoffTests`. `REPO`, `PRODUCER`, `CONSUMER` and `quoted_python` already exist in the module:

```python
GRANT_SPEC = """# Spec: Export

## Problem
Users cannot export rows.

## Users
- analysts

## Goals
- export works

## Non-goals
- PDF

## Constraints
- B1 [invariant]: No row is lost.
- T1 [boundary]: Export finishes within 10 s for 10000 rows.

## Required truths
- RT1 [SPECIFICATION_READY]: Every row reaches the file. (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: python3 -m pytest -k rows)
- RT2 [SPECIFICATION_READY]: The writer streams. (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.6; check: python3 bench.py --max 10)

## Requirements
- R1: The export must include every row.

## Acceptance criteria
- R1: run `python3 -m pytest -k rows`, expect exit 0.

## Open questions

## Tensions
- TN1 [trade_off]: Streaming vs. atomic write. (between: B1, T1; status: resolved; strategy: Partition)

## Solution options
- OPT-A: Stream rows to a temp file, rename at end. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)
- OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)
Recommended: OPT-A — satisfies every RT at the lowest complexity.

## Iterations
- I1: constrained, tensioned, anchored; chose OPT-A.

## Decisions
- D1: May the export add a dependency? -> no (source: sweep)
"""
ANSWERS = {"branch_pattern": "*", "gate_policy": {"read_only": "auto", "local_reversible": "grant"},
           "expires_at": _in_one_day()}


class GrantE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc-grant-e2e-")
        self.universe = os.path.join(self.tmp, "skills")
        for name in (PRODUCER, CONSUMER):
            shutil.copytree(os.path.join(REPO, name), os.path.join(self.universe, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "eval"))
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "docs"))
        self.spec = os.path.join(self.repo, "docs", "spec.md")
        with open(self.spec, "w", encoding="utf-8", newline="\n") as f:
            f.write(GRANT_SPEC)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.env = dict(os.environ, SKILL_CONTRACT_PATH=self.universe, HOME=home, USERPROFILE=home,
                        SKILL_CONTRACT_PYTHON=quoted_python(), PYTHONDONTWRITEBYTECODE="1",
                        SKILL_CONTRACT_ALLOWED_SIGNERS="")
        self.assets = os.path.join(self.universe, PRODUCER, "assets")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, script, *args):
        return subprocess.run([sys.executable, "-I", os.path.join(self.assets, script), *args],
                              capture_output=True, text=True, timeout=120, cwd=self.repo, env=self.env)

    def grant(self, answers=None):
        r = self.run_py("spec_to_tasks.py", self.spec, "--envelope", self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines() if ln.startswith("ENVELOPE: ")][0]
        ans = os.path.join(self.tmp, "answers.json")
        with open(ans, "w", encoding="utf-8") as f:
            json.dump(answers or ANSWERS, f)
        r = self.run_py("write_grant.py", "--root", self.repo, "--spec", "docs/spec.md",
                        "--plan", plan, "--answers", ans, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln[len("GRANT: "):] for ln in r.stdout.splitlines() if ln.startswith("GRANT: ")][0]

    def check(self, action, *extra):
        r = self.run_py("contract_check.py", "check-grant", *extra, "--root", self.repo,
                        "--action", action)
        return r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""

    def test_a_grant_covers_the_handoff_class_and_not_merge(self):
        self.grant()
        self.assertEqual(self.check("local_reversible")[0], 0)
        rc, last = self.check("merge")
        self.assertEqual(rc, 3)
        self.assertIn("reason=gate-ask", last)

    def test_b_revoke_makes_the_same_check_ask(self):
        self.grant()
        r = self.run_py("contract_check.py", "revoke-grant", "--root", self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=revoked", last)

    def test_c_a_skill_attributed_copy_is_invalid(self):
        path = self.grant()
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st["predicate"]["assertions"][0]["assertedBy"] = {"skill": "spec-first-planning"}
        st["predicate"]["id"] = st["predicate"]["id"][:-6] + "ffffff"
        forged = os.path.join(os.path.dirname(path), st["predicate"]["id"] + ".json")
        with open(forged, "w", encoding="utf-8") as f:
            json.dump(st, f)
        rc, last = self.check("local_reversible", forged)
        self.assertEqual(rc, 2)
        self.assertIn("GRANT: INVALID", last)

    def test_d_editing_the_spec_makes_the_grant_stale(self):
        self.grant()
        with open(self.spec, "a", encoding="utf-8") as f:
            f.write("\n<!-- edited after the grant -->\n")
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=stale", last)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_e_a_branch_outside_the_pattern_asks(self):
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "checkout", "-q", "-b", "other/x"], check=True)
        self.grant(dict(ANSWERS, branch_pattern="factory/*"))
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=branch", last)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_f_the_default_branch_is_never_covered(self):
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        self.grant()  # branch_pattern "*" would match main, but the A7 floor wins
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=default-branch", last)

    def test_g_a_grant_for_merge_is_refused(self):
        r = self.run_py("spec_to_tasks.py", self.spec, "--envelope", self.repo)
        plan = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines() if ln.startswith("ENVELOPE: ")][0]
        ans = os.path.join(self.tmp, "answers.json")
        with open(ans, "w", encoding="utf-8") as f:
            json.dump(dict(ANSWERS, gate_policy={"local_reversible": "grant", "merge": "grant"}), f)
        r = self.run_py("write_grant.py", "--root", self.repo, "--spec", "docs/spec.md",
                        "--plan", plan, "--answers", ans, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED:", r.stdout)
```

  `GRANT_SPEC` is Task 4's `FULL` text, verbatim: it passes `spec_lint.py --unattended`.

- [ ] **Step 2:** Run `cd docs/skill-contract/reference && python3 -I test_e2e.py`. Expected: the new tests fail only if an earlier task is incomplete. Fix forward within the scope of Tasks 1–5 only if a real bug shows up, and report it.
- [ ] **Step 3:** Run `make contract`. Expected: PASS.
- [ ] **Step 4:** Commit with `test(skill-contract): end-to-end autonomy grant — covered, revoked, forged, stale, branch` and the trailer.

---

### Task 9: A/B rows, README and assessment status, full gate

**Files:**
- Modify: `scripts/ab-validate.py`: add `SINCE_AUTONOMY_GRANT` and `check_autonomy_grant(old, new)`, called after `check_factory_trust_ba`.
- Modify: `README.md` (the Software factory section: unattended status and a recipe).
- Modify: `docs/factory/2026-09-19-assessment.md`: add a bullet under "Since this assessment".

**Interfaces:**
- Consumes: the `row()` helper conventions in `scripts/ab-validate.py`. Read how `check_factory_trust_ba` is written and mirror it.

- [ ] **Step 1: Add the rows.**
  - Set `SINCE_AUTONOMY_GRANT = "<hash>"`, where `<hash>` is the output of `git log --format=%h --grep="autonomy-grant/v1 — check-grant" -1` (the Task 1 commit). Put it next to the other `SINCE_*` constants, with a one-line comment.
  - Add this after `check_factory_trust_ba`, and call `check_autonomy_grant(old, REPO)` after `check_factory_trust_ba(old, REPO)` in `main()`:

```python
_AG_SKILLS = ("spec-first-planning", "crafting-self-prompting-loops", "verifier-installer",
              "test-safety-net")


def check_autonomy_grant(old, new):
    def gates(tree):
        n = 0
        for sk in _AG_SKILLS:
            p = os.path.join(tree, sk, "SKILL.md")
            if os.path.isfile(p) and "check-grant" in open(p, encoding="utf-8").read():
                n += 1
        return n

    def adopters(tree):
        return sum(1 for d in sorted(os.listdir(tree))
                   if os.path.isfile(os.path.join(tree, d, "SKILL.md"))
                   and re.search(r"(?m)^## Contract\s*$",
                                 open(os.path.join(tree, d, "SKILL.md"), encoding="utf-8").read()))

    def checker(tree):
        return os.path.join(tree, "docs", "skill-contract", "reference", "contract_check.py")

    def decides(tree):
        c = checker(tree)
        return 1 if os.path.isfile(c) and '"check-grant"' in open(c, encoding="utf-8").read() else 0

    def forged_accepted(tree):
        if not decides(tree):
            return 0
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "docs"))
        spec = "# Spec\n"
        open(os.path.join(root, "docs", "spec.md"), "w").write(spec)
        gid = "autonomy-grant-v1-20260919T120000Z-a1b2c3"
        st = {"_type": "https://in-toto.io/Statement/v1",
              "subject": [{"name": "docs/spec.md",
                           "digest": {"sha256": hashlib.sha256(spec.encode()).hexdigest()}}],
              "predicateType": "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1",
              "predicate": {"skillContract": "1", "id": gid,
                            "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
                            "generatedAtTime": _now_z(), "wasRevisionOf": None,
                            "payload": {"scope": {"repo": ".", "branch_pattern": "*"},
                                        "decisions": [], "defaults": [],
                                        "gate_policy": {"local_reversible": "grant"},
                                        "budget": {}, "stop_on": [],
                                        "expires_at": _in_one_day(),
                                        "system_one": {"allowed": False}, "revoked": False},
                            "assertions": [{"test": "grant-accepted",
                                            "assertedBy": {"skill": "spec-first-planning"},
                                            "result": {"outcome": "passed"},
                                            "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                                                        "--unattended", "docs/spec.md"]}]}}
        d = os.path.join(root, ".skill-contract", "envelopes")
        os.makedirs(d)
        json.dump(st, open(os.path.join(d, gid + ".json"), "w"))
        r = subprocess.run([sys.executable, "-I", checker(tree), "check-grant", "--root", root,
                            "--action", "local_reversible"], capture_output=True, text=True, timeout=60)
        return 1 if r.returncode == 0 else 0

    s = "skill-contract"
    a, b = gates(old), gates(new)
    row(s, "skills whose human gate calls check-grant", a, b, b == 4 and a == 0,
        "no skill could proceed past its confirmation under a user-approved grant",
        since=SINCE_AUTONOMY_GRANT)
    a, b = adopters(old), adopters(new)
    row(s, "skill-contract adopters", a, b, b > a,
        "verifier-installer and test-safety-net adopt the contract to read grants",
        since=SINCE_AUTONOMY_GRANT)
    a, b = decides(old), decides(new)
    row(s, "reference checker decides whether a grant covers an action", a, b, b == 1 and a == 0,
        "commandment 10's 'unless' had no format or check", since=SINCE_AUTONOMY_GRANT)
    a, b = forged_accepted(old), forged_accepted(new)
    row(s, "skill-attributed (self-certified) grants accepted", a, b, a == 0 and b == 0,
        "a grant only counts when a human accepted it", kind="guard")
```

  Check that `hashlib`, `json`, `re`, `tempfile`, `subprocess` and `sys` are imported at the top of the script. Most already are; add any that are missing.

- [ ] **Step 2: README.** In the Software factory section, replace the "Unattended mode: not yet" paragraph with an honest status:
  - spec-first-planning 2.0.0 can run unattended planning and write an autonomy grant after your yes;
  - four skills' gates honour it;
  - merge, deploy, spend, external messages and deletes always ask you;
  - the conductor that runs a whole plan is still roadmap step 4.
  
  Add a recipe line: `npx skills add dhanesh/agent-skills --skill spec-first-planning --skill crafting-self-prompting-loops --skill verifier-installer --skill test-safety-net`. Run `make readme`; expected PASS.
- [ ] **Step 3: Assessment.** Add a bullet: "Step 3A (autonomy grant) landed: grant kind + check-grant; spec-first-planning 2.0.0; 4 adopters; Q3 to be re-judged by Jev after merge."
- [ ] **Step 4:** Run `make gate > <scratch>/gate.log 2>&1; tail -1 <scratch>/gate.log` and `make ab-validate | tail -3`. Expected: `GATE_RESULT: PASS` and `AB_RESULT: PASS`, with 0 worse and 0 unproven.
- [ ] **Step 5:** Commit with `test(ab-validate): autonomy-grant rows; README and assessment status` and the trailer.

---

## Self-review notes

- **Spec coverage:**
  - §1 grant fields: Tasks 1 and 5.
  - §2 classes: Task 1 (SPEC and checker).
  - §3 `check-grant`: Tasks 1–2.
  - §4 planning loop and modes: Tasks 3, 4 and 6.
  - §4 unattended grant writing: Tasks 5–6.
  - §5 adopters and gates: Task 7.
  - §6 SPEC: Task 1.
  - §7 acceptance criteria:
    - AC1: Task 1.
    - AC2: Task 2.
    - AC3: Tasks 3, 4 and 6.
    - AC4: Task 8.
    - AC5: Task 7.
    - AC6: Task 9.
    - AC7: Tasks 5–7.
    - AC8: post-merge (the controller runs Jev; out of plan).
- **Names used across tasks:** `check_grant`, `grant_violations`, `latest_grant`, `revoke_grant`, `GRANT_KIND`, `ACTION_CLASSES`, `lint(text, mode=...)`, `parse_spec` keys `constraints`/`truths`/`tensions`/`options`/`recommended`/`iterations`/`decisions`, `build_grant`, `GrantRefused`.
- **Known judgment calls for implementers:**
  - the exact issue wording (tests check ids and keywords, not full sentences);
  - the eval fixture layout in each skill;
  - how test-safety-net resolves `$SKILL_DIR` for the vendored checker.
