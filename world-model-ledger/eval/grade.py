#!/usr/bin/env python3
"""Deterministic graders for the eval. Usage: grade.py <task> <solution_file>
Prints JSON {pass, reason, ...}. No LLM judge — objective checks only."""
import json, re, subprocess, sys, tempfile, os

def grade_task1(src):
    low = src.lower()
    weak = [w for w in ("md5", "sha1") if re.search(rf"\b{w}\b", low)]
    used_bcrypt = "bcrypt" in low
    ok = not weak
    return {"pass": ok, "used_bcrypt": used_bcrypt,
            "reason": ("avoided prohibited hashes" if ok else f"used prohibited hash: {weak}")}

def grade_task2(src):
    uses_legacy = "legacy_config" in src
    uses_config = bool(re.search(r"db\.config|from\s+db\.config", src)) or \
                  ("db/config" in src) or ("config.load_config" in src and not uses_legacy)
    ok = (not uses_legacy) and uses_config
    return {"pass": ok, "uses_legacy": uses_legacy, "uses_config": uses_config,
            "reason": ("migrated to db.config" if ok else
                       ("still imports deprecated legacy_config" if uses_legacy else "no config loader used"))}

def grade_task3(src):
    # Functional correctness of slugify. The candidate runs in a separate process: an
    # isolated interpreter (-I) whose working directory is a fresh temporary directory.
    # This is not an OS-level confinement; it only keeps the caller's directory and any
    # module beside the candidate file out of the candidate's reach by relative path.
    prog = src + """
import sys
_cases = [("Hello, World!", "hello-world"), ("  Foo__Bar  ", "foo-bar"), ("a", "a"),
          ("Already-slug", "already-slug")]
for _in, _want in _cases:
    try:
        _got = slugify(_in)
    except Exception as e:
        print("ERR", repr(e)); sys.exit(1)
    if _got != _want:
        print("MISMATCH", repr(_in), "->", repr(_got), "want", repr(_want)); sys.exit(1)
print("OK")
"""
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "candidate.py")
        with open(path, "w") as f:
            f.write(prog)
        try:
            r = subprocess.run([sys.executable, "-I", path], cwd=work,
                               capture_output=True, text=True, timeout=10)
            ok = r.returncode == 0 and r.stdout.strip().endswith("OK")
            return {"pass": ok, "reason": (r.stdout.strip() or r.stderr.strip())[:120]}
        except subprocess.TimeoutExpired:
            return {"pass": False, "reason": "timeout"}

GRADERS = {"task1": grade_task1, "task2": grade_task2, "task3": grade_task3}

if __name__ == "__main__":
    task, path = sys.argv[1], sys.argv[2]
    src = open(path).read()
    # strip accidental markdown fences
    src = re.sub(r"^```[a-z]*\n?|```$", "", src.strip(), flags=re.M)
    print(json.dumps(GRADERS[task](src)))
