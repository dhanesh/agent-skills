# jev-cli: run the jev CLI with no project uv config, the latest SDK, and Box and Image support

- id: jev-cli
- proven: no
- anchors: jev-agent-setup/assets/jev, jev-agent-setup/assets/install_jev_setup.py, jev-agent-setup/assets/jev-instructions.md

## What it is

**Status: planned (run 5).** `jev-agent-setup/assets/jev` is a PEP 723 script that uv
runs from its shebang. Today the shebang is `#!/usr/bin/env -S uv run --quiet --script`
and the dependency is `typesafe-sdk>=0.7`. uv then reads a `uv.toml` or
`pyproject.toml` from the working directory, so a repo the user is in can point the
dependency at its own package index, and a new SDK release can change what runs. After
run 5, the shebang is `#!/usr/bin/env -S uv run --quiet --no-config --script` and the
PEP 723 dependency is `typesafe-sdk>=0.7.5`, with no upper bound, so jev always gets the
latest SDK (0.7.5 is the first with Box questions and Image assets). uv reads no config
file, so a repo cannot choose the index. `install_jev_setup.py` copies that exact script
into its bin directory. jev also supports the two Jev features that 0.7.5 added. A
`box` question and an `{"image": "<path>"}` placeholder run only with `--images`.
`--images` sends the `TypeSafe-Danger-Zone-Images: true` header and turns each
placeholder into an SDK `Image` read from that regular file. Without `--images`, such a
request exits 2 before any call. The log keeps each image as its path and sha256, never
its bytes, and `jev log` shows a box answer's four edges and confidence.

## How to reach it

`uv run --show-settings` prints uv's resolved settings and exits. It does not resolve
dependencies and it does not run the script. `--quiet` hides that output, so the recipe
removes `--quiet` from the shebang words. The installer is
`python3 jev-agent-setup/assets/install_jev_setup.py --home <dir> [--bin-dir <dir>]`.
It writes `<home>/.local/bin/jev`, the agents' instruction files under `<home>` and a
placeholder `<home>/.config/typesafe/env`. It prints one `WROTE <path>` line for each
file and needs no network. `CODEX_HOME`, when set, moves the codex target, so the recipe
unsets it.

## Drive it

(a) Read the shebang, then ask uv for its settings from a directory whose `uv.toml`
names a hostile index. If `command -v uv` finds nothing, write that to
`uv-missing.txt` and skip (a).

```sh
JV="$WT/.verify-run/$INSTANCE/jv"
mkdir -p "$JV/cfg" "$JV/home" "$JV/xdg" "$JV/cache" "$JV/ihome"
printf 'index-url = "https://index.invalid/simple"\n' > "$JV/cfg/uv.toml"
head -n 4 jev-agent-setup/assets/jev > "$JV/head.txt"
python3 -I -c 'import shlex,sys; w=shlex.split(open(sys.argv[1]).readline()[2:]); print("\n".join(x for x in w[w.index("uv")+1:] if x not in ("--quiet","-q")))' \
  jev-agent-setup/assets/jev > "$JV/uv-args.txt"
if command -v uv >/dev/null 2>&1; then
  python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-settings -- \
    sh -c 'cd "$1" && env -u UV_CONFIG_FILE -u UV_INDEX_URL -u UV_DEFAULT_INDEX -u UV_EXTRA_INDEX_URL HOME="$2/home" XDG_CONFIG_HOME="$2/xdg" UV_OFFLINE=1 UV_CACHE_DIR="$2/cache" uv $(cat "$2/uv-args.txt") --show-settings "$3"' \
    sh "$JV/cfg" "$JV" "$WT/jev-agent-setup/assets/jev"
  python3 -I -c 'import json,sys; o=json.load(open(sys.argv[1])); print((o["stdout"]+o["stderr"]).count("index.invalid"))' \
    "$WT/.verify-run/$INSTANCE/captures/jv-settings.json" > "$JV/settings-hits.txt"
else
  echo "uv not installed: (a) skipped" > "$JV/uv-missing.txt"
fi
```

- `head.txt`: line 1 is `#!/usr/bin/env -S uv run --quiet --no-config --script` and
  line 4 is `# dependencies = ["typesafe-sdk>=0.7.5"]`.
- `uv-args.txt`: `run`, `--no-config`, `--script`, one word on each line.
- `jv-settings`: exit 0 and a long stdout that starts `GlobalSettings {`.
- `settings-hits.txt`: `0`. uv did not read `cfg/uv.toml`.

(b) Run the installer into a scratch home and compare the installed script.

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-install -- \
  env -u CODEX_HOME -u TYPESAFE_API_KEY HOME="$JV/ihome" \
  python3 -I jev-agent-setup/assets/install_jev_setup.py --home "$JV/ihome"
head -n 4 "$JV/ihome/.local/bin/jev" > "$JV/installed-head.txt"
cmp "$JV/ihome/.local/bin/jev" jev-agent-setup/assets/jev > "$JV/installed-cmp.txt" 2>&1 \
  && echo same >> "$JV/installed-cmp.txt"
```

- `jv-install`: exit 0, with a `WROTE <JV>/ihome/.local/bin/jev` line. Every `WROTE`
  path is under `$JV/ihome`.
- `installed-head.txt`: line 1 is
  `#!/usr/bin/env -S uv run --quiet --no-config --script` and line 4 is
  `# dependencies = ["typesafe-sdk>=0.7.5"]`.
- `installed-cmp.txt`: `same`.

(c) Drive `--images` and Box against a stub SDK, with no network and no API key. The
stub `typesafe_sdk` records each `system_one` call and returns fixed answers. jev runs
under `python3` directly (not through its shebang), so `PYTHONPATH` finds the stub.

```sh
mkdir -p "$JV/stub/typesafe_sdk" "$JV/shome" "$JV/log" "$JV/img"
cat > "$JV/stub/typesafe_sdk/__init__.py" <<'STUB'
import hashlib, json, os
class Image:
    def __init__(self, data): data = bytes(data); self.n, self.sha = len(data), hashlib.sha256(data).hexdigest()
class _A(dict):
    def model_dump(self): return dict(self)
def _enc(o):
    if isinstance(o, Image): return {"IMAGE": o.n, "sha256": o.sha}
    if isinstance(o, dict): return {k: _enc(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_enc(v) for v in o]
    return o
class TypeSafeClient:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def system_one(self, model=None, state=None, questions=None, extra_headers=None, **kw):
        with open(os.environ["JEV_STUB_OUT"], "w") as f:
            json.dump({"headers": dict(extra_headers or {}), "state": _enc(state),
                       "questions": _enc(questions)}, f)
        ans = {}
        for k, q in (questions or {}).items():
            t = q.get("type") if isinstance(q, dict) else getattr(q, "type", None)
            ans[k] = _A(type="box", box={"top": 0.2, "bottom": 0.6, "left": 0.1, "right": 0.5},
                        confidence=0.9) if t == "box" else _A(type="noul", noul=0.9)
        class R: pass
        r = R(); r.answers = ans; return r
STUB
printf 'not-really-a-png-0123456789' > "$JV/img/shot.png"
printf '{"state":{"shot":{"image":"%s"}},"questions":{"where":{"type":"box","instructions":"the red button"}}}' \
  "$JV/img/shot.png" > "$JV/box.json"
printf '{"state":{"shot":{"image":"%s"}},"questions":{"where":{"type":"box"}}}' "$JV/img" > "$JV/box-dir.json"
printf '{"state":{"x":"sky is blue"},"questions":{"q":{"type":"noul","instructions":"Is x true?"}}}' > "$JV/noul.json"
cat > "$JV/jrun.sh" <<'RUN'
#!/bin/sh
# usage: jrun.sh <tag> [jev args...]; runs jev against the stub SDK with a scratch home
JV=$(dirname "$0"); tag=$1; shift
exec env HOME="$JV/shome" JEV_LOG_DIR="$JV/log" TYPESAFE_API_KEY=dummy-not-a-key \
  PYTHONPATH="$JV/stub" JEV_STUB_OUT="$JV/stub-$tag.json" python3 jev-agent-setup/assets/jev "$@"
RUN
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-box-noflag -- \
  sh "$JV/jrun.sh" noflag "$JV/box.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-box -- \
  sh "$JV/jrun.sh" box --images "$JV/box.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-box-dir -- \
  sh "$JV/jrun.sh" dir --images "$JV/box-dir.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-noul -- \
  sh "$JV/jrun.sh" noul "$JV/noul.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name jv-log -- \
  env HOME="$JV/shome" JEV_LOG_DIR="$JV/log" python3 jev-agent-setup/assets/jev log -v -n 0
cat "$JV/log"/projects/*.jsonl > "$JV/log-all.jsonl"
ls "$JV" > "$JV/stub-files.txt"
```

- `jv-box-noflag`: exit 2, and stderr names `--images`. `stub-files.txt` has no
  `stub-noflag.json`: no call went out.
- `jv-box`: exit 0, and stdout holds the `where` answer with `"type": "box"`.
  `stub-box.json` has `"headers": {"TypeSafe-Danger-Zone-Images": "true"}`, and its
  `state.shot` is `{"IMAGE": 27, "sha256": ...}`: the file went in as an SDK `Image`,
  not as a path string.
- `jv-box-dir`: exit 2. A placeholder that names a directory, a missing file or a
  symlink is refused, and `stub-files.txt` has no `stub-dir.json`.
- `jv-noul`: exit 0. `stub-noul.json` has `"headers": {}`: a request with no image and
  no box sends no danger-zone header (regression).
- `jv-log`: exit 0. The box call's line shows the four edges (`top`, `bottom`, `left`,
  `right`) and the confidence, not raw JSON.
- `log-all.jsonl`: the box record holds the image as its path and a `sha256`, and no
  line holds `not-really-a-png`.

## Proof

`head.txt` and `uv-args.txt`, the `jv-settings` capture with `settings-hits.txt` at `0`
(or `uv-missing.txt` when uv is absent), the `jv-install` capture,
`installed-head.txt` and `installed-cmp.txt`. The `jv-box-noflag`, `jv-box`, `jv-box-dir`,
`jv-noul` and `jv-log` captures, the `stub-*.json` files, `stub-files.txt` and
`log-all.jsonl`.

## Gotchas

- Before the fix (dry run on `eeac771`, uv 0.7.12): `head.txt` line 1 was
  `#!/usr/bin/env -S uv run --quiet --script` and line 4 was
  `# dependencies = ["typesafe-sdk>=0.7"]`. `uv-args.txt` held `run` and `--script`.
  `settings-hits.txt` was `2`: uv read `cfg/uv.toml` and used
  `https://index.invalid/simple` as its index. The installer exited 0, wrote six files
  under the scratch home and copied the script byte for byte, with the same old line 1
  and line 4.
- Keep `--quiet` out of the uv words. With `--quiet`, `--show-settings` prints nothing,
  so the hit count is `0` before and after the fix and proves nothing.
- `--show-settings` makes no network call. Keep `UV_OFFLINE=1` all the same, and never
  run the script itself: that resolves the SDK from the network and calls the Jev API.
- Never run the installer without `--home` and a scratch `HOME`: it writes the user's
  global agent instruction files.
- Before the fix, part (c) has no `--images` flag: jev reads `--images` as the request
  file name and exits non-zero, and a box request without it reaches the stub with no
  header. The stub `typesafe_sdk` is a test double: never put it on `PYTHONPATH` outside
  this recipe.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
