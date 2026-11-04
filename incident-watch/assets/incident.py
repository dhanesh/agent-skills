#!/usr/bin/env python3
"""incident.py: incident-watch's live-incident probe (spec
docs/factory/2026-11-04-autonomous-factory-spec.md, R22; constraint S4; decision D1).

Commands:

  probe   Load the release recipe the way release-conductor does (its own `load_recipe`,
          imported from the release-conductor skill installed beside this one), then run
          only the recipe's read-only `health`, `version_probe` and each `prod_smoke`
          command. Each command runs as an argv list with no shell, no stdin and a
          timeout, through release-conductor's own `run_cmd`. Each result (time, name,
          argv, exit code, a bounded output tail) is appended to
          .skill-contract/incidents/probes.jsonl. Prints one
          `PROBE: <name> <pass|fail|timeout> rc=<rc>` line per command and one
          `PROBE_RESULT: <pass|fail> commands=<n> failed=<n> consecutive_failures=<n>` line.

Pending (not built here): refusing a requested command outside the three lists (R23),
opening one incident record and writing an `incident` signal after 2 or more consecutive
failed probes (R24), the rollback command shown word for word (R25) and
`incident close --by <name>` (R26). This tool never runs `build`, `deploy_staging`,
`deploy_prod`, `rollback` or `staging_checks`: the only commands it reads from the recipe
are the ones probe_commands() returns.

State lives in <root>/.skill-contract/incidents/ (git-ignored by its own `*` .gitignore,
checked before any command runs, the ops-intake and idea-validation pattern):
  probes.jsonl  one line per probe run, append-only;
  state.json    {"consecutive_failures", "last_probe_at", "last_result"}: the counter R24
                reads to decide when to open a record.

Recipe tokens: {env} is release-conductor's production value. {version} and {commit} come
from --version and --commit; a probe command that needs one that was not given is refused
before anything runs.

Release-conductor is found by directory name in the roots skill-contract `discover`
searches: $SKILL_CONTRACT_PATH, the directory beside this skill, <root>/.agents/skills,
<root>/.claude/skills, ~/.agents/skills, ~/.claude/skills. `--skills-dir` (repeatable)
replaces that list.

Stdlib only, Python 3.10+, POSIX. Exits: 0 every command passed; 3 a command failed or
another probe holds the lock; 2 refused (usage, no release-conductor, an invalid recipe, a
missing token value, or a state directory that cannot be shown to be git-ignored).
"""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("incident.py needs Python >= 3.10, found %d.%d\n" % sys.version_info[:2])
    sys.exit(2)

import argparse  # noqa: E402
import contextlib  # noqa: E402
import datetime  # noqa: E402
import fcntl  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402

SKILL_NAME = "incident-watch"
VERSION = "0.1.0"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCIDENTS_DIR = ".skill-contract/incidents"
PROBES_LOG = "probes.jsonl"
STATE_FILE = "state.json"
LOCK_FILE = ".lock"
RELEASE_TOOL = ("release-conductor", "assets/release.py")
DEFAULT_TIMEOUT = 60  # seconds per probe command
TAIL = 2000  # characters of stdout and of stderr kept per command (release.py's TAIL)
# The recipe keys a probe may read. Nothing else in the recipe is ever run (S4).
PROBE_KEYS = ("health", "version_probe", "prod_smoke")
_UNFILLED = re.compile(r"\{(version|commit|env)\}")


class Refused(Exception):
    """A refusal before any probe command runs: exit 2."""


class Locked(Exception):
    """Another probe holds the incidents lock: exit 3."""


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── finding and loading release-conductor ───────────────────────────────────────────────

def skill_roots(root, env=None, overrides=None):
    """The directories to search for sibling skills, in precedence order (the same list
    factory-pipeline's skill_roots() uses)."""
    env = os.environ if env is None else env
    if overrides:
        return [os.path.abspath(d) for d in overrides]
    roots = [d for d in (env.get("SKILL_CONTRACT_PATH") or "").split(os.pathsep) if d]
    roots.append(os.path.dirname(SKILL_DIR))
    roots += [os.path.join(root, b, "skills") for b in (".agents", ".claude")]
    home = env.get("HOME") or os.path.expanduser("~")
    roots += [os.path.join(home, b, "skills") for b in (".agents", ".claude")]
    return [os.path.abspath(d) for d in roots]


def find_release_tool(roots):
    """The absolute path of release-conductor's release.py, or None when it is not
    installed in any root."""
    skill, rel = RELEASE_TOOL
    for r in roots:
        d = os.path.join(r, skill)
        tool = os.path.join(d, *rel.split("/"))
        if os.path.isfile(os.path.join(d, "SKILL.md")) and os.path.isfile(tool):
            return tool
    return None


def load_release_module(path):
    """Import release-conductor's release.py from `path` as its own module. The recipe
    format, its validation, token expansion and the command runner are release-conductor's,
    so incident-watch carries no second copy of any of them."""
    spec = importlib.util.spec_from_file_location("incident_watch_release", path)
    if spec is None or spec.loader is None:
        raise Refused("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except (ImportError, OSError, SyntaxError) as e:
        raise Refused("cannot load %s: %s" % (path, e))
    for name in ("load_recipe", "recipe_sha", "expand", "run_cmd", "PROD_ENV"):
        if not hasattr(mod, name):
            raise Refused("%s has no %s; release-conductor is too old" % (path, name))
    return mod


# ── the commands a probe may run (S4) ───────────────────────────────────────────────────

def probe_commands(recipe):
    """[(name, argv template)] for every command a probe runs, in order: health,
    version_probe, then prod_smoke[0..n-1]. This is the whole set of recipe commands
    incident-watch can run; R23's refusal of any other command checks against it."""
    out = [("health", recipe["health"]), ("version_probe", recipe["version_probe"])]
    out += [("prod_smoke[%d]" % i, argv) for i, argv in enumerate(recipe["prod_smoke"])]
    return out


def expand_all(release, commands, version=None, commit=None):
    """[(name, argv)] with {env} (production), {version} and {commit} filled. Raises
    Refused when a command still needs a value that was not given, so nothing runs with a
    literal {token} in it."""
    values = {"env": release.PROD_ENV}
    if version:
        values["version"] = version
    if commit:
        values["commit"] = commit
    out = []
    for name, template in commands:
        try:
            argv = release.expand(template, values)
        except ValueError as e:
            raise Refused(str(e))
        left = sorted({m.group(1) for a in argv for m in _UNFILLED.finditer(a)})
        if left:
            raise Refused("recipe %s needs {%s}: pass %s" % (
                name, "}, {".join(left), " and ".join("--" + k for k in left)))
        out.append((name, argv))
    return out


# ── the git-ignored state directory ─────────────────────────────────────────────────────

def _incidents_dir(root):
    return os.path.join(root, *INCIDENTS_DIR.split("/"))


def _gitignore_ok(path):
    """True when the file is a regular file holding `*` and no `!` line."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
        return "*" in lines and not any(ln.startswith("!") for ln in lines)
    except (OSError, ValueError):
        return False


def ensure_dir(root):
    """Make .skill-contract/incidents/ and prove it is git-ignored before any other write.
    Mirrors idea-validation's _ensure_dir(): the directory carries its own `*` .gitignore,
    which git honours whatever the root .gitignore says. Probe output can carry production
    details, so it is never committed. Raises Refused when that cannot be shown."""
    top = os.path.join(root, ".skill-contract")
    d = _incidents_dir(root)
    if os.path.islink(top) or os.path.islink(d):
        raise Refused("%s is a symlink" % INCIDENTS_DIR)
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and _gitignore_ok(gi):
        return d
    if os.path.islink(gi) or (os.path.lexists(gi) and not os.path.isfile(gi)):
        raise Refused("%s/.gitignore is not a regular file" % INCIDENTS_DIR)
    if os.path.isfile(gi):
        with open(gi, encoding="utf-8") as f:
            if any(ln.strip().startswith("!") for ln in f.read().splitlines()):
                raise Refused("%s/.gitignore has a `!` line that un-ignores files"
                              % INCIDENTS_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by incident-watch: probe output is local, never committed\n*\n")
    except OSError as e:
        raise Refused("cannot write %s/.gitignore: %s" % (INCIDENTS_DIR, e))
    if not _gitignore_ok(gi):
        raise Refused("cannot verify %s/.gitignore" % INCIDENTS_DIR)
    return d


@contextlib.contextmanager
def probe_lock(d):
    """A non-blocking exclusive flock on <d>/.lock, so two probes never interleave their
    counter updates. Raises Locked when another probe holds it."""
    f = open(os.path.join(d, LOCK_FILE), "a+")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Locked()
        yield
    finally:
        f.close()


def read_state(d):
    """The probe counter state, or the zero state when none is recorded yet."""
    try:
        with open(os.path.join(d, STATE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("consecutive_failures"), int):
            return data
    except (OSError, ValueError):
        pass
    return {"consecutive_failures": 0, "last_probe_at": None, "last_result": None}


def write_state(d, state):
    """Rewrite state.json atomically (temp file, then rename)."""
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(d, STATE_FILE))


def append_probe(d, record):
    with open(os.path.join(d, PROBES_LOG), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


# ── the probe ───────────────────────────────────────────────────────────────────────────

def run_probe(release, root, commands, timeout):
    """Run each (name, argv) with release-conductor's run_cmd (argv list, no shell, no
    stdin, own process group, timeout). Return one result dict per command."""
    results = []
    for name, argv in commands:
        at = now_iso()
        res = release.run_cmd(argv, root, timeout)
        if res.get("timed_out"):
            word = "timeout"
        elif res.get("rc") == 0:
            word = "pass"
        else:
            word = "fail"
        results.append({"at": at, "name": name, "argv": argv, "rc": res.get("rc"),
                        "result": word, "timed_out": bool(res.get("timed_out")),
                        "out_tail": (res.get("out_tail") or "")[-TAIL:],
                        "err_tail": (res.get("err_tail") or "")[-TAIL:]})
    return results


def after_probe(d, state, record):
    """The seam for R24: after 2 or more consecutive failed probes, open one incident
    record and write one `incident` signal. Not built here; it returns None."""
    return None


def cmd_probe(args):
    root = os.path.abspath(args.root)
    try:
        if args.timeout <= 0:
            raise Refused("--timeout must be a positive number of seconds")
        tool = find_release_tool(skill_roots(root, overrides=args.skills_dir))
        if tool is None:
            raise Refused("release-conductor is not installed; incident-watch reads its "
                          "recipe format")
        release = load_release_module(tool)
        recipe, problems = release.load_recipe(root)
        if recipe is None or problems:
            raise Refused("invalid recipe %s: %s" % (
                getattr(release, "RECIPE_PATH", ".release/recipe.json"), "; ".join(problems)))
        commands = expand_all(release, probe_commands(recipe), args.version, args.commit)
        d = ensure_dir(root)
        with probe_lock(d):
            results = run_probe(release, root, commands, args.timeout)
            failed = [r for r in results if r["result"] != "pass"]
            state = read_state(d)
            at = now_iso()
            word = "fail" if failed else "pass"
            state["consecutive_failures"] = state["consecutive_failures"] + 1 if failed else 0
            state["last_probe_at"] = at
            state["last_result"] = word
            record = {"at": at, "result": word, "recipe_sha": release.recipe_sha(root),
                      "commands": results,
                      "consecutive_failures": state["consecutive_failures"]}
            append_probe(d, record)
            write_state(d, state)
            after_probe(d, state, record)
    except Refused as e:
        print("PROBE: refused: %s" % e)
        return 2
    except Locked:
        print("STOP: locked: another incident probe holds %s/%s" % (INCIDENTS_DIR, LOCK_FILE))
        return 3
    for r in results:
        print("PROBE: %s %s rc=%s" % (r["name"], r["result"], r["rc"]))
    print("PROBE_RESULT: %s commands=%d failed=%d consecutive_failures=%d"
          % (word, len(results), len(failed), state["consecutive_failures"]))
    return 3 if failed else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="incident.py", description=__doc__.split("\n")[0])
    p.add_argument("--root", default=".", help="the repository that holds .release/recipe.json")
    p.add_argument("--skills-dir", action="append", default=None,
                   help="search only this directory for release-conductor (repeatable)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe", help="run the recipe's read-only probes once")
    pr.add_argument("--version", dest="version", default=None,
                    help="the value for a {version} token in a probe command")
    pr.add_argument("--commit", default=None,
                    help="the value for a {commit} token in a probe command")
    pr.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="seconds per command (default %d)" % DEFAULT_TIMEOUT)
    args = p.parse_args(argv)
    if args.cmd == "probe":
        return cmd_probe(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
