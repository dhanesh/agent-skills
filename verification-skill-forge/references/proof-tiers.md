# Proof tiers

A clause of a feature contract can be proven at one of five tiers. This file gives the
tiers, the tools each tier accepts, the failure signature of each tool, and how to record
a tier proof with `verify_evidence.py record`. The record format is in `evidence.md`.

## Tiers

| Tier | Meaning | Rust | Python | TypeScript | Go |
|---|---|---|---|---|---|
| `proved` | holds for every input | Verus | — | — | — |
| `bounded` | holds for every input up to a stated bound | Kani | — | — | — |
| `property` | holds for N generated inputs, shrunk on failure | proptest | Hypothesis | fast-check | rapid |
| `runtime` | the running app did it once, as a user would | verify-<app> | verify-<app> | verify-<app> | verify-<app> |

Order: `proved` > `bounded` > `property` > `runtime` > `none`. `none` means that the
clause has no record. A `—` cell means that no backend is trusted for an unattended gate
in v1. CrossHair is not a `bounded` backend,
because its bound is a time budget and not an input bound. Python has no `bounded`
backend in v1.

The recorder accepts only these `--tool` values for each tier (`TIER_TOOLS` in
`verify_evidence.py`):

| Tier | Allowed `--tool` |
|---|---|
| `proved` | `verus` |
| `bounded` | `kani` |
| `property` | `proptest`, `hypothesis`, `fast-check`, `rapid` |

## Runners

`--tool` is not only a label. The recorder runs `--cmd` only when its argv runs that
tool (`runner_matches` in `verify_evidence.py`). The argv must start with one of the
tool's runners in the table below, word for word. These are the `RUNNERS` and `WRAPPERS`
tables in `verify_evidence.py`, and this section must stay equal to them.

| Tool | Runners |
|---|---|
| `verus` | `verus`, `cargo verus` |
| `kani` | `kani`, `cargo kani` |
| `hypothesis` | `pytest`, `python -m pytest`, `python3 -m pytest` |
| `proptest` | `cargo test` |
| `fast-check` | `vitest`, `jest` |
| `rapid` | `go test` |

Wrappers: `uv run`, `poetry run`, `pdm run`, `pipenv run`, `hatch run`, `pnpm exec`,
`yarn`, `bunx`, `npx`.

The rules:

- The recorder strips at most one wrapper from the start of the argv. Then the runner
  must come next. A flag between the wrapper and the runner is refused
  (`uv run --with x pytest`), and so is a second wrapper.
- The runner words must be the first words, in order. No option may come between them
  (`cargo +nightly test` is refused). Options and paths may follow the runner.
- argv[0] must be a bare name that is found on PATH. An argv[0] that holds a `/` is
  refused (`./bin/pytest`, `/usr/bin/python3`).
- A first word such as `python`, `python3` or `python3.12` counts as `python` or
  `python3`.

Any other argv exits 2 before the other checks, runs nothing and writes nothing. The
error is `--cmd runs <argv0>, which is not a runner of --tool <tool> (<runners>)`. To test
the recorder without the real tool, put a script with the runner's name on PATH.

## Recording a tier proof

```bash
# proved: the tool must pass on HEAD.
python3 verify_evidence.py record --instance a --feature notes-create --verifier "$VERIFIER" \
  --tier proved --tool verus --clauses C1 --cmd "verus src/notes.rs"

# bounded and property: the tool must pass on HEAD and fail on a mutant.
python3 verify_evidence.py record --instance a --feature notes-create --verifier "$VERIFIER" \
  --tier property --tool hypothesis --clauses C2 --cmd "python3 -m pytest tests/test_notes.py" \
  --mutant-patch /tmp/c2-mutant.diff --mutant-author verifier [--timeout 900]
```

A `bounded` or `property` pass alone does not show much, because a check that tests
nothing also passes. So these tiers need a mutant: a diff that breaks the code the clause
is about. The recorder does these steps:

1. It checks every flag. `--mutant-patch` and `--mutant-author` (`verifier` or `forge`,
   the one who wrote the mutant) are required for `bounded` and `property`, and refused
   for `proved`.
2. It checks that the worktree is clean. When `git status --porcelain
   --untracked-files=no` is not empty, it exits 2 and names the changed paths. A staged
   or an unstaged change to a tracked file counts. An untracked file does not count.
3. It checks out every tracked file of the committed HEAD into a temporary directory
   outside the worktree, and applies the patch there with `git apply`. A patch that does
   not apply exits 2 before any command runs. Make the diff against HEAD, with paths from
   the repository root (`git diff > mutant.diff`, then `git checkout -- .`).
4. It checks out HEAD again into a second temporary directory, and runs `--cmd` from its
   root. The command must exit 0.
5. It runs the same argv, with the same timeout, from the root of the mutated copy. The
   run must exit non-zero, and its stdout plus stderr must match the tool's failure
   signature below.
6. It removes both checkouts on every path, and only then writes the record.

`proved` does steps 1, 2, 4 and 6. The worktree and its index are never written. Each
checkout holds every tracked file of HEAD, and a file that `export-ignore` marks is in
it too. An untracked file is not in it, so a proof that needs one fails. Use paths
relative to the repository root in `--cmd`: an absolute path into the worktree makes the
run check the worktree and not the checkout. The record stores `"cwd": "."` and
`"ran_in": "checkout"`, because the runs happened at the root of a checkout that is gone.

A mutant run that exits 0, or whose output does not match the signature, exits 1 and
writes nothing. Write a mutant that makes the check itself fail, not one that breaks the
build. The record adds `mutant_author` and `mutant_exit_status`, and pins four more
artifacts: `<stem>.mutant.patch`, `<stem>.mutant.exit`, `<stem>.mutant.stdout` and
`<stem>.mutant.stderr`, where `<stem>` is `proof-<tier>-<tool>-<clause>`.

Exit codes of `record --tier`:

| Exit | Meaning |
|---|---|
| 0 | Recorded. It prints `PROOF: <tier> <path>`. |
| 1 | Nothing recorded. The command failed or timed out on HEAD, the mutant run exited 0 or did not match the signature, or HEAD moved during the run. |
| 2 | Nothing ran or was recorded. The input is not valid: for example a `--tool` outside its tier, a `--cmd` that is not a runner of its `--tool`, a `--clauses` value that is not one clause, a missing mutant flag, a worktree with tracked changes, or a patch that does not apply. |

### Known limits

- A command that is rigged on purpose to act differently on its second run, or to read
  its working directory, can fake a caught mutant. This is the same deliberate forgery
  that the recorder cannot stop for any command.
- The checkouts hold only what git tracks. A proof that needs a build cache, a
  submodule or a file that is not committed runs slower or fails. Commit what the proof
  needs, or record the clause at the tier its evidence earns.

## Failure signatures

The mutant run counts only when every "Requires" pattern matches its output and no
"Rejects" pattern does. The patterns are Python regular expressions. They are the
`FAILURE_SIGNATURES` table in `verify_evidence.py`, and this table must stay equal to it.

| Tool | Requires | Rejects |
|---|---|---|
| `kani` | `VERIFICATION:- FAILED` | `error\[E\d+\]`, `could not compile` |
| `proptest` | `test result: FAILED`, `minimal failing input` | `error\[E\d+\]`, `could not compile` |
| `hypothesis` | `Falsifying example`, `\b\d+ failed\b` | `errors? during collection`, `ERROR collecting` |
| `fast-check` | `Property failed after \d+ tests?` | `Test suite failed to run`, `error TS\d+` |
| `rapid` | `--- FAIL`, `\[rapid\] failed` | `\[build failed\]`, `\[setup failed\]` |

These are heuristics over each tool's usual output, not a parse of it. Each one asks for
positive evidence that the check failed, and each one rejects a known sign that the
mutant broke something else:

- `kani` requires the verification summary of a failed harness. It rejects a Rust
  compile error, because then no harness ran.
- `proptest` requires a failed `cargo test` run and the shrunk input that proptest
  prints. It rejects a Rust compile error.
- `hypothesis` requires the `Falsifying example` that Hypothesis prints and pytest's
  failed count. It rejects a pytest collection error, such as a syntax or import error
  in the mutant.
- `fast-check` requires fast-check's property report. It rejects a Jest test suite that
  did not load and a TypeScript compile error.
- `rapid` requires a failed `go test` and rapid's own failure report. It rejects a Go
  build failure and a setup failure.

A tool that prints its output in another format, or a test command that hides it, does
not match. Run the tool in a mode that prints the default report.

## Escape hatches

A Rust proof can pass because the code tells the tool to skip the hard part. Verus
`assume(...)` and `admit()` add a fact without a proof. `#[verifier::external_body]`
trusts a function body without a check. `forge.py check-proof` finds these tokens on the
lines that a diff adds:

```bash
python3 forge.py check-proof --root <repo> --base <ref> --head <ref>
```

The rule (`escape_hatches` in `forge.py`) looks at each line that the diff from `--base`
to `--head` adds to a `.rs` file. A line has an escape hatch when it holds one of these:

- `assume(` or `admit(` with no identifier character and no `::` just before it. So
  `assume(x)` and `x.assume(` count. `prop_assume(`, `my_assume(` and `kani::assume(`
  do not count.
- `external_body` inside `#[verifier ...]` on the same line, by the regex
  `#\[verifier\b[^\]]*\bexternal_body\b`. A bare `external_body` does not count.

Comments and strings are not skipped, so a token in a comment is reported too. A line
that the diff does not add, a removed line and a file that does not end in `.rs` are not
scanned. Renames are off, so the lines of a moved file count as added.

The output has one `ESCAPE: <path>:<line> <token>` line for each hit, sorted by path,
line and column. `<path>` is relative to the repository root, `<line>` is the line
number in the `--head` file, and `<token>` is `assume(`, `admit(` or `external_body`.
The last line is `CHECK_PROOF_RESULT: FAIL` or `CHECK_PROOF_RESULT: PASS`. The command
reads only commits, never the worktree.

| Exit | Meaning |
|---|---|
| 0 | `CHECK_PROOF_RESULT: PASS`. The diff adds no escape hatch. |
| 1 | `CHECK_PROOF_RESULT: FAIL`, after one `ESCAPE:` line for each hit. |
| 2 | Nothing was scanned. `--root` (default `.`) is not in a git work tree, or `--base` or `--head` does not resolve to a commit. |

factory-conductor holds a copy of this rule for its evidence gate. Both copies must pass
the same vector list, `assets/escape_hatch_vectors.json`. Change the rule, the vectors
and both copies together.
