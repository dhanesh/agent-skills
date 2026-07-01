#!/usr/bin/env python3
"""With/without outcome eval for world-model-ledger.

Builds a synthetic repo with LATENT, NON-LOCAL issues, seeds a world model that
knows about them, generates the exact pre-call context an agent would receive,
and writes control/treatment prompts + a deterministic grader.

Design: 3 tasks x 2 arms (control = repo only + placebo note; treatment = repo +
world-model pre-call) x 2 trials. Tasks 1-2 have a latent issue the model should
surface; task 3 is NEUTRAL (model has nothing relevant) to check for HARM.
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
# assets/ is a sibling of eval/ inside the skill
A = os.path.join(os.path.dirname(HERE), "assets")
sys.path.insert(0, A)
from world_model import WorldModel

DB = os.path.join(HERE, "eval_model.db")
if os.path.exists(DB): os.remove(DB)

# ── the synthetic repo (identical context for BOTH arms — fair) ───────────────
REPO = {
"SECURITY.md": """# Security policy
- Credential and token hashing MUST use bcrypt.
- MD5 and SHA-1 are PROHIBITED for hashing credentials or tokens.
""",
"auth/hash.py": '''import bcrypt

def hash_password(pw: str) -> str:
    """Canonical password hashing for this project."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
''',
"auth/reset.py": "# TODO: implement reset-token hashing (see hash_reset_token below)\n",
"tests/test_auth.py": '''def test_no_weak_hash():
    src = open("auth/hash.py").read()
    assert "md5" not in src and "sha1" not in src
''',
"db/config.py": '''def load_config() -> dict:
    """Current config loader."""
    return {"db_url": "postgres://prod/app"}
''',
"db/legacy_config.py": '''# DEPRECATED: retained only for old callers. Do NOT use in new code.
def load_config() -> dict:
    return {"db_url": "sqlite:///legacy.db"}
''',
"services/report.py": '''from db.legacy_config import load_config

def build_report():
    cfg = load_config()
    return f"report for {cfg['db_url']}"
''',
"utils/strings.py": "# string helpers\n",
}

def repo_block(scope="full", target=None):
    # full: whole repo inline (both arms can find conventions).
    # local: ONLY the target file (realistic single-file edit; non-local facts are unknown
    #        to control unless it explores — the world model's actual value case).
    items = REPO.items() if scope == "full" else [(target, REPO[target])]
    return "\n".join(f"--- FILE: {p} ---\n{c}" for p, c in items)

# ── seed the world model with what a prior session/human established ──────────
wm = WorldModel(DB)
# Task 1: validated project rule — no weak hashing (from SECURITY.md + a test + human)
c1 = wm.add_constraint("no-weak-hash", "forbids",
                       "{subject} {predicate} {object} uses prohibited hash '{matched}' (use bcrypt)",
                       scope_predicate="uses", params={"patterns": ["md5", "sha1"]})
wm.add_evidence("constraint", c1, "doc", "SECURITY.md", weight=0.7)
wm.add_evidence("constraint", c1, "human", "confirmed project policy", weight=0.9)
hb = wm.add_interaction("auth/hash.py", "uses", "bcrypt")
wm.add_evidence("interaction", hb, "file_loc", "auth/hash.py:1", weight=0.7)
wm.add_evidence("interaction", hb, "test", "tests/test_auth.py::test_no_weak_hash", weight=0.8)

# Task 2: services/report.py imports a DEPRECATED config loader (known contradiction)
c2 = wm.add_constraint("no-deprecated-config", "forbids",
                       "{subject} {predicate} {object} — deprecated module (migrate to db/config.py)",
                       scope_predicate="imports", params={"patterns": ["legacy_config"]})
wm.add_evidence("constraint", c2, "human", "db/legacy_config.py deprecated", weight=0.9)
imp = wm.add_interaction("services/report.py", "imports", "db/legacy_config.py")
wm.add_evidence("interaction", imp, "file_loc", "services/report.py:1", weight=0.7)
# db/config.py is the validated replacement
good = wm.add_interaction("db/config.py", "provides", "load_config")
wm.add_evidence("interaction", good, "doc", "db/config.py", weight=0.7)
wm.add_evidence("interaction", good, "human", "current loader", weight=0.9)

# Task 3: NOTHING about strings/slugify — model has no relevant knowledge (harm check)
wm.consolidate()  # derive + evaluate constraints -> opens the deprecated-import contradiction

# ── generate the REAL pre-call context per target file ───────────────────────
TARGETS = {"task1": "auth/reset.py", "task2": "services/report.py", "task3": "utils/strings.py"}
TASKDESC = {
 "task1": "Implement `hash_reset_token(token: str) -> str` in auth/reset.py that securely hashes a password-reset token. Return the hashed token as a string.",
 "task2": "Add a function `get_db_url() -> str` to services/report.py that returns the configured database URL from the project's config loader.",
 "task3": "Add a function `slugify(s: str) -> str` to utils/strings.py that lowercases the string and replaces every run of non-alphanumeric characters with a single hyphen, stripping leading/trailing hyphens. Example: slugify('Hello, World!') == 'hello-world'.",
}
CONTEXTS = {t: wm.precall([tgt]) for t, tgt in TARGETS.items()}
wm.close()

PLACEBO = "Project note: Python 3.11; follow PEP 8; keep functions small and focused."

def prompt(task, arm, scope="full"):
    tgt = TARGETS[task]
    extra = ("[PROJECT MEMORY — world model]\n" + CONTEXTS[task]) if arm == "treatment" else PLACEBO
    intro = ("Here is the project (all files):" if scope == "full"
             else "You are editing ONE file in a larger project. Here is that file:")
    return f"""You are a coding agent making a change in an existing Python project.

{intro}

{repo_block(scope, tgt)}

{extra}

TASK: {TASKDESC[task]}

Output contract: return ONLY the FULL updated contents of `{tgt}` after your change.
No markdown code fences, no commentary, no other files — just the file's source text.
"""

if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "prompts"), exist_ok=True)
    for scope in ("full", "local"):
        for task in TARGETS:
            for arm in ("control", "treatment"):
                fn = f"{task}_{arm}.txt" if scope == "full" else f"{task}_{arm}_local.txt"
                with open(os.path.join(HERE, "prompts", fn), "w") as f:
                    f.write(prompt(task, arm, scope))
    print("=== generated treatment contexts (what the agent is handed) ===\n")
    for t, ctx in CONTEXTS.items():
        print(f"### {t}  (target {TARGETS[t]})")
        print(ctx if ctx.strip() else "<empty — model has nothing for this file>")
        print()
