#!/bin/sh
# ci-enforce.sh <bundle> [base-ref] [--tolerance-days N]
#
# Layer 2 of the enforcement model (references/enforcement.md): build a
# changeset from git for every commit between <base-ref> and HEAD, resolve each
# commit's author to a team, and hand it to `okf_catalog.py enforce`.
#
# The mode-level guards protect the honest path only. This is the check that
# actually stops a provider from certifying its own capability, so wire it as a
# REQUIRED status check — and remember that an admin can still bypass it, which
# is why `audit` reconciles against git independently.
#
# Author -> team resolution, in order: the git trailer `Okf-Team:`, a Team
# document whose contacts.github_team / contacts.lead / claimed_by matches the
# commit author, else the single teams/<id>/ prefix the commit touched.
#
# Exits 0 when clean, 1 on any violation, 2 on usage/environment problems.
set -eu

BUNDLE="${1:?usage: ci-enforce.sh <bundle> [base-ref] [--tolerance-days N]}"
BASE="${2:-origin/main}"
shift 2 2>/dev/null || shift 1 2>/dev/null || true

TOLERANCE=2
while [ "$#" -gt 0 ]; do
    case "$1" in
        --tolerance-days) TOLERANCE="${2:?--tolerance-days needs a value}"; shift 2 ;;
        *) shift ;;
    esac
done

command -v git >/dev/null 2>&1 || { echo "ENFORCE_RESULT: FAIL — git not found"; exit 2; }
[ -d "$BUNDLE" ] || { echo "ENFORCE_RESULT: FAIL — bundle not found: $BUNDLE"; exit 2; }

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CLI="$HERE/../assets/okf_catalog.py"
[ -f "$CLI" ] || { echo "ENFORCE_RESULT: FAIL — $CLI missing"; exit 2; }

CHANGES=$(mktemp)
trap 'rm -f "$CHANGES"' EXIT

# The changeset is assembled in python (stdlib) so quoting and JSON escaping are
# not a shell problem; git is called for the facts only.
BUNDLE="$BUNDLE" BASE="$BASE" python3 - "$CHANGES" <<'PY'
import json, os, re, subprocess, sys

bundle, base = os.environ["BUNDLE"], os.environ["BASE"]
out_path = sys.argv[1]


def git(*args):
    return subprocess.run(["git", "-C", bundle, *args], capture_output=True,
                          text=True).stdout


def show(ref, path):
    r = subprocess.run(["git", "-C", bundle, "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


# team documents: handle -> team_id
handles = {}
for line in git("ls-files", "teams/*/team.md").splitlines():
    text = show("HEAD", line) or ""
    team_id = re.search(r"^team_id:\s*(\S+)", text, re.M)
    if not team_id:
        continue
    tid = team_id.group(1).strip('"')
    for key in ("github_team", "lead", "claimed_by"):
        m = re.search(rf"^\s*{key}:\s*(\S+)", text, re.M)
        if m:
            handles[m.group(1).strip('"@').lower()] = tid

commits = []
revs = git("rev-list", f"{base}..HEAD").split()
for sha in revs:
    author = git("show", "-s", "--format=%an|%ae|%aI", sha).strip().split("|")
    name, email, when = (author + ["", "", ""])[:3]
    handle = name
    paths = [p for p in git("show", "--name-only", "--format=", sha).split()
             if p.endswith(".md")]
    trailer = re.search(r"^Okf-Team:\s*(\S+)$",
                        git("show", "-s", "--format=%B", sha), re.M)
    team = trailer.group(1) if trailer else None
    if not team:
        for candidate in (name, email.split("@")[0], email):
            if candidate and candidate.lower() in handles:
                team = handles[candidate.lower()]
                break
    if not team:
        touched = {p.split("/")[1] for p in paths
                   if p.startswith("teams/") and len(p.split("/")) > 2}
        team = touched.pop() if len(touched) == 1 else None
    files = []
    for path in paths:
        content = show(sha, path)
        if content is None:
            continue
        entry = {"path": path, "content": content}
        previous = show(f"{sha}~1", path)
        if previous is not None:
            entry["previous"] = previous
        files.append(entry)
    if files:
        commits.append({"sha": sha, "author_handle": handle, "author_team": team,
                        "committed_at": when, "files": files})

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump({"commits": commits}, fh)
print(f"CHANGESET: {len(commits)} commit(s) since {base}")
PY

python3 "$CLI" enforce "$BUNDLE" --changes "$CHANGES" --tolerance-days "$TOLERANCE"
