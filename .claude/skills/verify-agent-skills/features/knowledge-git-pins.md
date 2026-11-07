# knowledge-git-pins: check git source pins without running repo-controlled git config

- id: knowledge-git-pins
- proven: no
- anchors: feynman-walkthrough/assets/okf.py:_git, feynman-walkthrough/assets/okf.py:git_drift, feynman-walkthrough/assets/okf.py:_read_pinned_sources, feynman-walkthrough/assets/okf.py:diff_okf, feynman-walkthrough/assets/okf.py:fingerprint, knowledge-gardener/assets/garden.py:_git, knowledge-gardener/assets/garden.py:git_drift, knowledge-gardener/assets/garden.py:extract_sources, knowledge-gardener/assets/garden.py:assess_source

## What it is

**Status: planned (run 5).** `okf.py status`, `okf.py diff` (feynman-walkthrough) and
`garden.py sweep` (knowledge-gardener) check each git source pin of an OKF explainer
against the source repo. After run 5, these rules apply:

- Every git command that either tool runs carries
  `-c core.fsmonitor=false -c core.hooksPath=/dev/null`. This is the same idea as
  `GIT_SAFE` in `factory-pipeline/assets/pipeline.py`. A source repo can set
  `core.fsmonitor` to a command in its own config. That command never runs.
- A git pin whose fingerprint is not a 40-hex commit SHA (with an optional `+dirty`
  suffix) is an error for that source. No git command receives that fingerprint. So a
  fingerprint such as `--output=<path>` cannot act as a git option and creates no file.
- A normal git pin still works: FRESH after `init`, then STALE after a new commit, with
  the changed file named.

Today (before run 5), both tools run plain `git ...`. A repo's `core.fsmonitor` command
runs on `git status`. The pinned fingerprint goes into `git diff <pin>..HEAD` with no
check, so `--output=<path>` makes git write a file named `<path>..HEAD`.

## How to reach it

- `python3 feynman-walkthrough/assets/okf.py --root <bundle> init <Subject> --source <repo>`
  pins the source. The explainer is `<bundle>/<slug>/explainer.md`. Its `sources:` list
  holds `fingerprint: <sha>[+dirty]`.
- `python3 feynman-walkthrough/assets/okf.py --root <bundle> status <slug>` prints
  `SOURCE: git <repo> -> <STATE>` and `OKF_STATUS: <slug> <STATE>`. It exits 1 on STALE.
- `python3 feynman-walkthrough/assets/okf.py --root <bundle> diff <slug>` prints
  `PINNED:`, `CURRENT:`, the changed files and `OKF_DIFF: <slug> <STATE>`.
- `python3 knowledge-gardener/assets/garden.py sweep <bundle>` prints `SUBJECT:`,
  `SOURCE:` and `GARDEN_RESULT: <STATE> (...)`. It exits 1 on STALE or ERROR.

The fixture is a repo `R` that holds an embedded bare repo `R/evil`. That bare repo is
set to `core.bare false` with `core.worktree ..`, so git treats `R/evil` as a normal
repo. Its config is under the fixture author's control. A `git` shim on `PATH` logs the
argv of every git call that the tools make.

## Drive it

Set up the instance, an empty git config, the git shim and the hostile fixture. The pin
is taken before `core.fsmonitor` is set.

```sh
KG="$WT/.verify-run/$INSTANCE/kg"
OUT="$WT/.verify-run/$INSTANCE"
PY="$(python3 -c 'import sys; print(sys.executable)')"   # resolve before HOME changes
mkdir -p "$KG/home" "$KG/bin"
: > "$KG/home/gitconfig"
export HOME="$KG/home" GIT_CONFIG_GLOBAL="$KG/home/gitconfig" GIT_CONFIG_NOSYSTEM=1
REALGIT="$(command -v git)"
printf '#!/bin/sh\nprintf "%%s " "$@" >> "%s/git-argv.log"\necho >> "%s/git-argv.log"\nexec "%s" "$@"\n' \
  "$KG" "$KG" "$REALGIT" > "$KG/bin/git"
chmod +x "$KG/bin/git"
SHIMPATH="$KG/bin:$PATH"
R="$KG/R"
G() { git --git-dir="$R/evil" "$@"; }
git init -q "$R"
git -C "$R" init -q --bare evil
G config core.bare false
G config core.worktree ..
(cd "$R" && G --work-tree=. read-tree --empty && G -c user.name=t -c user.email=t@t commit -q --allow-empty -m init)
"$PY" feynman-walkthrough/assets/okf.py --root "$R/knowledge" init Evil --source "$R/evil"
G config core.fsmonitor "touch $KG/fsmonitor-ran"

# kgp NAME ARGV...: capture ARGV through the git shim, then save the
# instance listing, the repo listing, the git argv log and the argv lines
# that lack either safe switch.
kgp() {
  name="$1"; shift
  rm -f "$KG/git-argv.log"
  "$PY" "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name "$name" -- \
    env PATH="$SHIMPATH" "$@"
  ls -A "$KG" > "$OUT/$name-ls.txt"
  ls -A "$R" > "$OUT/$name-repo-ls.txt"
  touch "$KG/git-argv.log"
  cp "$KG/git-argv.log" "$OUT/$name-git-argv.log"
  { grep -v -e 'core.fsmonitor=false' "$KG/git-argv.log"
    grep -v -e 'core.hooksPath=/dev/null' "$KG/git-argv.log"; } > "$OUT/$name-unsafe.txt"
  find "$KG" -maxdepth 1 \( -name fsmonitor-ran -o -name 'clobber*' \) -delete
}
kgp kgp-okf-status "$PY" feynman-walkthrough/assets/okf.py --root "$R/knowledge" status evil
kgp kgp-okf-diff "$PY" feynman-walkthrough/assets/okf.py --root "$R/knowledge" diff evil
kgp kgp-garden-sweep "$PY" knowledge-gardener/assets/garden.py sweep "$R/knowledge"
```

- `kgp-okf-status-ls.txt`, `kgp-okf-diff-ls.txt`, `kgp-garden-sweep-ls.txt`: no
  `fsmonitor-ran` line. The fsmonitor command did not run.
- `kgp-*-repo-ls.txt`: only `.git`, `evil` and `knowledge`. There are no stray files
  such as `1`, `2` or a long number (the arguments git passes to an fsmonitor hook).
- `kgp-*-git-argv.log`: at least one line each (the tools did call git).
- `kgp-*-unsafe.txt`: empty for all three. Every git call carried
  `core.fsmonitor=false` and `core.hooksPath=/dev/null`.
- The three captures still report the source (a `SOURCE: git .../R/evil` line). The
  state is not the point of this step.

Now remove the fsmonitor setting and replace the pinned fingerprint with an option.

```sh
G config --unset core.fsmonitor
EXP="$R/knowledge/evil/explainer.md"
sed -i.bak "s|^\( *\)fingerprint:.*|\1fingerprint: --output=$KG/clobber|" "$EXP" && rm -f "$EXP.bak"
grep -n 'fingerprint:' "$EXP" > "$OUT/kgp-badpin-line.txt"
kgp kgp-badpin-okf-status "$PY" feynman-walkthrough/assets/okf.py --root "$R/knowledge" status evil
kgp kgp-badpin-okf-diff "$PY" feynman-walkthrough/assets/okf.py --root "$R/knowledge" diff evil
kgp kgp-badpin-garden-sweep "$PY" knowledge-gardener/assets/garden.py sweep "$R/knowledge"
```

- `kgp-badpin-line.txt`: one line, `fingerprint: --output=<KG>/clobber`. The fixture
  is in place.
- `kgp-badpin-*-ls.txt`: no line that starts with `clobber`. No file was created.
- `kgp-badpin-*-git-argv.log`: no line contains `--output=`. No git command received
  the fingerprint. (The log can be empty if the tool rejects the pin before it runs git.)
- `kgp-badpin-*-unsafe.txt`: empty.
- `kgp-badpin-okf-status`: the source is reported as an error, not as FRESH or STALE,
  and the exit code is not 0.
- `kgp-badpin-okf-diff`: the source or the subject is reported as an error, not as
  FRESH or STALE. The output has no `PINNED: --output=` line followed by a git diff
  result.
- `kgp-badpin-garden-sweep`: exit 1, the subject or the source is `ERROR`, and the last
  line is `GARDEN_RESULT: ERROR (...)`.

Last, the regression: a normal repo with a bundle outside it.

```sh
N="$KG/N"
git init -q "$N"
echo one > "$N/a.txt"
git -C "$N" add a.txt
git -C "$N" -c user.name=t -c user.email=t@t commit -q -m one
"$PY" feynman-walkthrough/assets/okf.py --root "$KG/nk" init Normal --source "$N"
"$PY" "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name kgp-normal-status-1 -- \
  "$PY" feynman-walkthrough/assets/okf.py --root "$KG/nk" status normal
"$PY" "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name kgp-normal-sweep-1 -- \
  "$PY" knowledge-gardener/assets/garden.py sweep "$KG/nk"
echo two > "$N/b.txt"
git -C "$N" add b.txt
git -C "$N" -c user.name=t -c user.email=t@t commit -q -m two
"$PY" "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name kgp-normal-status-2 -- \
  "$PY" feynman-walkthrough/assets/okf.py --root "$KG/nk" status normal
"$PY" "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name kgp-normal-diff-2 -- \
  "$PY" feynman-walkthrough/assets/okf.py --root "$KG/nk" diff normal
"$PY" "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name kgp-normal-sweep-2 -- \
  "$PY" knowledge-gardener/assets/garden.py sweep "$KG/nk"
```

- `kgp-normal-status-1`: exit 0, `SOURCE: git <N> -> FRESH`, `OKF_STATUS: normal FRESH`.
- `kgp-normal-sweep-1`: exit 0, `GARDEN_RESULT: FRESH (fresh 1, ...)`.
- `kgp-normal-status-2`: exit 1, `SOURCE: git <N> -> STALE`, `OKF_STATUS: normal STALE`.
- `kgp-normal-diff-2`: exit 0, a `PINNED:` and a `CURRENT:` line with two different
  40-hex SHAs, the line `A	b.txt` and `OKF_DIFF: normal STALE`.
- `kgp-normal-sweep-2`: exit 1, `changed: b.txt` and `GARDEN_RESULT: STALE (...)`.

## Proof

The three `kgp-*-ls.txt` files with no `fsmonitor-ran`, the three `kgp-*-repo-ls.txt`
files with no stray hook-argument files, and the three empty `kgp-*-unsafe.txt` files
next to their non-empty `kgp-*-git-argv.log` files (target a). The
`kgp-badpin-line.txt` file, the three `kgp-badpin-*-ls.txt` files with no `clobber*`
entry, the three `kgp-badpin-*-git-argv.log` files with no `--output=`, and the three
`kgp-badpin-*` captures that report an error for the source (target b). The five
`kgp-normal-*` captures with FRESH, then STALE and `b.txt` (target c). Give every
capture and observation file to `record` with `--artifact`.

## Gotchas

- Before the fix (dry run on `factory/run5-base`, 2026-11-07):
  - `kgp-okf-status-ls.txt`, `kgp-okf-diff-ls.txt` and `kgp-garden-sweep-ls.txt` each
    held `fsmonitor-ran`. `kgp-*-repo-ls.txt` held stray files `1`, `2` and a long
    number such as `1794065813758024000`: the fsmonitor command `touch` got git's hook
    arguments. Each `kgp-*-git-argv.log` held plain lines such as
    `rev-parse HEAD` and `status --porcelain --untracked-files=all` with no `-c`, so
    every `kgp-*-unsafe.txt` was not empty. All three captures said FRESH, exit 0.
  - `kgp-badpin-okf-status-ls.txt` and `kgp-badpin-okf-diff-ls.txt` held
    `clobber..HEAD`. `kgp-badpin-garden-sweep-ls.txt` held `clobber..<40-hex sha>`.
    The argv logs held `diff --name-status --output=<KG>/clobber..HEAD` (okf.py) and
    `diff --stat --output=...` and `diff --name-only --output=...` (garden.py).
    `kgp-badpin-okf-status` said STALE with exit 1, `kgp-badpin-okf-diff` said STALE
    with exit 0 and `PINNED: --output=...`, and `kgp-badpin-garden-sweep` said
    `GARDEN_RESULT: STALE` with exit 1. No tool reported an error.
  - The five `kgp-normal-*` captures already behave as listed. They are a regression
    guard, not a delta.
- Resolve `PY` before you export `HOME`. With a new empty `HOME`, a version-manager shim
  for `python3` (mise, pyenv, asdf) can stop and wait for input. In the dry run,
  `okf.py init` hung with no output until it was killed.
- zsh does not split a variable into words. Use the `G` function, not a `"$G"` variable
  that holds `git --git-dir=...`.
- The pin of `R/evil` ends in `+dirty`, because its worktree `R` holds `evil/` and
  `knowledge/` as untracked files. That is expected and does not matter for this proof.
- The git shim logs only calls made by the tools under test. `drive.py` itself runs git
  without the shim, because the shim is only on the `PATH` given to `env` inside the
  capture.
- Each capture runs its command from `$WT`. Every file the recipe writes is under
  `$WT/.verify-run/$INSTANCE/`.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
