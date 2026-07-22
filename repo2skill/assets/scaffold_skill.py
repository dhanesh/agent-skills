#!/usr/bin/env python3
"""scaffold_skill.py — generate a gate-passing Agent Skill skeleton.

The generator half of the repo2skill authoring skill. Given a kebab-case
skill name, it writes a complete skeleton directory:

    <name>/
      SKILL.md                     standard frontmatter (license, compatibility,
                                   metadata: author/version/tags) + a body already
                                   shaped to pass the structural gates, with
                                   TODO(repo2skill) markers where real content goes
      README.md                    human-facing overview stub
      references/overview.md       progressive-disclosure stub
      assets/test_<snake>_smoke.py runnable stdlib smoke suite
      eval/run_eval.py             outcome-eval stub per docs/eval-standard.md
                                   (CHECK/EVAL_RESULT protocol, negative fixture)

The skeleton is designed to pass validate-skill.sh, prompting-playbook.sh, and
frontmatter-standard.sh out of the box, so the author starts from green and
edits under gate cover instead of debugging structure later.

Stdlib only, offline, deterministic (no timestamps): the same invocation
produces byte-identical output on every run.

Usage:
    python3 scaffold_skill.py <name> [--dir DIR] [--author NAME]
                              [--version X.Y.Z] [--tags "a,b,c"]

Exit codes: 0 success; 1 target already exists; 2 invalid name / usage.
"""
import argparse
import os
import re
import string
import sys

DEFAULT_AUTHOR = "dhanesh"
DEFAULT_VERSION = "1.0.0"
DEFAULT_TAGS = "TODO,comma,separated"

NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
MAX_NAME_LEN = 64


class ScaffoldError(Exception):
    """Raised when scaffolding is refused (invalid name, target exists)."""


def validate_name(name):
    """Return None if `name` is a valid skill name, else a human-readable reason.

    Mirrors the validate-skill.sh contract: kebab-case ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$,
    at most 64 characters.
    """
    if not name:
        return "name is empty"
    if len(name) > MAX_NAME_LEN:
        return "name exceeds %d characters (length=%d)" % (MAX_NAME_LEN, len(name))
    if not NAME_RE.match(name):
        return (
            "name %r is not kebab-case (must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$)"
            % name
        )
    return None


# ── Templates ────────────────────────────────────────────────────────────────
# string.Template ($name / $snake) so literal { } in generated Python survive.

SKILL_MD = string.Template('''\
---
name: $name
description: "TODO(repo2skill): one clause on what $name does and the outcome it produces. Use when the user says 'TODO trigger phrasing' or 'TODO another concrete phrasing'. Not for TODO-adjacent-job — use TODO-sibling-skill for that."
license: MIT
compatibility: TODO(repo2skill) one-liner on runtime requirements (default assumption python3, stdlib only, offline).
metadata:
  author: $author
  version: "$version"
  tags: "$tags"
---

# $name

TODO(repo2skill): one paragraph stating what this skill does for the agent that loads
it and the outcome it exists to produce. Write it as a reusable prompt, not
documentation — a system prompt the agent follows when the skill fires.

## When to use

TODO(repo2skill): the concrete situations that should trigger this skill, in the
user's own phrasings — and the boundary: the adjacent jobs it deliberately does not
do, naming the sibling skill that does each one.

## Workflow

1. **Scope.** TODO(repo2skill): the single first job — gather exactly the inputs the
   next step needs, and nothing more. State what was found in one line.
2. **Execute.** TODO(repo2skill): the core step that produces the deliverable named
   below. One job per step; offload reference detail to references/overview.md
   rather than inlining it here.
3. **Verify and repair.** Check the produced output against the success criteria
   below, fix what fails, and re-check until everything holds. State plainly what
   was verified and what was repaired.

## Deliverable

TODO(repo2skill): name the concrete output contract — the artifact, report, or file
set this skill produces, with the fixed fields or structure downstream consumers
rely on. "The output" is not a contract; a named shape is.

## Success criteria

TODO(repo2skill): the deterministic checks that decide the deliverable is done — the
evaluate half of the generate -> evaluate -> repair loop. The shipped smoke suite
(assets/test_${snake}_smoke.py) and outcome eval (eval/run_eval.py) are the seed:
grow them as the skill grows.
''')

README_MD = string.Template('''\
# $name

TODO(repo2skill): one-paragraph human-facing overview — what this skill does, why it
exists, and who should install it.

## Install

```bash
npx skills add $author/agent-skills --skill $name
```

## Usage

TODO(repo2skill): how an agent or person actually uses the skill once installed —
trigger phrasings, entry points, and what to expect back.

## Layout

- `SKILL.md` — the agent-facing prompt.
- `references/` — progressive-disclosure detail the SKILL.md links to.
- `assets/` — shipped tooling and its stdlib test suite.
- `eval/run_eval.py` — deterministic outcome eval (see the repo's docs/eval-standard.md).
''')

OVERVIEW_MD = string.Template('''\
# $name — reference overview

TODO(repo2skill): this file exists for progressive disclosure. Move depth here —
rubrics, long examples, per-case playbooks, parameter docs — and link it from
SKILL.md, so the prompt body stays a lean control surface. Delete this stub's
guidance once real reference content replaces it. If you document CLI flags or
parameters, do it here (as references/parameters.md), not in a top-level
PARAMETERS.md — that filename is reserved for template-placeholder bijection.
''')

SMOKE_TEST = string.Template('''\
#!/usr/bin/env python3
"""Smoke suite for $name. Stdlib unittest; no pip, no network, deterministic.

TODO(repo2skill): grow this into the skill's real unit suite as shipped tooling
lands in assets/. Run:  python3 test_${snake}_smoke.py
"""
import os
import re
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(SKILL_DIR, rel), encoding="utf-8") as f:
        return f.read()


class TestSkeleton(unittest.TestCase):
    def test_skill_md_and_readme_exist(self):
        for rel in ("SKILL.md", "README.md"):
            self.assertTrue(os.path.isfile(os.path.join(SKILL_DIR, rel)), rel)

    def test_frontmatter_name_matches_directory(self):
        text = read("SKILL.md")
        m = re.search(r"^name:\\s*(\\S+)\\s*$$", text, re.M)
        self.assertIsNotNone(m, "frontmatter has a name: field")
        self.assertEqual(m.group(1), os.path.basename(SKILL_DIR))

    def test_standard_metadata_fields_present(self):
        text = read("SKILL.md")
        for key in ("license:", "compatibility:", "author:", "version:", "tags:"):
            self.assertIn(key, text, key)

    def test_body_keeps_gate_passing_structure(self):
        body = read("SKILL.md").split("---", 2)[2]
        self.assertGreaterEqual(len(re.findall(r"^## ", body, re.M)), 3)
        self.assertGreaterEqual(len(re.findall(r"^[0-9]+\\. ", body, re.M)), 3)


if __name__ == "__main__":
    unittest.main()
''')

EVAL_STUB = string.Template('''\
#!/usr/bin/env python3
"""Outcome eval for $name (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; scratch writes go to a tempdir only.
Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.

TODO(repo2skill): this stub grades the skill's contract surface (frontmatter
shape plus body structure) with a model-free grader and a known-bad negative
fixture. Replace/extend it with an end-to-end harness -> skill tooling ->
grader pipeline once the skill ships real tooling — keep at least one
negative fixture; an eval that cannot fail is not an eval.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def frontmatter_errors(text):
    """Model-free grader: return a list of contract violations in a SKILL.md."""
    errors = []
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return ["missing opening ---"]
    try:
        close = lines[1:].index("---") + 1
    except ValueError:
        return ["missing closing ---"]
    fields = {}
    for ln in lines[1:close]:
        m = re.match(r"^([A-Za-z0-9_-]+):\\s*(.*)$$", ln)
        if m:
            fields[m.group(1)] = m.group(2).strip().strip("\\"").strip("'")
    name = fields.get("name", "")
    if len(name) > 64 or not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name or ""):
        errors.append("bad name: %r" % name)
    desc = fields.get("description", "")
    if not desc or len(desc) > 1024:
        errors.append("description missing, empty, or over 1024 characters")
    return errors


def main():
    with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()

    check("SKILL.md frontmatter contract holds", not frontmatter_errors(text))

    body = text.split("---", 2)[2] if text.count("---") >= 2 else ""
    check(
        "body keeps gate-passing structure (>=3 sections, >=3 steps)",
        len(re.findall(r"^## ", body, re.M)) >= 3
        and len(re.findall(r"^[0-9]+\\. ", body, re.M)) >= 3,
    )

    tmp = tempfile.mkdtemp(prefix="$snake-eval-")
    try:
        bad = os.path.join(tmp, "SKILL.md")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("---\\nname: Bad_Name\\ndescription:\\n---\\nbody\\n")
        with open(bad, encoding="utf-8") as f:
            bad_errors = frontmatter_errors(f.read())
        check(
            "grader rejects known-bad frontmatter fixture",
            len(bad_errors) >= 2,
            "; ".join(bad_errors)[:80],
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
''')


def build_files(name, author=DEFAULT_AUTHOR, version=DEFAULT_VERSION, tags=DEFAULT_TAGS):
    """Return {relative_path: content} for the skeleton. Pure and deterministic."""
    reason = validate_name(name)
    if reason:
        raise ScaffoldError(reason)
    snake = name.replace("-", "_")
    subs = {"name": name, "snake": snake, "author": author, "version": version, "tags": tags}
    return {
        "SKILL.md": SKILL_MD.substitute(subs),
        "README.md": README_MD.substitute(subs),
        os.path.join("references", "overview.md"): OVERVIEW_MD.substitute(subs),
        os.path.join("assets", "test_%s_smoke.py" % snake): SMOKE_TEST.substitute(subs),
        os.path.join("eval", "run_eval.py"): EVAL_STUB.substitute(subs),
    }


def scaffold(name, target_dir, author=DEFAULT_AUTHOR, version=DEFAULT_VERSION,
             tags=DEFAULT_TAGS):
    """Write the skeleton under <target_dir>/<name>; return the created path.

    Refuses to touch an existing skill directory — scaffolding is for new
    skills, authoring over an existing one is an edit job, not a re-generate.
    """
    files = build_files(name, author=author, version=version, tags=tags)
    dest = os.path.join(target_dir, name)
    if os.path.exists(dest):
        raise ScaffoldError("target already exists: %s" % dest)
    for rel, content in sorted(files.items()):
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scaffold_skill.py",
        description="Generate a gate-passing Agent Skill skeleton.",
    )
    parser.add_argument("name", help="skill name (kebab-case, <= 64 chars; becomes the directory name)")
    parser.add_argument("--dir", default=".", help="parent directory to create the skill under (default: cwd)")
    parser.add_argument("--author", default=DEFAULT_AUTHOR, help="metadata author (default: %(default)s)")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="metadata version (default: %(default)s)")
    parser.add_argument("--tags", default=DEFAULT_TAGS, help="metadata tags, comma-separated")
    args = parser.parse_args(argv)

    reason = validate_name(args.name)
    if reason:
        parser.error(reason)  # exits 2

    try:
        dest = scaffold(args.name, args.dir, author=args.author,
                        version=args.version, tags=args.tags)
    except ScaffoldError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    print("Scaffolded %s" % dest)
    print("Next: replace every TODO(repo2skill) marker, then run "
          "`make gate-skill SKILL=%s` from the repo root." % args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
