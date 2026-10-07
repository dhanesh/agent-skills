#!/usr/bin/env python3
"""write_grant.py — build an autonomy-grant/v1 envelope after the user says yes.

Called only after the user has explicitly accepted the grant summary spec-first-planning
showed them (design spec §4, "Unattended mode, opt-in"). It refuses (never writes an
envelope) unless the spec is decision-closed under `spec_lint.py --unattended`, `--plan`
names a fresh task-plan/v1 envelope for exactly that spec, the answers are well shaped, and
the resulting statement passes the reference checker's structural and grant-specific checks
— so a malformed, stale or over-broad grant is a bug caught here, not something a consumer
has to notice later.

Usage:
    python3 write_grant.py --root <repo-root> --spec <spec path, relative to --root>
                           --plan <task-plan envelope path> --answers <answers.json>
                           --accepted-by <human name>

--spec and --plan may be given relative to --root or as absolute paths; either way, the
path (after following symlinks) must resolve inside --root, or the run is refused — a
symlink pointing out of the root does not count as "inside".

answers.json keys (only these 9 are recognised; any other key is refused):
    branch_pattern (str, required)   -- a glob, e.g. "factory/*"
    gate_policy    (dict, required)  -- action class -> "auto" | "grant" | "ask"
    expires_at     (str, required)   -- RFC 3339 UTC, in the future, at most 7 days after
                                        generatedAtTime
    budget         (dict, optional)  -- default {}
    stop_on        (list of str, optional)   -- default []
    defaults       (list of dict, optional)  -- default []
    system_one     (dict, optional)  -- {"allowed": bool, ...}; default {"allowed": false}
    reentry        (dict, optional)  -- consent to scheduled re-entry; checked against
                                        contract_check.reentry_problems (agent_cmd, plus
                                        contract_check.REENTRY_DEFAULTS' interval_min,
                                        stall_min, max_reentries). Absent means no block
                                        is written. When a .release/recipe.json exists
                                        under --root, agent_cmd's --allowedTools (or a
                                        permission-bypass flag) is refused when it would
                                        let an unattended agent run the recipe's
                                        deploy_prod or rollback (design spec D10): the
                                        release grant itself is written only by
                                        `release prep`, with the human present.
    release_defaults (dict, optional) -- exactly {"bump": "patch"|"minor"|"major",
                                        "grant_staging": bool, "grant_tag": bool}; the
                                        unattended interview's release defaults (design
                                        spec D10). Written into the grant payload as
                                        `release_defaults` for a later `release prep` to
                                        read; absent means no key is written.

Exit 0: prints "GRANT: <path>" as its last line. When <root>/.git is a directory, it first
    appends GRANT_EXCLUDE to <root>/.git/info/exclude (once) and says so: a grant is one
    person's acceptance, and check-grant treats a tracked grant as covering nothing.
Exit 1: refused; prints "REFUSED: <reason>" (a GrantRefused: the spec, the plan, the
    answers, the re-entry allowlist's reach into production, or the resulting statement
    failed a check).
Exit 2: usage error (an unreadable or malformed --answers file, or one missing a required
    key; a bad CLI invocation).

Stdlib only. contract_check (vendored, same dir) needs Python >= 3.10 and is imported
lazily, mirroring spec_to_tasks.py's style, so importing this module doesn't force that
floor on callers that only want GrantRefused or the constants.
"""

import argparse
import fnmatch
import json
import os
import re
import shlex
import sys
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_lint  # noqa: E402  (shared parser lives beside this script)
import spec_to_tasks  # noqa: E402

USAGE = ("usage: write_grant.py --root DIR --spec REL_SPEC --plan PLAN_ENVELOPE_PATH "
        "--answers ANSWERS.json --accepted-by NAME")

KNOWN_ANSWER_KEYS = frozenset({
    "branch_pattern", "gate_policy", "expires_at", "budget", "stop_on", "defaults", "system_one",
    "reentry", "release_defaults",
})

RELEASE_BUMP_LEVELS = ("patch", "minor", "major")  # mirrors release.py's BUMP_LEVELS (~line 198)
RELEASE_DEFAULTS_KEYS = frozenset({"bump", "grant_staging", "grant_tag"})


# Keeps every grant out of commits in this clone (skill-contract SPEC: a grant MUST NOT be
# committed). info/exclude is per clone and never tracked, so no tracked file is touched.
GRANT_EXCLUDE = ".skill-contract/envelopes/autonomy-grant-v1-*"


def exclude_grants(root):
    """Append GRANT_EXCLUDE to <root>/.git/info/exclude unless it is already there.
    Returns True when the line is in place, False when <root>/.git is not a directory."""
    git_dir = os.path.join(root, ".git")
    if not os.path.isdir(git_dir):
        return False
    path = os.path.join(git_dir, "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    if GRANT_EXCLUDE in (ln.strip() for ln in text.splitlines()):
        return True
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("\n" if text and not text.endswith("\n") else "") + GRANT_EXCLUDE + "\n")
    return True


class GrantRefused(Exception):
    """The grant was not written: the spec, the plan, the answers, or the resulting
    statement failed a check. Its message is the single reason to show the user."""


def _norm_rel(p):
    return os.path.normpath(p).replace(os.sep, "/") if isinstance(p, str) else p


def _parse_rfc3339(contract_check, s):
    if not (isinstance(s, str) and contract_check.TIME_RE.match(s)):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _check_unknown_keys(answers):
    unknown = sorted(k for k in answers if k not in KNOWN_ANSWER_KEYS)
    if unknown:
        key = unknown[0]
        note = " (A8: grants are never signed)" if key == "require_signature" else ""
        raise GrantRefused("unknown answer key %r%s" % (key, note))


def _check_answer_shapes(contract_check, answers):
    budget = answers.get("budget", {})
    if not isinstance(budget, dict):
        raise GrantRefused("answers.budget must be an object")
    stop_on = answers.get("stop_on", [])
    if not (isinstance(stop_on, list) and all(isinstance(s, str) for s in stop_on)):
        raise GrantRefused("answers.stop_on must be a list of strings")
    defaults = answers.get("defaults", [])
    if not (isinstance(defaults, list) and all(isinstance(d, dict) for d in defaults)):
        raise GrantRefused("answers.defaults must be a list of objects")
    system_one = answers.get("system_one", {"allowed": False})
    if not (isinstance(system_one, dict) and isinstance(system_one.get("allowed"), bool)):
        raise GrantRefused("answers.system_one must be an object with a boolean 'allowed'")
    reentry = answers.get("reentry")
    if reentry is not None:
        problems = contract_check.reentry_problems(reentry)
        if problems:
            raise GrantRefused("answers.reentry: " + "; ".join(problems))


def _check_release_defaults(answers):
    """answers.release_defaults, when given, must carry exactly {"bump", "grant_staging",
    "grant_tag"} (design spec D10: "the planning interview records only release
    defaults: the bump level, and whether to grant staging and tag pushes") -- no
    subset, no extra key, so a later `release prep` reads a complete, well-shaped
    default rather than guessing a missing one."""
    if "release_defaults" not in answers:
        return
    rd = answers["release_defaults"]
    if not isinstance(rd, dict):
        raise GrantRefused("answers.release_defaults must be an object")
    unknown = sorted(set(rd) - RELEASE_DEFAULTS_KEYS)
    if unknown:
        raise GrantRefused("answers.release_defaults: unknown key(s): %s" % ", ".join(unknown))
    missing = sorted(RELEASE_DEFAULTS_KEYS - set(rd))
    if missing:
        raise GrantRefused("answers.release_defaults is missing %s" % ", ".join(missing))
    if rd["bump"] not in RELEASE_BUMP_LEVELS:
        raise GrantRefused("answers.release_defaults.bump must be one of %s"
                           % ", ".join(RELEASE_BUMP_LEVELS))
    for key in ("grant_staging", "grant_tag"):
        if not isinstance(rd[key], bool):
            raise GrantRefused("answers.release_defaults.%s must be a boolean" % key)


# ── The production-allowlist refusal (design spec D10, "the allowlist paragraph") ───────
# Copied from release-conductor/assets/release.py's `_split_rules` (~line 2070),
# `allowlist_matches` (~line 2094) and `agent_cmd_exposes` (~line 2143), byte-for-byte in
# logic, because skills do not import each other. release.py's own docstring for
# `allowlist_matches` says so too ("Shared with spec-first-planning's write_grant.py
# (Task 7), which copies it: keep the two in step"). Any fix to the matcher there
# (notably R29's fail-closed malformed-list handling) must be ported here too.
ALLOWED_TOOLS_FLAGS = ("--allowedTools", "--allowed-tools")


def _split_rules(allowed_tools_str):
    """(rules, well_formed) for an --allowedTools value: split on commas AND whitespace
    outside parentheses (Claude Code accepts a comma- or space-separated list; a
    Bash(...) glob may itself carry either), each rule stripped, empties dropped.
    well_formed is False when a `)` closes nothing, a `(` is never closed, or a rule goes
    on after its parentheses close (`Bash(a)Bash(npx *)`, R45c) -- then the split cannot
    be trusted (a stray `(` swallows every rule after it, and a run-on rule hides the
    second glob inside the first one's parentheses)."""
    rules, depth, cur, well_formed, closed = [], 0, [], True, False
    for ch in allowed_tools_str or "":
        if (ch == "," or ch.isspace()) and depth == 0:
            rules.append("".join(cur))
            cur, closed = [], False
            continue
        if closed:
            well_formed = False  # text after the rule's parentheses closed
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth:
                depth -= 1
                closed = depth == 0
            else:
                well_formed = False
        cur.append(ch)
    rules.append("".join(cur))
    return [r.strip() for r in rules if r.strip()], well_formed and depth == 0


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*\Z")
# Programs that run the command after them (their own options and, for timeout, a
# duration first): a rule naming one reaches whatever follows it.
_RUN_WRAPPERS = ("env", "uvx", "sudo", "doas", "command", "builtin", "exec", "nice",
                 "nohup", "time", "stdbuf", "xargs", "timeout")
_SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "fish")
_PYTHON = re.compile(r"python(3(\.\S+)?)?\Z")
_FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")
# Modules that run a script or code given to them: `python3 -m <one> *` runs anything.
_PYTHON_RUN_MODULES = ("pdb", "runpy", "cProfile", "profile", "trace", "timeit", "code")


def _rule_readings(pat):
    """Every way an agent may reach a Bash(...) pattern's program (fail closed): the
    pattern as written, and each reading reached by repeatedly dropping a leading
    NAME=value or a run-anything wrapper (_RUN_WRAPPERS, `uv run`, `uv tool run`, with
    uv's global options before them) with its options -- each option alone and with the
    word after it, since it may take an argument; timeout then drops its duration --
    reducing an absolute or relative program path to its basename (`/usr/bin/python3`
    is python3), reading a versioned `python3.N` as python3, dropping a python flag
    (and the value of -X or -W), and expanding a `~/` or `$HOME/` word. A rule that runs
    any command reads as `*`: a shell (_SHELLS) given -c in its leading options (or a
    shell glob that could be), `eval`, `python -c`, `python -m` with a glob or a
    _PYTHON_RUN_MODULES module, `uv` with a glob that could be `uv run`, and find with
    -exec/-execdir/-ok/-okdir. `Bash(uv run *)` thus reads as `*`, and
    `Bash(/usr/bin/env python3 *)` as `python3 *`."""
    home = os.path.expanduser("~")
    out, todo = [], [pat]
    while todo:
        cur = todo.pop()
        if cur in out:
            continue
        out.append(cur)
        words = cur.split(" ")
        todo.append(" ".join(home + w[1:] if w.startswith("~/")
                             else home + w[5:] if w.startswith("$HOME/") else w
                             for w in words))
        head, rest = words[0], words[1:]
        opts = []
        for w in rest:  # a shell's leading options, up to its first operand
            if not w.startswith("-") or w in ("-", "--"):
                break
            opts.append(w)
        if head == "eval" \
                or (head == "find" and (any(w in _FIND_EXEC for w in rest)
                                        or fnmatch.fnmatch("find . -exec x ;", cur))) \
                or (head in _SHELLS and (fnmatch.fnmatch("%s -c x" % head, cur)
                                         or any(not w.startswith("--") and "c" in w[1:]
                                                for w in opts))) \
                or (head == "uv" and fnmatch.fnmatch("uv run x", cur)):
            todo.append("*")  # runs any command
        elif head.startswith("-") and head != "-":
            todo.append(" ".join(rest))  # a wrapper's option: alone, or with its argument
            todo.append(" ".join(rest[1:]))
        elif _ASSIGNMENT.match(head):
            todo.append(" ".join(rest))
        elif "/" in head and head.rsplit("/", 1)[1]:
            todo.append(" ".join([head.rsplit("/", 1)[1]] + rest))
        elif head == "uv":
            if rest[:1] and rest[0].startswith("-"):  # a global option, maybe with a value
                todo.append(" ".join([head] + rest[1:]))
                todo.append(" ".join([head] + rest[2:]))
            elif rest[:1] == ["run"]:
                todo.append(" ".join(rest[1:]))
            elif rest[:2] == ["tool", "run"]:
                todo.append(" ".join(rest[2:]))
        elif head in _RUN_WRAPPERS:
            if rest[:1] and rest[0].startswith("-"):  # its option, maybe with a value
                todo.append(" ".join([head] + rest[1:]))
                todo.append(" ".join([head] + rest[2:]))
            todo.append(" ".join(rest))
            if head == "timeout":
                todo.append(" ".join(rest[1:]))  # its duration
        elif _PYTHON.match(head):
            if re.match(r"python3\.\S+\Z", head):
                todo.append(" ".join(["python3"] + rest))
            flag = rest[0] if rest else ""
            module = (flag[2:] or (rest[1] if len(rest) > 1 else "")) \
                if flag.startswith("-m") else ""
            if flag.startswith("-c") or re.search(r"[*?\[]", module) \
                    or module in _PYTHON_RUN_MODULES:
                todo.append("*")  # python -c, or a module that runs any script
            elif flag in ("-X", "-W"):
                todo.append(" ".join([head] + rest[2:]))
            elif flag.startswith("-") and flag != "-" and not flag.startswith("-m"):
                todo.append(" ".join([head] + rest[1:]))
    return out


def allowlist_matches(allowed_tools_str, argv):
    """True when a Claude Code --allowedTools value would let a headless agent run argv
    without a prompt. Rules split on commas and whitespace outside parentheses; runs of
    whitespace in a pattern or a command compare as one space; `Bash` and `Bash(*)`
    match every command; `Bash(<glob>)` is matched with fnmatch against shlex.join(argv)
    -- and, failing closed, against " ".join(argv) and both with argv[0] reduced to its
    basename, since an agent may type the command either way; the legacy
    `Bash(<prefix>:*)` form is a prefix match on those same strings (the prefix may
    itself be a glob: fnmatch against prefix + "*"). Each pattern is tried in every
    reading _rule_readings gives (wrappers such as env and uv run dropped, an absolute
    or versioned interpreter reduced to its name, `~/` expanded). Other tools never
    match. A malformed list fails CLOSED (R29) and counts as matching: unbalanced
    parentheses anywhere, a rule that runs on after its parentheses close (R45c), or a rule that starts with the word `Bash` but is not exactly
    `Bash` or `Bash(...)` -- we cannot tell what Claude Code would make of it. (A longer
    tool name such as BashOutput is another tool, not a malformed Bash rule.) Copied from
    release-conductor's assets/release.py (Task 7): keep the two in step."""
    def norm(text):
        return " ".join(text.split())  # runs of whitespace compare as one space
    argv = list(argv)
    forms = {shlex.join(argv), " ".join(argv)}
    if argv:
        short = [os.path.basename(argv[0])] + argv[1:]
        forms |= {shlex.join(short), " ".join(short)}
    forms |= {norm(f) for f in forms}
    rules, well_formed = _split_rules(allowed_tools_str)
    if not well_formed:
        return True
    for rule in rules:
        if rule in ("Bash", "Bash(*)"):
            return True
        m = re.match(r"^Bash\((.*)\)\Z", rule, re.S)
        if not m:
            if re.match(r"^Bash(?![A-Za-z0-9_])", rule):
                return True  # Bash-something we cannot parse: fail closed
            continue
        pat = norm(m.group(1))
        legacy = pat.endswith(":*")  # `prefix:*`: the prefix is itself a glob
        for body in _rule_readings(pat[:-2] if legacy else pat):
            if legacy:  # Bash(*:*) and Bash(npx *:*) match too
                if any(f.startswith(body) or fnmatch.fnmatch(f, body + "*") for f in forms):
                    return True
            elif any(fnmatch.fnmatch(f, body) for f in forms):
                return True
    return False


def agent_cmd_exposes(agent_cmd, argv):
    """True when a re-entry agent_cmd would let its headless agent run argv unprompted:
    its --allowedTools (or --allowed-tools) value -- `--flag=value`, or every argument
    after the flag up to the next option, since Claude Code takes the list as several
    arguments too -- matches argv (allowlist_matches), or it bypasses permission prompts
    altogether (--dangerously-skip-permissions, --permission-mode bypassPermissions).
    Copied from release-conductor's assets/release.py (Task 7): keep the two in step."""
    if not isinstance(agent_cmd, list):
        return False
    values = []
    i = 0
    while i < len(agent_cmd):
        a = agent_cmd[i] if isinstance(agent_cmd[i], str) else ""
        if a == "--dangerously-skip-permissions" or a == "--permission-mode=bypassPermissions":
            return True
        if a == "--permission-mode" and i + 1 < len(agent_cmd) \
                and agent_cmd[i + 1] == "bypassPermissions":
            return True
        flag, eq, val = a.partition("=")
        if flag in ALLOWED_TOOLS_FLAGS:
            if eq:
                values.append(val)
            else:
                j = i + 1
                while j < len(agent_cmd) and isinstance(agent_cmd[j], str) \
                        and not agent_cmd[j].startswith("-"):
                    values.append(agent_cmd[j])
                    j += 1
                i = j
                continue
        i += 1
    return any(allowlist_matches(v, argv) for v in values)


# R43: release.py's own production commands. An allowlist that lets a headless agent run
# `python3 <...>/release.py deploy --approved-by ...` reaches production as surely as one
# that reaches the recipe's deploy_prod: the agent could forge the yes. Each spelling an
# allowlist author may glob on: the path the caller knows, plus generic ones (bare, the
# skill-relative assets/ path, the $SKILL_DIR form the SKILL writes (bare and in the
# double quotes it is typed with), and an absolute
# install path that a `*/release.py` glob or a `python3 *` rule matches).
RELEASE_TOOL_PATHS = ("release.py", "assets/release.py", "$SKILL_DIR/assets/release.py",
                      '"$SKILL_DIR/assets/release.py"',
                      "/skills/release-conductor/assets/release.py", "./release.py",
                      "~/.claude/skills/release-conductor/assets/release.py",
                      "~/.agents/skills/release-conductor/assets/release.py")
RELEASE_PROD_COMMANDS = ("deploy", "rollback", "abandon")
# Each interpreter spelling an agent may type before release.py (an empty one runs the
# script by its shebang). _rule_readings covers more (python3.N, env options).
RELEASE_TOOL_INTERPRETERS = (("python3",), ("python",), ("/usr/bin/python3",),
                             ("/usr/local/bin/python3",), ("env", "python3"),
                             ("/usr/bin/env", "python3"), ("uv", "run", "python"),
                             ("uv", "run"), ("uvx", "python"), ())


def release_tool_argvs(release_paths):
    """[argv, ...]: every interpreter spelling (RELEASE_TOOL_INTERPRETERS, then this one)
    x every spelling of release.py (`release_paths` first, each also in its `~/` form,
    then RELEASE_TOOL_PATHS) x each production command (deploy, rollback, abandon), bare
    and with the arguments a real call carries."""
    home = os.path.expanduser("~")
    paths = [p for p in release_paths if p]
    paths += ["~" + p[len(home):] for p in paths if p.startswith(home + "/")]
    paths += [p for p in RELEASE_TOOL_PATHS if p not in paths]
    pys = list(RELEASE_TOOL_INTERPRETERS) + ([(sys.executable,)] if sys.executable else [])
    out = []
    for py in pys:
        for path in paths:
            for cmd in RELEASE_PROD_COMMANDS:
                out.append(list(py) + [path, cmd])
                out.append(list(py) + [path, cmd, "--root", ".", "--approved-by", "human"])
    return out


RELEASE_RECIPE_REL = ".release/recipe.json"
_EXPAND_TOKEN = re.compile(r"\{(version|commit|env)\}")
# version/commit/env placeholders: a real release's values aren't known yet when a grant
# is written, so a fixed, glob-safe stand-in is substituted instead. Mirrors release.py's
# `expand` (~line 342) for just this token set, narrowly, rather than importing it.
_PLACEHOLDER_VALUES = {"version": "0.0.0", "commit": "0" * 40, "env": "production"}


def _expand_placeholder(argv):
    """argv with every {version}/{commit}/{env} token replaced by _PLACEHOLDER_VALUES."""
    return [_EXPAND_TOKEN.sub(lambda m: _PLACEHOLDER_VALUES[m.group(1)], a) for a in argv]


def _production_argvs(root):
    """[argv, ...] write_grant must refuse an exposing re-entry allowlist against: the
    repo's .release/recipe.json (release-conductor, design spec D10) `deploy_prod` and
    `rollback` argv (and release.py's own deploy/rollback/abandon, R43), each recipe argv
    checked both as the raw template (an allowlist could glob on
    the literal {version}/{commit}/{env} braces) and with those tokens expanded to a
    fixed placeholder (_expand_placeholder) -- so either spelling an allowlist author
    might have used is caught (R29's fail-closed spirit in release.py). None when there
    is no recipe file: a repo without release-conductor has nothing to check.

    Raises GrantRefused when the recipe exists but cannot be read, is not valid JSON, or
    does not carry a well-formed deploy_prod/rollback argv list -- fail closed rather
    than silently treat an unreadable recipe as "nothing to check"."""
    path = os.path.join(root, *RELEASE_RECIPE_REL.split("/"))
    if not os.path.lexists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise GrantRefused("cannot read %s: %s" % (RELEASE_RECIPE_REL, exc))
    try:
        recipe = json.loads(raw)
    except ValueError as exc:
        raise GrantRefused("%s is not valid JSON: %s" % (RELEASE_RECIPE_REL, exc))
    if not isinstance(recipe, dict):
        raise GrantRefused("%s must be a JSON object" % RELEASE_RECIPE_REL)
    argvs = []
    for key in ("deploy_prod", "rollback"):
        argv = recipe.get(key)
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            raise GrantRefused("%s's %r must be a non-empty list of strings to check the "
                               "production allowlist against" % (RELEASE_RECIPE_REL, key))
        argvs.append(argv)
        argvs.append(_expand_placeholder(argv))
    # R43: release.py's own deploy/rollback/abandon reach production too. Its path is known
    # when release-conductor is installed beside this skill; the generic spellings in
    # release_tool_argvs catch `Bash(python3 *)` and `*/release.py` globs either way.
    argvs.extend(release_tool_argvs(_release_py_paths()))
    return argvs


def _release_py_paths():
    """[path] of release-conductor's release.py when it is installed beside this skill
    (skills install as sibling directories), else []."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(os.path.dirname(here)), "release-conductor",
                        "assets", "release.py")
    return [path] if os.path.isfile(path) else []


def _check_production_allowlist(root, answers):
    """Refuse when answers.reentry.agent_cmd would let the unattended agent it
    reinstalls run the repo's production deploy or rollback (design spec D10): a release
    grant is written only by `release prep`, with the human present, so an unattended
    interview's own re-entry grant must never reach production by way of its allowlist.
    A no-op when answers carries no reentry.agent_cmd: there is then no allowlist of this
    grant's own to check (release prep's own grant, written separately, is covered by
    release.py's own refusal at stage/deploy time)."""
    reentry = answers.get("reentry")
    agent_cmd = reentry.get("agent_cmd") if isinstance(reentry, dict) else None
    if not isinstance(agent_cmd, list):
        return
    argvs = _production_argvs(root)
    if not argvs:
        return
    for argv in argvs:
        if agent_cmd_exposes(agent_cmd, argv):
            raise GrantRefused(
                "answers.reentry.agent_cmd's allowlist would let an unattended agent run "
                "the repo's production deploy or rollback (%s); the release grant itself "
                "is written only by `release prep`, with the human present"
                % RELEASE_RECIPE_REL)


def _check_accepted_by(accepted_by):
    if not (isinstance(accepted_by, str) and accepted_by.strip()):
        raise GrantRefused("--accepted-by must name the human who said yes")
    if any(unicodedata.category(c)[0] == "C" for c in accepted_by):
        raise GrantRefused("--accepted-by must not contain control characters")


def _validate_plan(contract_check, root, spec_rel, spec_path, plan_rel):
    """--plan must be a fresh task-plan/v1 envelope pinning exactly this spec (design spec
    §1: the grant's subjects are the spec and "the task-plan envelope it was approved
    for"). Each check's failure message says which of those five things is wrong."""
    plan_path = os.path.join(root, *plan_rel.split("/"))
    plan_st, errs = contract_check.load_envelope(plan_path)
    if errs:
        raise GrantRefused("--plan %s: %s" % (plan_rel, errs[0][1]))
    plan_viol = contract_check.check_statement(plan_st)
    if plan_viol:
        raise GrantRefused("--plan %s fails the reference checker: C%d: %s"
                           % ((plan_rel,) + plan_viol[0]))
    if plan_st.get("predicateType") != spec_to_tasks.TASK_PLAN_KIND:
        raise GrantRefused("--plan %s is not a task-plan/v1 envelope (predicateType %r)"
                           % (plan_rel, plan_st.get("predicateType")))
    plan_spec = _norm_rel(plan_st["predicate"]["payload"].get("spec"))
    if plan_spec != _norm_rel(spec_rel):
        raise GrantRefused("--plan %s was derived from %r, not %r"
                           % (plan_rel, plan_spec, spec_rel))
    spec_subject = next((s for s in plan_st.get("subject", [])
                         if _norm_rel(s.get("name")) == _norm_rel(spec_rel)), None)
    if spec_subject is None:
        raise GrantRefused("--plan %s does not pin the spec %s" % (plan_rel, spec_rel))
    if (spec_subject.get("digest") or {}).get("sha256") != contract_check.sha256_file(spec_path):
        raise GrantRefused("--plan %s is stale: its digest for %s does not match the file "
                           "on disk" % (plan_rel, spec_rel))
    stale = contract_check.stale_names(root, plan_st.get("subject"))
    if stale:
        raise GrantRefused("--plan %s is stale: %s changed since the plan was written"
                           % (plan_rel, ", ".join(stale)))


def build_grant(root, spec_rel, plan_rel, answers, accepted_by, now=None):
    """Build (but do not write) the autonomy-grant/v1 statement.

    Raises GrantRefused when: an answers key is unrecognised or malformed,
    answers.release_defaults is not exactly {bump, grant_staging, grant_tag} well shaped,
    answers.reentry.agent_cmd's allowlist would reach the repo's .release/recipe.json
    deploy_prod or rollback, --accepted-by is blank or carries a control character,
    expires_at is not a valid RFC 3339 timestamp in the future, the spec cannot be read or
    is not decision-closed, --plan is not a fresh task-plan/v1 envelope for exactly this
    spec, or the resulting statement fails contract_check's structural (C3-C6) or
    grant-specific (C10) checks — including the 7-day lifetime cap, which
    grant_violations enforces.
    """
    import contract_check  # lazy: needs Python >= 3.10 (see the module docstring)

    _check_unknown_keys(answers)
    _check_answer_shapes(contract_check, answers)
    _check_release_defaults(answers)
    _check_production_allowlist(root, answers)
    _check_accepted_by(accepted_by)

    now = now or contract_check.utc_now()
    expires = _parse_rfc3339(contract_check, answers.get("expires_at"))
    if expires is None:
        raise GrantRefused("expires_at must be RFC 3339 UTC (YYYY-MM-DDThh:mm:ssZ)")
    if expires <= now:
        raise GrantRefused("expires_at must be in the future")

    spec_path = os.path.join(root, *spec_rel.split("/"))
    try:
        with open(spec_path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise GrantRefused("cannot read --spec %s: %s" % (spec_rel, exc))
    issues = spec_lint.lint(text, mode="unattended")
    if issues:
        raise GrantRefused("spec is not decision-closed: " + issues[0])

    _validate_plan(contract_check, root, spec_rel, spec_path, plan_rel)

    spec = spec_lint.parse_spec(text)
    reentry = answers.get("reentry")
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
        **({"reentry": dict(contract_check.REENTRY_DEFAULTS, **reentry)} if reentry else {}),
        **({"release_defaults": answers["release_defaults"]}
           if "release_defaults" in answers else {}),
    }
    accepted = {"test": "grant-accepted", "assertedBy": {"human": accepted_by.strip()},
                "result": {"outcome": "passed"},
                "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                            "--unattended", spec_rel],
                "subject": [contract_check.pin(root, spec_rel)]}
    st = contract_check.build_statement(contract_check.GRANT_KIND, "spec-first-planning",
                                        spec_to_tasks.SKILL_VERSION, root, [spec_rel, plan_rel],
                                        payload, [accepted], now=now)
    viol = ["C%d: %s" % v for v in contract_check.check_statement(st)] \
        or contract_check.grant_violations(st)
    if viol:
        raise GrantRefused(viol[0])
    return st


def _contained_rel(root, root_real, given):
    """Root-relative, forward-slash path for `given` (absolute, or relative to root), or
    None when it resolves — after following symlinks — outside root. Used for both --spec
    and --plan so a symlink out of the root, a `..` escape, or an absolute path elsewhere
    is refused rather than silently pinned."""
    abs_path = given if os.path.isabs(given) else os.path.join(root, given)
    real = os.path.realpath(abs_path)
    try:
        rel = os.path.relpath(real, root_real)
    except ValueError:
        return None  # e.g. a different drive on Windows
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def build_parser():
    ap = argparse.ArgumentParser(prog="write_grant.py",
                                 description="build the autonomy-grant/v1 envelope after the "
                                             "user says yes")
    ap.add_argument("--root", required=True, help="repo root")
    ap.add_argument("--spec", required=True,
                    help="the spec path, relative to --root (or absolute) — must resolve "
                         "inside --root")
    ap.add_argument("--plan", required=True,
                    help="the task-plan envelope path, relative to --root (or absolute) — "
                         "must resolve inside --root")
    ap.add_argument("--answers", required=True, help="path to a JSON file of answers")
    ap.add_argument("--accepted-by", required=True, dest="accepted_by",
                    help="the human who said yes")
    return ap


def main(argv=None):
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code else 0

    root = os.path.abspath(a.root)
    root_real = os.path.realpath(root)

    spec_rel = _contained_rel(root, root_real, a.spec)
    if spec_rel is None:
        print("REFUSED: --spec %s is outside --root %s" % (a.spec, root))
        return 1
    plan_rel = _contained_rel(root, root_real, a.plan)
    if plan_rel is None:
        print("REFUSED: --plan %s is outside --root %s" % (a.plan, root))
        return 1

    try:
        with open(a.answers, encoding="utf-8") as f:
            answers = json.load(f)
    except (OSError, ValueError) as exc:
        print(USAGE, file=sys.stderr)
        print("usage: cannot read --answers %s: %s" % (a.answers, exc), file=sys.stderr)
        return 2
    if not isinstance(answers, dict):
        print(USAGE, file=sys.stderr)
        print("usage: --answers must be a JSON object", file=sys.stderr)
        return 2
    missing = [k for k in ("branch_pattern", "gate_policy", "expires_at") if k not in answers]
    if missing:
        print(USAGE, file=sys.stderr)
        print("usage: --answers is missing %s" % ", ".join(missing), file=sys.stderr)
        return 2

    try:
        st = build_grant(root, spec_rel, plan_rel, answers, a.accepted_by)
    except GrantRefused as exc:
        print("REFUSED: %s" % exc)
        return 1

    import contract_check  # lazy: needs Python >= 3.10 (see the module docstring)
    try:
        path = contract_check.write_envelope(root, st)
    except (OSError, ValueError) as exc:
        print("REFUSED: cannot write the envelope: %s" % exc)
        return 1
    try:
        excluded = exclude_grants(root)
    except (OSError, UnicodeDecodeError) as exc:
        print("WARNING: could not add %s to .git/info/exclude (%s); do not commit the grant: "
              "check-grant treats a tracked grant as covering nothing" % (GRANT_EXCLUDE, exc))
    else:
        if excluded:
            print("NOTE: the grant is yours alone and is kept out of commits "
                  "(.git/info/exclude lists %s)" % GRANT_EXCLUDE)
    reentry = st["predicate"]["payload"].get("reentry")
    if reentry:
        print("REENTRY: %s every %d min" % (shlex.join(reentry["agent_cmd"]),
                                             reentry["interval_min"]))
        if not os.path.isabs(reentry["agent_cmd"][0]):
            print("warning: agent_cmd[0] is not an absolute path; the timer's PATH is the "
                  "one captured at reentry install", file=sys.stderr)
    print("GRANT: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
