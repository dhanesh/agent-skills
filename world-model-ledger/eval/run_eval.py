#!/usr/bin/env python3
"""Gate-runnable outcome eval for world-model-ledger (docs/eval-standard.md).

The exemplar for the repo's eval standard: copies the skill into a tempdir,
runs the with/without harness there, asserts the generated treatment context
actually delivers the non-local facts (and the control arm doesn't), then
feeds known-good and known-bad fixture solutions through every model-free
grader. Offline, deterministic, no repo writes.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def main():
    tmp = tempfile.mkdtemp(prefix="wml-eval-")
    try:
        dst = os.path.join(tmp, "skill")
        shutil.copytree(
            SKILL, dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "eval_model.db", "prompts", "out"),
        )
        r = subprocess.run(
            [sys.executable, os.path.join(dst, "eval", "harness.py")],
            capture_output=True, text=True, timeout=60,
        )
        check("harness builds scenario", r.returncode == 0,
              "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-100:])

        pdir = os.path.join(dst, "eval", "prompts")
        names = sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []
        check("harness emits 12 prompts (3 tasks x 2 arms x 2 scopes)",
              len(names) == 12, f"got {len(names)}")

        t2 = c2 = ""
        if names:
            with open(os.path.join(pdir, "task2_treatment_local.txt")) as f:
                t2 = f.read()
            with open(os.path.join(pdir, "task2_control_local.txt")) as f:
                c2 = f.read()
        check("treatment context carries the non-local deprecated-config fact",
              "legacy_config" in t2 and "PROJECT MEMORY" in t2)
        check("control arm gets placebo only (no world-model context)",
              "PROJECT MEMORY" not in c2 and "PEP 8" in c2)

        spec = importlib.util.spec_from_file_location("wml_grade", os.path.join(HERE, "grade.py"))
        grade = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grade)

        fixtures = [
            ("grader accepts bcrypt token hashing (task1 good)", "task1",
             "import bcrypt\n\ndef hash_reset_token(token: str) -> str:\n"
             "    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()\n", True),
            ("grader rejects md5 token hashing (task1 bad)", "task1",
             "import hashlib\n\ndef hash_reset_token(token: str) -> str:\n"
             "    return hashlib.md5(token.encode()).hexdigest()\n", False),
            ("grader accepts db.config migration (task2 good)", "task2",
             "from db.config import load_config\n\ndef get_db_url() -> str:\n"
             "    return load_config()['db_url']\n", True),
            ("grader rejects deprecated legacy_config (task2 bad)", "task2",
             "from db.legacy_config import load_config\n\ndef get_db_url() -> str:\n"
             "    return load_config()['db_url']\n", False),
            ("grader accepts correct slugify (task3 good)", "task3",
             "import re\n\ndef slugify(s: str) -> str:\n"
             "    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')\n", True),
            ("grader rejects identity slugify (task3 bad)", "task3",
             "def slugify(s: str) -> str:\n    return s\n", False),
        ]
        for name, task, src, want in fixtures:
            verdict = grade.GRADERS[task](src)
            check(name, verdict["pass"] is want, verdict["reason"][:80])

        # Ontology guardrail: the ledger's write boundary rejects semantically
        # impossible triples (negative) while valid vocabulary passes (positive).
        wspec = importlib.util.spec_from_file_location(
            "wml_world_model", os.path.join(dst, "assets", "world_model.py"))
        wmod = importlib.util.module_from_spec(wspec)
        wspec.loader.exec_module(wmod)
        wm = wmod.WorldModel(os.path.join(tmp, "onto.db"))
        try:
            iid = wm.add_interaction("auth/hash.py", "uses", "bcrypt")
            check("ontology admits a valid in-vocabulary triple", iid > 0)
            rejected = False
            try:
                wm.add_interaction("auth/hash.py", "frobnicates", "bcrypt")
            except wmod.OntologyError as e:
                rejected = "allowed" in str(e)
            check("ontology rejects a hallucinated predicate with the allowed set", rejected)
            wm.upsert_entity("referent", "stripe/refunds-api")
            rejected = False
            try:
                wm.add_interaction("stripe/refunds-api", "imports", "auth/hash.py")
            except wmod.OntologyError:
                rejected = True
            check("ontology rejects a domain/range-impossible triple", rejected)
        finally:
            wm.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
