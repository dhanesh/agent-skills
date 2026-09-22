#!/usr/bin/env python3
"""Install the Jev System One setup for coding agents on this machine.

Installs, idempotently:
  * the `jev` CLI (PEP 723 script, run by uv) into --bin-dir (default ~/.local/bin)
  * a managed Jev instruction block into each agent's global instruction file
  * a placeholder ~/.config/typesafe/env (0600) if none exists — never the key itself

Targets:  claude  ~/.claude/CLAUDE.md          agents  ~/.agents/AGENTS.md
          codex   $CODEX_HOME/AGENTS.md (~/.codex)   gemini  ~/.gemini/GEMINI.md

--mode block   every target gets only the managed Jev block (default)
--mode mirror  claude gets the block; the other targets get a managed copy of the whole
               CLAUDE.md (read from --claude-source, @imports inlined), so every agent
               follows the same global instructions

Content outside the markers is never touched. A file is backed up once, to
<file>.bak-jev-agent-setup, before its first modification. Stdlib only.

Exit: 0 ok · 1 --check found drift · 2 usage · 3 a target is malformed (nothing written)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
CLI_SRC = ASSETS / "jev"
BLOCK_SRC = ASSETS / "jev-instructions.md"

BEGIN = "<!-- BEGIN jev-agent-setup (managed by the jev-agent-setup skill; re-run its installer instead of editing) -->"
END = "<!-- END jev-agent-setup -->"
BEGIN_RE = re.compile(r"^<!-- BEGIN jev-agent-setup\b.*-->$", re.M)
END_RE = re.compile(r"^<!-- END jev-agent-setup -->$", re.M)
IMPORT_RE = re.compile(r"^@(\S+)[ \t]*$", re.M)
TARGETS = ("claude", "agents", "codex", "gemini")
ENV_PLACEHOLDER = (
    "# TypeSafe (Jev) API key: https://console.typesafe.ai/\n"
    "# Replace the placeholder below. Never commit this file or copy it into a repo.\n"
    "TYPESAFE_API_KEY=replace-me\n"
)


class Malformed(Exception):
    pass


def target_paths(home: Path, env: dict) -> dict[str, Path]:
    codex_home = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"
    return {
        "claude": home / ".claude" / "CLAUDE.md",
        "agents": home / ".agents" / "AGENTS.md",
        "codex": codex_home / "AGENTS.md",
        "gemini": home / ".gemini" / "GEMINI.md",
    }


def split_managed(text: str) -> tuple[str, str | None, str]:
    """Return (before, managed_body_or_None, after). Raise Malformed on broken markers."""
    begins, ends = list(BEGIN_RE.finditer(text)), list(END_RE.finditer(text))
    if not begins and not ends:
        return text, None, ""
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].end():
        raise Malformed(f"{len(begins)} BEGIN / {len(ends)} END markers, or END before BEGIN")
    b, e = begins[0], ends[0]
    return text[: b.start()], text[b.end() : e.start()].strip("\n"), text[e.end() :]


def render(before: str, body: str, after: str) -> str:
    before = before.rstrip("\n")
    after = after.lstrip("\n")
    block = f"{BEGIN}\n{body.strip()}\n{END}\n"
    out = (before + "\n\n" if before else "") + block
    return out + ("\n" + after if after.strip() else "")


def strip_managed(text: str) -> str:
    before, body, after = split_managed(text)
    if body is None:
        return text
    rest = before.rstrip("\n") + ("\n\n" + after.lstrip("\n") if after.strip() else "\n")
    return rest if rest.strip() else ""


def inline_imports(text: str, base: Path, seen: frozenset = frozenset()) -> str:
    """Inline Claude-style `@path` import lines; unresolved ones become a visible comment."""
    def sub(m: re.Match) -> str:
        raw = m.group(1)
        p = Path(os.path.expanduser(raw))
        p = p if p.is_absolute() else base / p
        if p in seen or not p.is_file():
            return f"<!-- unresolved import: {raw} -->"
        return inline_imports(p.read_text(encoding="utf-8").strip(), p.parent, seen | {p})
    return IMPORT_RE.sub(sub, text)


def mirror_body(claude_text: str, claude_base: Path) -> str:
    before, body, after = split_managed(claude_text)
    flat = "\n\n".join(x.strip("\n") for x in (before, body or "", after) if x.strip())
    header = ("This is a managed mirror of ~/.claude/CLAUDE.md for non-Claude agents; "
              "@imports are inlined. Change CLAUDE.md, then re-run the jev-agent-setup installer.")
    return header + "\n\n" + inline_imports(flat, claude_base).strip()


def write_file(path: Path, content: str, dry: bool, log: list) -> None:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        log.append(f"UNCHANGED {path}")
        return
    if dry:
        log.append(f"WOULD-WRITE {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = path.with_name(path.name + ".bak-jev-agent-setup")
    if old is not None and not bak.exists():
        shutil.copy2(path, bak)
    tmp = path.with_name(path.name + ".tmp-jev")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    log.append(f"WROTE {path}")


def desired(name: str, current: str, block: str, mode: str, claude_text: str, claude_base: Path) -> str:
    before, _, after = split_managed(current)
    body = block if (mode == "block" or name == "claude") else mirror_body(claude_text, claude_base)
    return render(before, body, after)


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    env = dict(os.environ if env is None else env)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", default=",".join(TARGETS), help="comma list of: " + ", ".join(TARGETS))
    ap.add_argument("--mode", choices=("block", "mirror"), default="block")
    ap.add_argument("--home", type=Path, default=Path(env.get("HOME", "~")).expanduser())
    ap.add_argument("--bin-dir", type=Path, help="where to install jev (default <home>/.local/bin)")
    ap.add_argument("--claude-source", type=Path, help="CLAUDE.md to mirror from (default the claude target)")
    ap.add_argument("--no-cli", action="store_true", help="skip installing the jev CLI")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--check", action="store_true", help="report drift, write nothing")
    g.add_argument("--uninstall", action="store_true")
    a = ap.parse_args(argv)

    names = [t.strip() for t in a.targets.split(",") if t.strip()]
    bad = [t for t in names if t not in TARGETS]
    if bad:
        print(f"ERROR unknown target(s): {', '.join(bad)}", file=sys.stderr)
        return 2
    paths = target_paths(a.home, env)
    bin_dir = a.bin_dir or a.home / ".local" / "bin"
    cli_dst = bin_dir / "jev"
    block = BLOCK_SRC.read_text(encoding="utf-8")
    log: list[str] = []

    # Validate every target before writing any of them.
    try:
        for n in names:
            if paths[n].exists():
                split_managed(paths[n].read_text(encoding="utf-8"))
    except Malformed as e:
        print(f"ERROR {paths[n]}: malformed jev-agent-setup markers ({e}); fix by hand, nothing written",
              file=sys.stderr)
        return 3

    if a.uninstall:
        for n in names:
            p = paths[n]
            if p.exists():
                new = strip_managed(p.read_text(encoding="utf-8"))
                if new == "":
                    p.unlink()
                    log.append(f"REMOVED {p}")
                else:
                    write_file(p, new, False, log)
        if not a.no_cli and cli_dst.exists() and cli_dst.read_bytes() == CLI_SRC.read_bytes():
            cli_dst.unlink()
            log.append(f"REMOVED {cli_dst}")
        print("\n".join(log))
        return 0

    # claude is rendered first so a mirror in the same run reflects its new block.
    claude_path = paths["claude"]
    claude_text = claude_path.read_text(encoding="utf-8") if claude_path.exists() else ""
    if "claude" in names:
        claude_text = desired("claude", claude_text, block, a.mode, "", claude_path.parent)
    if a.claude_source:
        src = a.claude_source.expanduser()
        claude_text = desired("claude", src.read_text(encoding="utf-8"), block, "block", "", src.parent)
    claude_base = claude_path.parent

    drift = 0
    for n in names:
        p = paths[n]
        cur = p.read_text(encoding="utf-8") if p.exists() else ""
        want = desired(n, cur, block, a.mode, claude_text, claude_base)
        if a.check:
            state = "OK" if cur == want else ("MISSING" if split_managed(cur)[1] is None else "STALE")
            drift += state != "OK"
            log.append(f"{state} {n} {p}")
        else:
            write_file(p, want, a.dry_run, log)

    if not a.no_cli:
        ok = cli_dst.exists() and cli_dst.read_bytes() == CLI_SRC.read_bytes()
        if a.check:
            drift += not ok
            log.append(f"{'OK' if ok else 'STALE'} cli {cli_dst}")
        elif ok:
            log.append(f"UNCHANGED {cli_dst}")
        elif a.dry_run:
            log.append(f"WOULD-WRITE {cli_dst}")
        else:
            bin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CLI_SRC, cli_dst)
            cli_dst.chmod(0o755)
            log.append(f"WROTE {cli_dst}")

    env_file = a.home / ".config" / "typesafe" / "env"
    key_set = bool(env.get("TYPESAFE_API_KEY")) or (
        env_file.exists() and re.search(r"^TYPESAFE_API_KEY=(?!replace-me\s*$)\S+", env_file.read_text(), re.M))
    if not env_file.exists() and not a.check and not a.dry_run:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.parent.chmod(0o700)
        env_file.write_text(ENV_PLACEHOLDER)
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        log.append(f"WROTE {env_file} (placeholder — add your key)")
    log.append(f"{'OK' if key_set else 'TODO'} key {'set' if key_set else f'not set: edit {env_file}'}")
    log.append(f"{'OK' if shutil.which('uv', path=env.get('PATH')) else 'TODO'} uv "
               f"{'on PATH' if shutil.which('uv', path=env.get('PATH')) else 'missing: https://docs.astral.sh/uv/'}")
    if str(bin_dir) not in env.get("PATH", "").split(os.pathsep):
        log.append(f"TODO path {bin_dir} is not on PATH")
    print("\n".join(log))
    return 1 if (a.check and drift) else 0


if __name__ == "__main__":
    sys.exit(main())
