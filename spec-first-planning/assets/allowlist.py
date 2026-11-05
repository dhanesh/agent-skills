#!/usr/bin/env python3
"""allowlist.py — lint and match the check-command allowlist (.factory/check-commands.txt).

A plan made from an intake item or an idea may run a check command unattended only when
the command matches one pattern in the owner's committed allowlist (master spec S1, D3).
This module reads that file, lints each pattern, and matches a command against it.
The format is in references/spec-format.md, section "Check-command allowlist".

File format:
    One pattern per line. A line that starts with `#` (after leading spaces) and a blank
    line are comments. A pattern is tokens split on whitespace, in the plan command form:
    the first token is `{python}` or a bare program name, the rest are literal arguments
    or `*`.

Matching:
    A command is split on whitespace (no quote processing). It matches a pattern when it
    has the same number of tokens, each literal token is equal, and each `*` token's
    argument passes `arg_ok`: only letters, digits and `_./=-`, not starting with `-`,
    with no `..` (decision D15). So `SKILL=$(shell id)` and `-mhttp.server` never match.

Lint (a pattern fails when):
    - the program is an absolute or relative path (`/usr/bin/python3`, `./x`);
    - the program is a bare interpreter (`python3`, `node`, ...): use `{python}`;
    - the program is a launcher that runs another program (`env`, `sudo`, `xargs`, ...);
    - the program is a shell (`sh`, `bash`, ...), unless the pattern is exactly
      `<shell> <repo-relative .sh path>` with no flag and no `*`, so a shell runs one
      fixed, committed script and never a `-c` string;
    - any token holds a character outside letters, digits and `_./=-` (literal tokens),
      starts with `/` or `~`, or holds `..`; or `*` is the program; or `{python}` is not
      the first token.

Usage:
    python3 allowlist.py lint <file>
        Exit 0, last line "ALLOWLIST-LINT: pass <n> patterns".
        Exit 1, one "ALLOWLIST-LINT: fail line <n>: <reason>" line per problem.
    python3 allowlist.py match <file> <command>
        Exit 0, prints "MATCH: <pattern>". Exit 1, prints "NO-MATCH: <command>".
        A file that fails the lint exits 1 with its lint lines; nothing matches.
    Exit 2: usage error or an unreadable file.

Stdlib only. Importable: `lint_text`, `load`, `matches`, `match_command`, `arg_ok`.
"""
import os
import re
import sys

PYTHON = "{python}"
WILDCARD = "*"

# D15: one argument of letters, digits and `_./=-`.
_ARG_CHARS = re.compile(r"^[A-Za-z0-9_./=-]+$")
_PROGRAM = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")

SHELLS = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "csh", "tcsh", "fish",
    "busybox", "pwsh", "powershell", "cmd",
})
INTERPRETERS = frozenset({
    "python", "python2", "python3", "pypy", "pypy3", "node", "nodejs", "deno", "bun",
    "perl", "ruby", "php", "lua", "luajit", "tclsh", "wish", "Rscript", "osascript",
    "awk", "gawk", "mawk", "nawk", "jshell", "groovy", "julia", "irb",
})
LAUNCHERS = frozenset({
    "env", "sudo", "doas", "su", "xargs", "exec", "eval", "command", "builtin",
    "nohup", "nice", "ionice", "time", "timeout", "stdbuf", "chroot", "setsid",
    "watch", "parallel", "find", "ssh", "script", "unbuffer", "npx", "bunx",
})
_VERSIONED_PY = re.compile(r"^python[0-9.]*$")


def arg_ok(arg):
    """True when `arg` may stand in for one `*`: D15's character set, no leading `-`,
    no `..`."""
    return (bool(_ARG_CHARS.match(arg)) and not arg.startswith("-")
            and ".." not in arg)


def _literal_problem(tok):
    if tok == PYTHON:
        return "`{python}` may only be the first token"
    if not _ARG_CHARS.match(tok):
        return "token %r holds a character outside letters, digits and `_./=-`" % tok
    if tok.startswith("/") or tok.startswith("~"):
        return "token %r is an absolute path" % tok
    if ".." in tok:
        return "token %r holds `..`" % tok
    return None


def lint_pattern(pattern):
    """Return the list of problems with one pattern line (empty list: it passes)."""
    tokens = pattern.split()
    if not tokens:
        return ["empty pattern"]
    prog, args = tokens[0], tokens[1:]
    problems = []
    if prog == WILDCARD:
        problems.append("`*` may not be the program")
    elif prog != PYTHON:
        if "/" in prog or prog.startswith("~"):
            problems.append("program %r is a path; name a bare program or `{python}`"
                            % prog)
        elif not _PROGRAM.match(prog):
            problems.append("program %r is not a bare program name" % prog)
        elif prog in INTERPRETERS or _VERSIONED_PY.match(prog):
            problems.append("program %r is an interpreter; use `{python}`" % prog)
        elif prog in LAUNCHERS:
            problems.append("program %r runs another program" % prog)
        elif prog in SHELLS:
            ok = (len(args) == 1 and args[0] != WILDCARD and arg_ok(args[0])
                  and args[0].endswith(".sh") and "/" in args[0])
            if not ok:
                problems.append("program %r is a shell; a shell may only run one "
                                "repo-relative .sh script, with no flag and no `*`"
                                % prog)
                return problems
    for tok in args:
        if tok == WILDCARD:
            continue
        p = _literal_problem(tok)
        if p:
            problems.append(p)
    return problems


def _pattern_lines(text):
    """Yield (line number, pattern) for each non-comment line."""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield n, line


def lint_text(text):
    """Lint a whole allowlist. Return (patterns, problems): patterns is the list of
    pattern strings (normalised to single spaces); problems is a list of
    (line number, reason)."""
    patterns, problems = [], []
    for n, line in _pattern_lines(text):
        found = lint_pattern(line)
        problems.extend((n, p) for p in found)
        patterns.append(" ".join(line.split()))
    return patterns, problems


def load(path):
    """Read and lint an allowlist file. Return (patterns, problems). Raises OSError."""
    with open(path, encoding="utf-8") as fh:
        return lint_text(fh.read())


def matches(pattern, command):
    """True when `command` (a string) matches `pattern` (a string)."""
    ptoks, ctoks = pattern.split(), command.split()
    if not ptoks or len(ptoks) != len(ctoks):
        return False
    for p, c in zip(ptoks, ctoks):
        if p == WILDCARD:
            if not arg_ok(c):
                return False
        elif p != c:
            return False
    return True


def match_command(patterns, command):
    """Return the first pattern that `command` matches, or None."""
    for p in patterns:
        if matches(p, command):
            return p
    return None


def _usage():
    sys.stderr.write("usage: allowlist.py lint <file> | allowlist.py match <file> "
                     "<command>\n")
    return 2


def main(argv):
    if len(argv) < 2 or argv[0] not in ("lint", "match"):
        return _usage()
    if (argv[0] == "lint" and len(argv) != 2) or (argv[0] == "match" and len(argv) != 3):
        return _usage()
    path = argv[1]
    try:
        patterns, problems = load(path)
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("ERROR: cannot read %s: %s\n" % (path, exc))
        return 2
    for n, reason in problems:
        print("ALLOWLIST-LINT: fail line %d: %s" % (n, reason))
    if problems:
        return 1
    if argv[0] == "lint":
        print("ALLOWLIST-LINT: pass %d patterns" % len(patterns))
        return 0
    hit = match_command(patterns, argv[2])
    if hit is None:
        print("NO-MATCH: %s" % argv[2])
        return 1
    print("MATCH: %s" % hit)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
