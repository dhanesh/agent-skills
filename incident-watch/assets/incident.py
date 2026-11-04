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
          `--only <list>` (repeatable) runs only the named lists. The name has to be
          exactly `health`, `version_probe` or `prod_smoke`; any other name (another recipe
          field such as `rollback`, an unknown name, a path or a command), any extra
          argument after `probe`, and any recipe probe entry whose program word holds a
          {token} is refused with exit 2 before anything runs (R23).

          When a failed probe brings `consecutive_failures` to 2 or more and no record is
          open, the probe opens ONE incident record (R24): records/<id>.json with the
          failing probes (their bounded output), the last release record and the recent
          default-branch CI runs, and appends one intake-signals-jsonl line of kind
          `incident` to signals.jsonl. It prints `INCIDENT: opened <id> <path>`,
          `SIGNAL: <path> kind incident` and the `NEXT: intake import ...` line. A later
          failure while that record is open opens no other record: it appends the probe to
          the open record and prints `INCIDENT: open <id> <path> probes=<n>`. When the CI
          runs are not on disk, the record says so and the probe prints the exact
          `NEXT: gh run list ...` command that fetches them (this tool never runs gh).

Pending (not built here): the rollback command shown word for word in the record (R25;
the seam is rollback_view() and the record's `rollback` field) and
`incident close --by <name>` (R26; the seams are find_open_record() and the record's
`status`, `closed_at` and `closed_by` fields). This tool never runs `build`,
`deploy_staging`, `deploy_prod`, `rollback` or `staging_checks`: the only commands it
reads from the recipe are the ones probe_commands() returns.

State lives in <root>/.skill-contract/incidents/ (git-ignored by its own `*` .gitignore,
checked before any command runs, the ops-intake and idea-validation pattern):
  probes.jsonl      one line per probe run, append-only;
  state.json        {"consecutive_failures", "last_probe_at", "last_result",
                    "open_incident"}: the counter that decides when to open a record, and
                    the id of the open record (or null);
  records/<id>.json one incident record, rewritten atomically. The id is
                    inc-<UTC time of opening, YYYYMMDDTHHMMSSZ>-<6 hex>;
  signals.jsonl     one intake-signals-jsonl line (source `incident-watch`, kind
                    `incident`, severity 4) per opened record, append-only. ops-intake
                    reads it with `intake import --format intake-signals-jsonl --source
                    incident-watch .skill-contract/incidents/signals.jsonl`;
  ci-runs.json      optional, written by the owner: the output of the printed
                    `NEXT: gh run list ...` command. The record reads the CI runs only
                    from this file.

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
import collections  # noqa: E402
import fcntl  # noqa: E402
import hashlib  # noqa: E402
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
RECORDS_DIR = "records"
SIGNALS_FILE = "signals.jsonl"
CI_RUNS_FILE = "ci-runs.json"
OPEN_AFTER = 2  # consecutive failed probes that open an incident record (R24)
MAX_RECORD_PROBES = 20  # failed probes kept in one record, newest last
MAX_CI_RUNS = 10  # recent default-branch CI runs kept in one record
MAX_RELEASES = 20  # release directories listed in one record
SIGNAL_SOURCE = "incident-watch"  # the ops-intake source name the signal line carries
SIGNAL_SEVERITY = 4
INTAKE_CONFIG = ".intake/config.json"
DEFAULT_BRANCH = "main"  # used only when .intake/config.json names no ci.default_branch
# The fields ops-intake's gh-runs-json format asks gh for; the record keeps only these.
CI_RUN_FIELDS = ("databaseId", "workflowName", "headBranch", "headSha", "event", "status",
                 "conclusion", "createdAt", "updatedAt", "url", "attempt")
RELEASE_TOOL = ("release-conductor", "assets/release.py")
DEFAULT_TIMEOUT = 60  # seconds per probe command
TAIL = 2000  # characters of stdout and of stderr kept per command (release.py's TAIL)
# The recipe keys a probe may read. Nothing else in the recipe is ever run (S4).
PROBE_KEYS = ("health", "version_probe", "prod_smoke")
_UNFILLED = re.compile(r"\{(version|commit|env)\}")


class Refused(Exception):
    """A refusal before any probe command runs: exit 2."""


def check_only(names):
    """The recipe lists a probe runs: all of PROBE_KEYS when `names` is empty, else the
    named ones in PROBE_KEYS order. Raises Refused for any name that is not exactly one
    of PROBE_KEYS, so a caller can never name another recipe field, a path or a raw
    command (R23). The check is exact: no case folding, no prefix match."""
    if not names:
        return PROBE_KEYS
    for n in names:
        if n not in PROBE_KEYS:
            raise Refused("--only %r is not a probe list; a probe runs only %s (S4)"
                          % (n, ", ".join(PROBE_KEYS)))
    return tuple(k for k in PROBE_KEYS if k in names)


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

def probe_commands(recipe, keys=PROBE_KEYS):
    """[(name, argv template)] for every command a probe runs, in order: health,
    version_probe, then prod_smoke[0..n-1]. This is the whole set of recipe commands
    incident-watch can run; `keys` (from check_only) can only narrow it."""
    out = []
    for key in PROBE_KEYS:
        if key not in keys:
            continue
        if key == "prod_smoke":
            out += [("prod_smoke[%d]" % i, argv) for i, argv in enumerate(recipe[key])]
        else:
            out.append((key, recipe[key]))
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
        if "{" in template[0] or "}" in template[0]:
            raise Refused("recipe %s program %r holds a {token}: a value would choose the "
                          "command, and a probe runs only the recipe's own commands (S4)"
                          % (name, template[0]))
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
    return {"consecutive_failures": 0, "last_probe_at": None, "last_result": None,
            "open_incident": None}


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


# ── the incident record (R24) ───────────────────────────────────────────────────────────

def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _write_json(path, data):
    """Write `data` to `path` atomically (temp file in the same directory, then rename)."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _records_dir(d):
    return os.path.join(d, RECORDS_DIR)


def find_open_record(d):
    """(id, path, record) of the open incident record, or None. The records directory is
    the truth, not state.json, so a crash between the record write and the state write can
    never open a second record. Also the seam R26's `incident close` uses."""
    rd = _records_dir(d)
    try:
        names = sorted(n for n in os.listdir(rd) if n.endswith(".json"))
    except FileNotFoundError:
        return None
    for name in names:
        path = os.path.join(rd, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("status") == "open":
            return name[:-len(".json")], path, rec
    return None


def failing_streak(d, n):
    """The last `n` probe records from probes.jsonl (at most MAX_RECORD_PROBES), oldest
    first: the failed probes that make up the current streak."""
    n = max(1, min(n, MAX_RECORD_PROBES))
    keep = collections.deque(maxlen=n)
    try:
        with open(os.path.join(d, PROBES_LOG), encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    keep.append(ln)
    except OSError:
        return []
    out = []
    for ln in keep:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("result") == "fail":
            out.append(rec)
    return out


def last_release(root, release):
    """The newest release-result/v1 envelope under .skill-contract/envelopes/, read the
    way ops-intake's sync reads it: a plain file read, the skill-contract checks, and
    release-conductor as the producer. Returns a dict with `available`."""
    cc = getattr(release, "CC", None)
    kind = getattr(release, "RESULT_KIND", None)
    if cc is None or kind is None:
        return {"available": False, "reason": "release-conductor has no envelope reader"}
    d = cc.envelope_dir(root)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except FileNotFoundError:
        names = []
    except OSError:
        return {"available": False, "reason": "cannot list .skill-contract/envelopes"}
    best = None
    for name in names:
        path = os.path.join(d, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        st, err = cc.load_envelope(path)
        if err or not isinstance(st, dict) or st.get("predicateType") != kind:
            continue
        if cc.check_statement(st):
            continue
        pred = st["predicate"]
        if pred.get("wasAttributedTo", {}).get("skill") != "release-conductor":
            continue
        p = pred.get("payload")
        if not (isinstance(p, dict) and isinstance(p.get("version"), str)
                and p.get("outcome") in ("verified", "rolled_back")):
            continue
        key = (pred["generatedAtTime"], pred["id"])
        if best is None or key > best[0]:
            best = (key, {"available": True, "source": "release-result envelope",
                          "path": _rel(root, path), "id": pred["id"],
                          "at": pred["generatedAtTime"], "version": p["version"],
                          "commit": p.get("commit"), "outcome": p["outcome"],
                          "rollback_target": p.get("rollback_target")})
    if best is None:
        return {"available": False,
                "reason": "no release-result/v1 envelope under .skill-contract/envelopes"}
    return best[1]


def release_states(root, release):
    """[{"version", "status"}] for each release directory release-conductor keeps, the way
    its `release status` reads them (read-only: no lock, no state change). A release that
    failed in production has a status here even when it wrote no result envelope."""
    rdir = os.path.join(root, *getattr(release, "RELEASES_DIR",
                                        ".skill-contract/releases").split("/"))
    try:
        names = sorted(n for n in os.listdir(rdir) if os.path.isdir(os.path.join(rdir, n)))
    except OSError:
        return []
    out = []
    for name in names[-MAX_RELEASES:]:
        try:
            status = release.Release.load(root, name).status
        except Exception:  # an unreadable release is reported, never fatal to the probe
            status = "unreadable"
        out.append({"version": name, "status": status})
    return out


def default_branch(root):
    """ci.default_branch from ops-intake's .intake/config.json, else DEFAULT_BRANCH."""
    try:
        with open(os.path.join(root, *INTAKE_CONFIG.split("/")), encoding="utf-8") as f:
            cfg = json.load(f)
        b = cfg.get("ci", {}).get("default_branch")
        if isinstance(b, str) and re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", b) \
                and not b.startswith("-"):
            return b
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_BRANCH


def ci_runs_next(branch):
    """The exact command the owner runs to put the CI runs on disk. It matches ops-intake's
    gh-runs-json command, with its output saved to the incidents directory."""
    return ("gh run list --branch %s --limit %d --json %s > %s/%s"
            % (branch, MAX_CI_RUNS, ",".join(CI_RUN_FIELDS), INCIDENTS_DIR, CI_RUNS_FILE))


def ci_runs(root, d):
    """The recent default-branch CI runs, read only from <d>/ci-runs.json (data already on
    disk: no gh, no network). Returns a dict with `available`; when the runs are not
    available it carries `next`, the command that fetches them."""
    branch = default_branch(root)
    path = os.path.join(d, CI_RUNS_FILE)
    out = {"branch": branch, "path": _rel(root, path)}
    try:
        if os.path.islink(path):
            raise ValueError("is a symlink")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("not a JSON array")
        fetched = datetime.datetime.fromtimestamp(
            os.path.getmtime(path), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except FileNotFoundError:
        return dict(out, available=False, reason="%s is not on disk" % out["path"],
                    next=ci_runs_next(branch))
    except (OSError, ValueError) as e:
        return dict(out, available=False, reason="%s is unreadable: %s" % (out["path"], e),
                    next=ci_runs_next(branch))
    runs = [{k: r.get(k) for k in CI_RUN_FIELDS} for r in data
            if isinstance(r, dict) and r.get("headBranch") == branch]
    runs.sort(key=lambda r: str(r.get("createdAt") or ""), reverse=True)
    return dict(out, available=True, fetched_at=fetched, runs=runs[:MAX_CI_RUNS])


def rollback_view(root, release):
    """The seam for R25: the recipe's rollback argv shown word for word and the
    release-conductor command that asks the owner. Not built here; returns None. The
    rollback is shown, never run (D1, B1)."""
    return None


def new_record_id(at, probes):
    stamp = at.replace("-", "").replace(":", "")
    digest = hashlib.sha256(json.dumps(probes, sort_keys=True).encode("utf-8")).hexdigest()
    return "inc-%s-%s" % (stamp, digest[:6])


def _failed_names(probe):
    return [c.get("name") for c in probe.get("commands", []) if c.get("result") != "pass"]


def signal_line(rid, record_rel, probes, opened_at):
    """One intake-signals-jsonl record of kind `incident` (ops-intake
    references/formats.md). The evidence names the failed commands, their exit codes and
    the record path; output tails stay in the record, not in the signal."""
    last = probes[-1] if probes else {}
    names = sorted({n for p in probes for n in _failed_names(p) if n})
    parts = ["%s %s rc=%s" % (c.get("name"), c.get("result"), c.get("rc"))
             for c in last.get("commands", []) if c.get("result") != "pass"]
    text = "probe %s: %s; record %s" % (last.get("at", opened_at), "; ".join(parts),
                                       record_rel)
    return {"source": SIGNAL_SOURCE, "source_id": rid, "url": "", "kind": "incident",
            "title": "incident %s: %d consecutive failed production probes (%s)"
                     % (rid, len(probes), ", ".join(names) or "none"),
            "severity": SIGNAL_SEVERITY,
            "first_seen": probes[0].get("at", opened_at) if probes else opened_at,
            "last_seen": opened_at,
            "evidence": [{"text": text, "key": "%s:open" % rid}]}


def open_record(root, d, release, state, record):
    """Open one incident record for the current failing streak and write its signal.
    Returns (id, path, signal path, ci_runs)."""
    probes = failing_streak(d, state["consecutive_failures"]) or [record]
    at = record["at"]
    rid = new_record_id(at, probes)
    rd = _records_dir(d)
    os.makedirs(rd, exist_ok=True)
    path = os.path.join(rd, rid + ".json")
    ci = ci_runs(root, d)
    rec = {"id": rid, "status": "open", "opened_at": at, "updated_at": at,
           "open_after": OPEN_AFTER, "consecutive_failures": state["consecutive_failures"],
           "probe_count": len(probes), "probes": probes,
           "last_release": last_release(root, release),
           "releases": release_states(root, release),
           "ci_runs": ci,
           "rollback": rollback_view(root, release),  # R25 fills this
           "closed_at": None, "closed_by": None,  # R26 fills these
           "signal": {"path": "%s/%s" % (INCIDENTS_DIR, SIGNALS_FILE),
                      "source": SIGNAL_SOURCE, "source_id": rid}}
    _write_json(path, rec)
    line = signal_line(rid, _rel(root, path), probes, at)
    sig = os.path.join(d, SIGNALS_FILE)
    with open(sig, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
    return rid, path, sig, ci


def append_to_record(root, d, found, state, record):
    """Add the newest failed probe to the open record (no new record, no new signal).
    Refresh the CI runs when the record still has none. Returns (id, path, n, ci_runs)."""
    rid, path, rec = found
    probes = rec.get("probes") if isinstance(rec.get("probes"), list) else []
    probes.append(record)
    rec["probes"] = probes[-MAX_RECORD_PROBES:]
    rec["probe_count"] = int(rec.get("probe_count") or 0) + 1
    rec["updated_at"] = record["at"]
    rec["consecutive_failures"] = state["consecutive_failures"]
    ci = rec.get("ci_runs") or {}
    if not ci.get("available"):
        ci = ci_runs(root, d)
        rec["ci_runs"] = ci
    _write_json(path, rec)
    return rid, path, rec["probe_count"], ci


def after_probe(root, d, release, state, record):
    """R24: after OPEN_AFTER or more consecutive failed probes, open ONE incident record
    and write one `incident` signal; while a record is open, a later failure only appends
    its probe to it. Returns the lines to print after PROBE_RESULT (empty on a pass or on
    the first failure). Called under the probe lock."""
    if record["result"] != "fail" or state["consecutive_failures"] < OPEN_AFTER:
        return []
    found = find_open_record(d)
    if found:
        rid, path, n, ci = append_to_record(root, d, found, state, record)
        state["open_incident"] = rid
        lines = ["INCIDENT: open %s %s probes=%d" % (rid, _rel(root, path), n)]
    else:
        rid, path, sig, ci = open_record(root, d, release, state, record)
        state["open_incident"] = rid
        lines = ["INCIDENT: opened %s %s" % (rid, _rel(root, path)),
                 "SIGNAL: %s kind incident" % _rel(root, sig),
                 "NEXT: intake import --format intake-signals-jsonl --source %s %s"
                 % (SIGNAL_SOURCE, _rel(root, sig))]
    if not ci.get("available"):
        lines.append("NEXT: %s" % ci["next"])
    return lines


def cmd_probe(args):
    root = os.path.abspath(args.root)
    try:
        if args.extra:
            raise Refused("unexpected argument %r after probe: a probe takes no command; "
                          "it runs only %s (S4)" % (args.extra[0], ", ".join(PROBE_KEYS)))
        keys = check_only(args.only)
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
        commands = expand_all(release, probe_commands(recipe, keys), args.version, args.commit)
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
            try:
                incident_lines = after_probe(root, d, release, state, record)
                if incident_lines:
                    write_state(d, state)
            except OSError as e:
                incident_lines = ["INCIDENT: error: cannot write the incident record: %s" % e]
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
    for ln in incident_lines:
        print(ln)
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
    pr.add_argument("--only", action="append", default=None, metavar="LIST",
                    help="run only this list: health, version_probe or prod_smoke "
                         "(repeatable); any other name is refused with exit 2")
    args, extra = p.parse_known_args(argv)
    args.extra = extra
    if args.cmd == "probe":
        return cmd_probe(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
