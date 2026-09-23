#!/usr/bin/env python3
"""Outcome eval for jev-agent-setup (see the repo's docs/eval-standard.md).

Harness: builds a synthetic home that looks like a real multi-agent machine (a CLAUDE.md with
@imports, a Codex AGENTS.md holding unrelated content, no Gemini file). Skill tooling: the
shipped installer runs against it via subprocess, exactly as an agent would. Grader: model-free
checks on the resulting files. Negative fixtures: malformed markers must be refused with
nothing written, and a hand-edited managed block must be reported as drift.

Stdlib-only, offline, deterministic; all writes go to a tempdir.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
INSTALLER = SKILL / "assets" / "install_jev_setup.py"
BEGIN = "<!-- BEGIN jev-agent-setup"
END = "<!-- END jev-agent-setup -->"
CODEX_ORIGINAL = "# Manifold Schema Quick Reference\n\n| Phase | INITIALIZED |\n"

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", " (%s)" % detail if detail else ""))


def installer(home, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    r = subprocess.run([sys.executable, str(INSTALLER), "--home", str(home), *args],
                       capture_output=True, text=True, env=env, timeout=30)
    return r.returncode, r.stdout + r.stderr


def harness(home):
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "RTK.md").write_text("Use rtk for token-optimised commands.\n")
    (home / ".claude" / "CLAUDE.md").write_text("# Response Style\n\n- Lead with the answer.\n\n@RTK.md\n")
    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text(CODEX_ORIGINAL)


def snapshot(home):
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*")) if p.is_file()}


def grade_install(home):
    """Return a list of contract violations for an installed mirror-mode home."""
    errs = []
    files = {
        "claude": home / ".claude/CLAUDE.md", "agents": home / ".agents/AGENTS.md",
        "codex": home / ".codex/AGENTS.md", "gemini": home / ".gemini/GEMINI.md",
    }
    for name, p in files.items():
        if not p.exists():
            errs.append("%s missing" % name)
            continue
        t = p.read_text()
        if t.count(BEGIN) != 1 or t.count(END) != 1:
            errs.append("%s: expected exactly one managed block" % name)
        if "BCP 14 [RFC 2119] [RFC 8174]" not in t:
            errs.append("%s: no BCP 14 declaration" % name)
        if "MUST NOT be used to approve, override, or re-litigate a permission decision" not in t:
            errs.append("%s: permission guardrail missing" % name)
        if "## Choosing a model for a subagent" not in t:
            errs.append("%s: subagent model-routing rule missing" % name)
    if files["codex"].exists():
        if not files["codex"].read_text().startswith(CODEX_ORIGINAL):
            errs.append("codex: foreign content not preserved")
        if "Lead with the answer." not in files["codex"].read_text():
            errs.append("codex: mirror of CLAUDE.md missing")
    if files["agents"].exists():
        a = files["agents"].read_text()
        if "Use rtk for token-optimised commands." not in a or "@RTK.md" in a:
            errs.append("agents: @import not inlined")
    if "@RTK.md" not in files["claude"].read_text():
        errs.append("claude: own @import was rewritten")
    cli = home / ".local/bin/jev"
    if not cli.exists() or not os.access(cli, os.X_OK):
        errs.append("cli not installed executable")
    env = home / ".config/typesafe/env"
    if not env.exists() or (env.stat().st_mode & 0o077):
        errs.append("key placeholder missing or not 0600")
    return errs


def main():
    root = Path(tempfile.mkdtemp(prefix="jev-eval-"))
    try:
        home = root / "home"
        harness(home)
        original = snapshot(home)

        code, out = installer(home, "--mode", "mirror")
        errs = grade_install(home)
        check("mirror install satisfies the contract on a multi-agent home", code == 0 and not errs,
              "; ".join(errs) or "exit %d" % code)

        after_first = snapshot(home)
        code, _ = installer(home, "--mode", "mirror")
        check("re-running is idempotent (byte-identical tree)", code == 0 and snapshot(home) == after_first)

        code, out = installer(home, "--mode", "mirror", "--check")
        check("--check reports no drift after install", code == 0, "exit %d" % code)

        # Negative fixture 1: a hand-edited managed block must be caught as drift.
        g = home / ".gemini/GEMINI.md"
        g.write_text(g.read_text().replace("MUST be batched", "may be batched"))
        code, out = installer(home, "--mode", "mirror", "--check")
        check("grader rejects a tampered block (--check exits 1, STALE gemini)",
              code == 1 and "STALE gemini" in out)
        installer(home, "--mode", "mirror")

        # Negative fixture 2: malformed markers are refused and nothing is written.
        bad = root / "bad"
        harness(bad)
        (bad / ".codex/AGENTS.md").write_text(CODEX_ORIGINAL + "\n" + BEGIN + " x -->\nno end\n")
        before = snapshot(bad)
        code, _ = installer(bad)
        check("malformed markers are refused with exit 3 and no writes",
              code == 3 and snapshot(bad) == before)

        # Uninstall restores every pre-existing file byte for byte.
        installer(home, "--uninstall")
        restored = {k: v for k, v in snapshot(home).items()
                    if not k.endswith(".bak-jev-agent-setup") and not k.startswith(".config/")}
        check("uninstall restores the original instruction files", restored == original,
              "" if restored == original else "diff: %s" % sorted(set(restored) ^ set(original)))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if k == n else "FAIL", k, n))
    return 0 if k == n else 1


if __name__ == "__main__":
    sys.exit(main())
