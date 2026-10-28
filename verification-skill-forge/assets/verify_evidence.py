#!/usr/bin/env python3
"""verify_evidence.py — the evidence recorder a generated verify-<app> skill ships.

verification-skill-forge copies this file, byte for byte, into every verify skill it
generates (<verify skill>/scripts/verify_evidence.py) and `forge.py lint` fails a copy
that drifted. factory-conductor reads what it writes. stdlib only, offline.

Usage:
    python3 verify_evidence.py port    --instance I [--base 41000] [--span 1000]
                                                        # PORT: <n>  (claims it for I)
    python3 verify_evidence.py claim   --instance I --port N   # PORT: <n> | exit 3 if taken
    python3 verify_evidence.py release --instance I             # RELEASED: <n> ...
    python3 verify_evidence.py doctor  --instance I --ok|--fail --check NAME=pass|fail ...
                                        [--verifier ID] [--worktree W]
                                                        # DOCTOR: <path>
    python3 verify_evidence.py record  --instance I --feature F --verifier ID
                                        --result pass|fail --action TEXT --observed TEXT
                                        --side-effect TEXT [--side-effect TEXT ...]
                                        --artifact PATH [--artifact PATH ...] [--worktree W]
                                                        # EVIDENCE: <path>
    python3 verify_evidence.py record  --instance I --feature F --verifier ID
                                        --tier proved|bounded|property --tool NAME
                                        --clauses C<n> --cmd ARGV [--timeout SECS]
                                        [--worktree W]  # PROOF: <tier> <path>

A proof record (--tier) is taken by running the proof tool here: ARGV is split with shlex
(no shell) and run from the worktree root under --timeout, and the record is written only
when it exits 0 on the same HEAD. Its stdout and stderr are pinned as artifacts; nothing
the caller supplies stands in for them, so --result, --action, --observed, --side-effect
and --artifact are refused with --tier. The record is
<evidence dir>/<instance>/<feature-id>/<sha>/proof-<tier>-<tool>-<clause>.json, with the
evidence.json fields and artifact shape plus tier, tool, clauses, argv and exit_status.
--tool must be on its tier's list in TIER_TOOLS (proved: verus; bounded: kani; property:
proptest, hypothesis, fast-check, rapid) and --clauses must name exactly one clause.

Where things go. Evidence lives under the evidence directory: $VERIFY_EVIDENCE_DIR when
set, else <git toplevel of --worktree (default: cwd)>/.verify. Each record is
<evidence dir>/<instance>/<feature-id>/<sha>/evidence.json; each doctor result is
<evidence dir>/<instance>/doctor/<sha>/doctor.json. <sha> is the HEAD commit of --worktree,
read with git: never a flag, so a record cannot name a head it was not taken from.
Artifacts are copied into the record's directory and pinned by sha256; a file that
already sits there is pinned where it is. Port claims and other per-instance run state
live under <evidence dir>/../.verify-run/, which cleanup may delete; nothing here ever
deletes evidence.

Exit 0 on success, 1 when a proof command did not exit 0 on the head, 2 on invalid
input, 3 when a port is taken by another instance. Every check runs before any write.
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import zlib

SCHEMA = "verify-evidence/v1"
ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*\Z")
INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
RESERVED = ("doctor", "findings")
TIERS = ("proved", "bounded", "property")
# The tools each proof tier accepts for --tool. A tool earns a tier by what its pass
# means: verus proves for all inputs, kani checks every input up to a stated bound, and
# the property tools sample inputs. CrossHair is not bounded: its bound is a time budget.
TIER_TOOLS = {
    "proved": ("verus",),
    "bounded": ("kani",),
    "property": ("proptest", "hypothesis", "fast-check", "rapid"),
}
CLAUSE_RE = re.compile(r"^C[1-9][0-9]*\Z")
RUNTIME_ONLY = ("result", "action", "observed", "side_effect", "artifact")


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(msg, code=2):
    sys.stderr.write("verify_evidence: %s\n" % msg)
    return code


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


def head_sha(worktree):
    """The HEAD commit of worktree, or None."""
    r = _git(worktree, "rev-parse", "--verify", "HEAD^{commit}")
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and SHA_RE.match(sha) else None


def evidence_dir(worktree):
    env = os.environ.get("VERIFY_EVIDENCE_DIR")
    if env:
        return os.path.abspath(env)
    r = _git(worktree, "rev-parse", "--show-toplevel")
    top = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else worktree
    return os.path.join(os.path.abspath(top), ".verify")


def run_dir(worktree):
    """Per-instance run state (port claims): a sibling of the evidence directory."""
    return os.path.join(os.path.dirname(evidence_dir(worktree)), ".verify-run")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


# ── ports ────────────────────────────────────────────────────────────────────
def _lock_path(worktree, port):
    return os.path.join(run_dir(worktree), "ports", "%d.lock" % port)


def _holder(lock):
    try:
        with open(lock, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _bindable(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def claim_port(worktree, instance, port):
    """Claim port for instance. True when it is (now or already) ours, False when another
    instance holds it. The claim is an O_EXCL lock file, so two owners racing for the
    same port cannot both win."""
    lock = _lock_path(worktree, port)
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return _holder(lock) == instance
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(instance + "\n")
    return True


def cmd_port(a):
    if not INSTANCE_RE.match(a.instance):
        return _fail("bad instance name %r" % a.instance)
    wt = os.path.abspath(a.worktree)
    start = a.base + zlib.crc32(a.instance.encode()) % a.span
    for i in range(a.span):
        port = a.base + (start - a.base + i) % a.span
        lock = _lock_path(wt, port)
        if _holder(lock) == a.instance:
            print("PORT: %d" % port)
            return 0
        if os.path.exists(lock) or not _bindable(port):
            continue
        if claim_port(wt, a.instance, port):
            print("PORT: %d" % port)
            return 0
    return _fail("no free port in [%d, %d)" % (a.base, a.base + a.span), 3)


def cmd_claim(a):
    if not INSTANCE_RE.match(a.instance):
        return _fail("bad instance name %r" % a.instance)
    wt = os.path.abspath(a.worktree)
    if claim_port(wt, a.instance, a.port):
        print("PORT: %d" % a.port)
        return 0
    return _fail("port %d is held by instance %s" % (a.port, _holder(_lock_path(wt, a.port))), 3)


def cmd_release(a):
    wt = os.path.abspath(a.worktree)
    d = os.path.join(run_dir(wt), "ports")
    released = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        path = os.path.join(d, name)
        if _holder(path) == a.instance:
            os.unlink(path)
            released.append(name.split(".")[0])
    print("RELEASED: %s" % (" ".join(released) or "none"))
    return 0


# ── records ──────────────────────────────────────────────────────────────────
def _instance_and_head(a):
    if not INSTANCE_RE.match(a.instance):
        return None, None, _fail("bad instance name %r" % a.instance)
    wt = os.path.abspath(a.worktree)
    sha = head_sha(wt)
    if sha is None:
        return None, None, _fail("%s is not a git work tree with a commit" % wt)
    return wt, sha, None


def cmd_doctor(a):
    wt, sha, err = _instance_and_head(a)
    if err is not None:
        return err
    checks = {}
    for item in a.check or []:
        name, _, value = item.partition("=")
        if not name or value not in ("pass", "fail"):
            return _fail("--check takes NAME=pass|fail, got %r" % item)
        checks[name] = value
    if not checks:
        return _fail("doctor needs at least one --check (process, version, port, auth)")
    ok = a.ok and all(v == "pass" for v in checks.values())
    path = os.path.join(evidence_dir(wt), a.instance, "doctor", sha, "doctor.json")
    _write_json(path, {"schema": SCHEMA, "kind": "doctor", "instance": a.instance,
                       "sha": sha, "ok": ok, "checks": checks, "verifier": a.verifier,
                       "captured_at": _now()})
    print("DOCTOR: %s %s" % ("ok" if ok else "red", path))
    return 0


def _record_target(a):
    """(wt, sha, err) after the checks every record shares: instance, head, feature."""
    wt, sha, err = _instance_and_head(a)
    if err is not None:
        return None, None, err
    if not ID_RE.match(a.feature) or a.feature in RESERVED:
        return None, None, _fail("bad feature id %r (lowercase kebab-case, not %s)"
                                 % (a.feature, " or ".join(RESERVED)))
    return wt, sha, None


def cmd_record(a):
    if a.tier is not None:
        return cmd_record_tier(a)
    wt, sha, err = _record_target(a)
    if err is not None:
        return err
    for name in ("verifier", "action", "observed"):
        if not (getattr(a, name) or "").strip():
            return _fail("--%s is required and cannot be blank" % name)
    effects = [s for s in (a.side_effect or []) if s.strip()]
    if not effects:
        return _fail("at least one --side-effect is required (write 'none: <why>' when the "
                     "feature has none): visible state alone is not proof")
    if not a.artifact:
        return _fail("at least one --artifact is required: the captured action and state")
    out = os.path.join(evidence_dir(wt), a.instance, a.feature, sha)
    names = [os.path.basename(os.path.abspath(x)) for x in a.artifact]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        # Each artifact lands in the record's directory under its basename: two with one
        # name would overwrite each other and the first hash would no longer match.
        return _fail("artifacts share a file name (%s): rename one before recording"
                     % ", ".join(dup))
    for src in a.artifact:
        if not os.path.isfile(os.path.abspath(src)):
            return _fail("artifact %s does not exist" % os.path.abspath(src))
    os.makedirs(out, exist_ok=True)
    arts = []
    for src in a.artifact:
        src = os.path.abspath(src)
        if os.path.dirname(src) != out:
            dest = os.path.join(out, os.path.basename(src))
            shutil.copyfile(src, dest)
            src = dest
        arts.append({"path": os.path.basename(src), "sha256": sha256_file(src)})
    path = os.path.join(out, "evidence.json")
    _write_json(path, {"schema": SCHEMA, "kind": "evidence", "instance": a.instance,
                       "feature": a.feature, "sha": sha, "verifier": a.verifier,
                       "result": a.result, "action": a.action, "observed": a.observed,
                       "side_effects": effects, "artifacts": arts, "captured_at": _now()})
    print("EVIDENCE: %s %s" % (a.result, path))
    return 0


def _worktree_root(wt):
    r = _git(wt, "rev-parse", "--show-toplevel")
    top = r.stdout.strip()
    return os.path.abspath(top) if r.returncode == 0 and top else None


def cmd_record_tier(a):
    """A proof record: run the tool on the head, and write only on exit 0."""
    wt, sha, err = _record_target(a)
    if err is not None:
        return err
    given = ["--" + n.replace("_", "-") for n in RUNTIME_ONLY if getattr(a, n) is not None]
    if given:
        return _fail("%s cannot be used with --tier: a proof record holds only what the "
                     "recorder saw the tool do; drop them" % ", ".join(given))
    for flag, name in (("verifier", "verifier"), ("tool", "tool"), ("cmd", "proof_cmd")):
        if not (getattr(a, name) or "").strip():
            return _fail("--%s is required with --tier and cannot be blank" % flag)
    allowed = TIER_TOOLS[a.tier]
    if a.tool not in allowed:
        return _fail("--tool %r is not allowed for --tier %s: use %s, or record the proof "
                     "at the tier its tool earns" % (a.tool, a.tier, ", ".join(allowed)))
    # One record proves one clause: a list, or nothing, would credit clauses the run
    # may not cover.
    named = [c for c in re.split(r"[\s,;]+", a.clauses or "") if c]
    if len(named) != 1:
        return _fail("--clauses %r names %d clauses: exactly one clause is required, such "
                     "as C1; record each clause separately" % (a.clauses or "", len(named)))
    if not CLAUSE_RE.match(a.clauses):
        return _fail("bad --clauses %r: name one contract clause id, such as C1"
                     % a.clauses)
    if a.timeout <= 0:
        return _fail("--timeout must be a positive number of seconds, got %d" % a.timeout)
    try:
        argv = shlex.split(a.proof_cmd)
    except ValueError as e:
        return _fail("--cmd does not parse as a command line (%s): fix its quoting" % e)
    if not argv:
        return _fail("--cmd is required with --tier and cannot be blank")
    root = _worktree_root(wt)
    if root is None:
        return _fail("cannot find the git toplevel of %s" % wt)
    try:
        run = subprocess.run(argv, cwd=root, capture_output=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        return _fail("%s did not finish in %d seconds: nothing recorded; raise --timeout "
                     "or narrow the proof" % (argv[0], a.timeout), 1)
    except OSError as e:
        return _fail("cannot run %s (%s): nothing recorded; check the tool is installed "
                     "and on PATH" % (argv[0], e), 1)
    if run.returncode != 0:
        return _fail("%s exited %d on %s: nothing recorded; a proof tier needs the tool "
                     "to pass on the head" % (argv[0], run.returncode, sha[:12]), 1)
    if head_sha(wt) != sha:
        return _fail("HEAD moved from %s while %s ran: nothing recorded; record again on "
                     "a fixed head" % (sha[:12], argv[0]), 1)
    out = os.path.join(evidence_dir(wt), a.instance, a.feature, sha)
    stem = "proof-%s-%s-%s" % (a.tier, a.tool, a.clauses)
    os.makedirs(out, exist_ok=True)
    arts = []
    for ext, data in (("stdout", run.stdout), ("stderr", run.stderr)):
        name = "%s.%s" % (stem, ext)
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
        arts.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    path = os.path.join(out, stem + ".json")
    # kind stays "evidence" and result "pass" so the conductor's record check (sha,
    # artifacts, verifier) reads a proof record exactly as it reads evidence.json.
    _write_json(path, {"schema": SCHEMA, "kind": "evidence", "instance": a.instance,
                       "feature": a.feature, "sha": sha, "verifier": a.verifier,
                       "result": "pass", "tier": a.tier, "tool": a.tool,
                       "clauses": [a.clauses], "argv": argv, "cwd": root,
                       "timeout": a.timeout, "exit_status": run.returncode,
                       "artifacts": arts, "captured_at": _now()})
    print("PROOF: %s %s" % (a.tier, path))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="verify_evidence.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    parsers = {}
    for name in ("port", "claim", "release", "doctor", "record"):
        s = parsers[name] = sub.add_parser(name)
        s.add_argument("--instance", required=True)
        s.add_argument("--worktree", default=".")
        if name == "port":
            s.add_argument("--base", type=int, default=41000)
            s.add_argument("--span", type=int, default=1000)
        if name == "claim":
            s.add_argument("--port", type=int, required=True)
        if name == "doctor":
            g = s.add_mutually_exclusive_group(required=True)
            g.add_argument("--ok", action="store_true")
            g.add_argument("--fail", action="store_true")
            s.add_argument("--check", action="append")
            s.add_argument("--verifier")
        if name == "record":
            s.add_argument("--feature", required=True)
            s.add_argument("--verifier", required=True)
            s.add_argument("--result", choices=("pass", "fail"))
            s.add_argument("--action")
            s.add_argument("--observed")
            s.add_argument("--side-effect", action="append")
            s.add_argument("--artifact", action="append")
            s.add_argument("--tier", choices=TIERS)
            s.add_argument("--tool")
            s.add_argument("--clauses")
            s.add_argument("--cmd", dest="proof_cmd")
            s.add_argument("--timeout", type=int, default=900)
    a = p.parse_args(argv)
    if a.cmd == "record" and a.tier is None:
        # A runtime record keeps its required flags; only --tier lifts them.
        missing = ["--" + n for n in ("result", "action", "observed")
                   if getattr(a, n) is None]
        if missing:
            parsers["record"].error("the following arguments are required: %s"
                                    % ", ".join(missing))
        stray = [f for f, n in (("--tool", "tool"), ("--clauses", "clauses"),
                                ("--cmd", "proof_cmd")) if getattr(a, n) is not None]
        if stray:
            parsers["record"].error("%s only apply to a proof record: add --tier"
                                    % ", ".join(stray))
    return {"port": cmd_port, "claim": cmd_claim, "release": cmd_release,
            "doctor": cmd_doctor, "record": cmd_record}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
