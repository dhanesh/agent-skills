# Install playbooks — per-stack verifier setup

One section per stack the detector recognizes. Each playbook gives: the files
to create (matching `detect_stack.py`'s `file_to_create` proposals), the exact
commands that exercise each rail, and a **prove-the-loop** recipe — a trivial
intentional break that turns the verifier red, and the revert that turns it
green again. Install only the rails the confirmed plan lists as missing; never
overwrite a file the repo already has without showing the diff first.

The common shape, regardless of stack:

1. Every rail gets **one command** an agent can run and read unaided.
2. A `verify` entrypoint chains them (format → build → test), exiting non-zero
   on the first failure.
3. CI runs the *same* entrypoint — local green and CI green must mean the same
   thing. Prefer pinned toolchain versions and committed lockfiles.

---

## Python

**Files.** If the repo has no test suite, create `tests/test_smoke.py`:

```python
import unittest


class TestSmoke(unittest.TestCase):
    def test_imports(self):
        # Replace with the package's real import once one exists.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
```

Add (or extend) a `Makefile` verify entrypoint:

```makefile
.PHONY: format build test verify

format:
	python3 -m compileall -q .        # swap for black/ruff if the repo adopts one

build:
	python3 -m compileall -q .

test:
	python3 -m pytest                  # or: python3 -m unittest discover -s tests

verify: format build test
```

Use `python3 -m pytest` only when the plan detected pytest; otherwise the
stdlib `unittest discover` needs no installs at all. `compileall` is the
zero-dependency floor for format/build (it catches syntax errors); if the repo
already uses black/ruff, wire those instead — but do not start a style debate
this skill is not for.

**Prove the loop.** Append `def broken(:` to any tracked `.py` file →
`make verify` goes red at the compile step. Revert the line → green.

## Node

**Files.** All three rails live in `package.json` scripts:

```json
{
  "scripts": {
    "format": "prettier --check .",
    "build": "node --check index.js",
    "test": "node --test",
    "verify": "npm run format && npm run build && npm test"
  }
}
```

Adapt to what's present: keep an existing bundler build (`tsc`, `vite build`,
…), keep an existing runner (`jest`, `vitest`); `node --test` (Node 18+) is the
zero-install default when nothing exists. Replace the npm-init placeholder test
script (`echo "Error: no test specified" && exit 1`) — the detector already
refuses to count it as a verifier. Commit a lockfile and use `npm ci` in CI
when one exists.

**Prove the loop.** Add a failing `test('red', () => { throw new Error() })`
(or temporarily assert falsehood in an existing test) → `npm test` red. Remove
it → green.

## Go

A `go.mod` repo carries its verifiers in the toolchain; usually only CI and a
convenience entrypoint are missing:

```makefile
.PHONY: format build test verify

format:
	test -z "$$(gofmt -l .)"

build:
	go build ./...

test:
	go test ./...

verify: format build test
```

If there are no `*_test.go` files, add a `smoke_test.go` with one trivial test
so `go test ./...` exercises something.

**Prove the loop.** Mis-indent any `.go` file → `make format` red. `gofmt -w`
it back → green.

## Rust

Same shape — `Cargo.toml` gives the commands, the entrypoint and CI are what's
missing:

```makefile
.PHONY: format build test verify

format:
	cargo fmt --check

build:
	cargo build

test:
	cargo test

verify: format build test
```

**Prove the loop.** Add `assert!(false)` to a test (or break formatting) →
red; revert → green.

## Make-only / bare repo

For a repo with no recognizable stack, install the Makefile skeleton the plan
proposes, with each target failing loudly until the owner fills it in:

```makefile
.PHONY: format build test verify

format:
	@echo "TODO: wire the repo's formatter here" && exit 1

build:
	@echo "TODO: wire the repo's build here" && exit 1

test:
	@echo "TODO: wire the repo's tests here" && exit 1

verify: format build test
```

A loudly-failing placeholder beats a silently-passing one: it keeps CI honest
about which rails are real. Tell the user which targets are placeholders in
the handback summary.

## CI workflow (GitHub Actions — the default provider)

Create `.github/workflows/verify.yml`, substituting the plan's actual commands
for the `run:` steps (or just `make verify` when the entrypoint exists):

```yaml
name: verify
on:
  push:
    branches: [main]
  pull_request:

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Pick the setup step(s) for the detected stack(s):
      # - uses: actions/setup-python@v5
      #   with: { python-version: "3.12" }
      # - uses: actions/setup-node@v4
      #   with: { node-version: "22", cache: npm }
      # - uses: actions/setup-go@v5
      #   with: { go-version: "1.22" }
      # - uses: dtolnay/rust-toolchain@stable
      - name: verify
        run: make verify   # or the plan's individual rail commands
```

Rules of the road:

- **Never create a second workflow when one exists** — extend the existing one
  so there is a single ground truth. The detector credits an existing workflow as
  the `ci` rail **only when that workflow actually runs a verifier command**, so
  you don't duplicate a real CI job — and doesn't credit one that verifies
  nothing (a stale-bot, dependabot or labeler workflow). When workflows exist but
  none of them verifies, the plan says so in `ci_note`: extend one of those rather
  than adding a competing file.

  Each proposal carries `exists` and `action` (`create` or `extend`). Honour them:
  `action: extend` means the file is already in the repo and must be added to, not
  overwritten.
- Pin major versions of actions; install deps from the lockfile (`npm ci`,
  `pip install -r requirements.txt`) so green is reproducible.
- If the user prefers another provider (GitLab CI, CircleCI), translate the
  same job: checkout → toolchain setup → the verify entrypoint. The contract
  is "CI runs the R1 commands", not "CI is GitHub Actions".
- Recommend (but do not silently change) branch protection so the workflow
  gates merge — that setting lives outside the repo and belongs to the owner.

## Prove-the-loop protocol (all stacks)

The install is not done when the files exist; it is done when the loop has
been *seen* to catch a defect:

1. Run each installed rail command once — record green (or explain any red
   that is the repo's pre-existing debt, and stop to ask before "fixing" code
   you were not asked to touch).
2. Introduce one trivial, reversible break (the per-stack recipes above).
3. Run the verifier — confirm it goes red and the failure message names the
   break.
4. Revert the break exactly; run again — confirm green.
5. Leave the working tree exactly as it was before step 2 (`git status`
   clean apart from the files the plan said to create).

If any rail cannot be demonstrated (e.g. the toolchain is absent in this
environment), say so explicitly in the summary and mark that rail
**installed-but-unproven** — never report a rail as proven that you did not
watch fail and recover.
