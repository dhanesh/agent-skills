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

Where things go. Evidence lives under the evidence directory: $VERIFY_EVIDENCE_DIR when
set, else <git toplevel of --worktree (default: cwd)>/.verify. Each record is
<evidence dir>/<instance>/<feature-id>/<sha>/evidence.json; each doctor result is
<evidence dir>/<instance>/doctor/<sha>/doctor.json. <sha> is the HEAD commit of --worktree,
read with git: never a flag, so a record cannot name a head it was not taken from.
Artifacts are copied into the record's directory and pinned by sha256; a file that
already sits there is pinned where it is. Port claims and other per-instance run state
live under <evidence dir>/../.verify-run/, which cleanup may delete; nothing here ever
deletes evidence.

Exit 0 on success, 2 on invalid input, 3 when a port is taken by another instance.
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
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


def cmd_record(a):
    wt, sha, err = _instance_and_head(a)
    if err is not None:
        return err
    if not ID_RE.match(a.feature) or a.feature in RESERVED:
        return _fail("bad feature id %r (lowercase kebab-case, not %s)"
                     % (a.feature, " or ".join(RESERVED)))
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
    os.makedirs(out, exist_ok=True)
    arts = []
    names = [os.path.basename(os.path.abspath(x)) for x in a.artifact]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        # Each artifact lands in the record's directory under its basename: two with one
        # name would overwrite each other and the first hash would no longer match.
        return _fail("artifacts share a file name (%s): rename one before recording"
                     % ", ".join(dup))
    for src in a.artifact:
        src = os.path.abspath(src)
        if not os.path.isfile(src):
            return _fail("artifact %s does not exist" % src)
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="verify_evidence.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("port", "claim", "release", "doctor", "record"):
        s = sub.add_parser(name)
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
            s.add_argument("--result", required=True, choices=("pass", "fail"))
            s.add_argument("--action", required=True)
            s.add_argument("--observed", required=True)
            s.add_argument("--side-effect", action="append")
            s.add_argument("--artifact", action="append")
    a = p.parse_args(argv)
    return {"port": cmd_port, "claim": cmd_claim, "release": cmd_release,
            "doctor": cmd_doctor, "record": cmd_record}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
