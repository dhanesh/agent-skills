# test-safety-net: multi-stack architecture

**Status:** design. Amends `2026-09-09-test-safety-net-design.md`, whose stack table
promised python / node / go / rust and whose implementation shipped python only.

## Why this exists

The original spec carried a four-row stack table with the four language-specific facts
per stack. What shipped supports Python and marks the other three *not yet supported*.
The narrowing happened silently at the plan-writing step — the plan was named
`…-test-safety-net-python.md` and no decision was ever put to the owner. `stacks.md`
promises "see the follow-up plan" three times and no such plan existed.

This spec is that follow-up, and it corrects the original's central mistake: **that
table was written before the AST triage and the runtime guard existed**, so it made
adding a stack look like filling in a row. It is not. Roughly 400 of `rank_risk.py`'s
1360 lines are stack-agnostic (churn, scoring, ranking, CLI, text-based reference
counting); the remaining ~900 are Python `ast` machinery with no cross-language
equivalent, and the guard is monkey-patching, which compiled languages cannot accept.

## Decisions (owner-approved, 2026-09-09)

### D1 — Discovery is hybrid: toolchain if present, heuristic if not

No dependency-free AST exists for JS/TS (node exposes **no public parser API** —
verified) or for Rust (`syn` needs network). Go alone has `go/ast` in its stdlib.

Each stack therefore implements two discovery paths behind one interface:

- **Precise path** — shells out to what a repo of that language already has.
- **Heuristic path** — regex plus brace-matching. All three are brace languages with
  unambiguous function syntax, which is what makes this viable at all.

The report **names which path ran**, per run, in its output and in the JSON. A run that
silently degraded is a run whose numbers cannot be compared to another's.

This is architecturally consistent with the existing design, not a compromise of it:
the filter is already documented as approximate, and the guard is what enforces the
invariant. A less precise filter produces more Tier-1 candidates for the guard to
catch — the safe direction — and never the reverse.

### D2 — Compiled stacks are enforced by an OS sandbox, not by patching

`go test` and `cargo test` compile before they run. There is no live object graph to
patch, so `io_guard.py`'s mechanism does not port at all.

Go and Rust proof runs execute under a syscall sandbox — `sandbox-exec` on macOS,
`bwrap`/seccomp on Linux — with no network and a read-only filesystem outside the
build's own scratch. A violation kills the process, which is the same signal shape as
`IOGuardViolation`: a classification failure, distinct from an assertion failure.

Two honest limits, to be stated in the shipped docs rather than discovered later:

- The sandbox is **coarser** than the Python guard's per-group tiers. It cannot say
  "filesystem allowed, network not" as precisely, so Tier 2's controllable-group
  vocabulary is narrower for these stacks.
- It is **platform-specific**. Where no sandbox is available the stack reports that it
  cannot prove the invariant and declines to write, rather than writing unproven tests.

## Per-stack facts

| stack | precise discovery | heuristic discovery | guard | run ONE test |
|---|---|---|---|---|
| python | `ast` (stdlib) | n/a — always precise | `io_guard.py` via `-p` | `pytest path::name` |
| node | the repo's own `typescript`/`tsc` via `npx --no-install` | regex + brace match | `--require` preload | `node --test --test-name-pattern '^name$'`; vitest `-t`, jest `-t` |
| go | a shipped Go helper using `go/ast`, run with `go run` | regex + brace match | `sandbox-exec` / `bwrap` | `go test -run '^Name$' ./pkg` |
| rust | none available dependency-free | regex + brace match | `sandbox-exec` / `bwrap` | `cargo test name -- --exact` |

Rust has no precise path. That is a real gap, stated plainly rather than papered over:
its filter is heuristic-only, so its Tier 1 verdicts lean hardest on the sandbox.

## The node guard, proven before speccing

The naive port fails exactly as the Python guard's fix round 3 did, and for the same
reason. Patching `fs.readFileSync` breaks node's **module loader**, which reads `.js`
files through it — so a test that touches no filesystem still dies:

    at fs.readFileSync (guard.cjs)
    at defaultLoadImpl (node:internal/modules/cjs/loader)

The fix is the provenance rule the Python guard arrived at after four rounds: walk
outward to the first frame that is neither the guard nor runtime internals, and hold
the unit responsible only when that frame is user code. Node makes this cheaper than
CPython did — internal frames carry a literal `node:internal/` prefix, where CPython
required a hand-derived list of frozen-importlib frame names.

Verified in a 20-line spike before this spec was written: clean test passes under the
guard, violating test fails with the guard's own error type, and single-test invocation
works. **Carry the Python guard's hard-won rules across rather than rediscovering
them** — the error type must not be the runner's assertion type; the guard installs
before the module under test loads; a violation off the main thread must still reach
the result.

## What every stack must reuse, not reimplement

`churn`, `rank`, `_normalise`, the CLI contract and the JSON output shape are
stack-agnostic and stay in one place. A stack that copies them has already begun to
drift — the `okf.py`/`garden.py` divergence in this repo is the standing example of
what that costs.

## Sequencing

One branch per stack, each cut from `main`. They necessarily touch shared files
(`SKILL.md`, `references/stacks.md`, `eval/run_eval.py`, and whatever common module the
ranker grows), so **each branch should merge before the next is cut**, or the second and
third will conflict on every shared surface and re-litigate this spec.

---

## Amended 2026-09-10 — D2 is buildable, but not as written

D2 said compiled stacks get an OS syscall sandbox "with no network and a read-only
filesystem", and that "a violation kills the process, which is the same signal shape as
`IOGuardViolation`". Spiked against real `go test` before planning the Go stack. The
approach holds; three of its details do not.

### The sandbox cannot wrap `go test`

`go test` **compiles** before it runs, and compilation needs broad access — `GOCACHE`,
`GOROOT`, the module cache. Sandboxing the whole invocation blocks the build, not the I/O.

Two phases instead: compile unsandboxed with `go test -c -o <binary> ./pkg`, then run the
**binary** under the sandbox with `-test.run '^Name$'`. Verified: a pure test passes and
`os.WriteFile("/tmp/…")` fails with `operation not permitted`, file absent afterwards.

### `deny file-read*` kills the process before `main`

A blanket read denial produces no output at all — the binary cannot load its own dynamic
linker. The workable shape denies **`file-read-data` on named trees** (the repo, `/etc`,
`/tmp`, `$HOME`) while leaving the loader's own paths readable. Verified: pure test passes,
`os.ReadFile("/etc/hosts")` fails.

This is a real narrowing of D2's "read-only filesystem": what is enforced is *no data reads
from the trees a test could plausibly depend on*, not a globally read-only view.

### A sandbox denial is NOT distinguishable from an assertion failure

This is the substantive correction. Both print exactly `--- FAIL: TestX` then `FAIL`. The
original spec's whole point — that a guard trip means the **classification** is wrong while
an assertion failure means the **captured value** is wrong, and the two demand opposite
responses — has no signal to stand on. Parsing the sandbox's error text would work until a
message changes, and would differ per platform.

**Use a differential instead.** Run each proof twice and read the PAIR of exit codes:

| sandboxed | free | meaning |
|---|---|---|
| 0 | 0 | clean — keep the test |
| non-0 | 0 | the unit does real I/O — **classification wrong**, reclassify Tier 3, discard |
| non-0 | non-0 | **assertion failure** — the captured value is wrong |
| 0 | non-0 | anomaly — report, never keep |

Verified across all four rows with real fixtures. It reads no error string, so it cannot
rot when a message changes; it is platform-independent; and it works for Rust unchanged.

The cost is honest and belongs in the docs: **two runs per proof** instead of one, and a
unit whose I/O is non-deterministic can land in the anomaly row, which is why that row
reports rather than guesses.

### What still needs proving on Linux

`sandbox-exec` is macOS-only and deprecated. The Linux equivalent (`bwrap`, or seccomp) has
not been spiked, and the profile shape above may not translate. The Go stack must verify it
in a container before claiming the platform, and decline to write — per D2's existing rule
— wherever no sandbox is available.

### The sandbox profiles, spiked on both platforms

All four differential rows verified against real `go test` binaries on macOS
(`sandbox-exec`) and Linux (`bwrap`, in a container). The profiles are not symmetric and
the differences are not cosmetic.

**macOS** — `sandbox-exec -f <profile>`:

    (version 1) (allow default)
    (deny file-write*) (deny network*)
    (deny file-read-data (subpath "<repo>") (subpath "/etc") (subpath "/tmp") (subpath "<home>"))
    (allow file-write* (subpath "/dev"))

**Linux** — `bwrap --ro-bind / / --proc /proc --ro-bind <empty-dir> <read-target> --die-with-parent`

Four findings that a plan would not have predicted:

1. **`--tmpfs` is the wrong tool for denying writes, and fails in the dangerous direction.**
   It gives the write a throwaway filesystem to *succeed* into. A test writing
   `/var/tmp/x` under `--tmpfs /var/tmp` returns exit 0 and the differential reads it as
   clean. Real I/O passing the guard is the one outcome this design exists to prevent.
   Deny writes with `--ro-bind / /` and add no tmpfs over the trees you are protecting.

2. **A blanket read denial kills the process before `main`.** macOS `deny file-read*`
   produces no output at all — the dynamic linker cannot load. Deny `file-read-data` on
   named trees instead.

3. **On Linux, `--ro-bind / /` blocks writes but permits reads.** Blocking a read needs an
   empty directory bound over the target (`--ro-bind <empty> /etc`). Without that step
   Linux and macOS disagree on a read-only unit — Linux calls it clean, macOS calls it
   Tier 3 — so the bind list is what keeps the two platforms honest with each other, not a
   refinement.

4. **`bwrap` needs unprivileged user namespaces, which containers block by default.**
   Inside Docker it fails with `Creating new namespace failed: Operation not permitted`
   until the container is given `SYS_ADMIN` and unconfined seccomp/apparmor. Many CI
   setups are containers, so the Go stack must *detect* a working sandbox rather than
   assume one, and decline to write when it cannot get one — per D2's existing rule.
   `--unshare-net` additionally failed loopback setup in that environment
   (`RTM_NEWADDR: No child processes`), so network denial on Linux is **not yet proven**
   and must be verified on a real runner before it is claimed.

---

## Amended 2026-09-12 — for Go, D2 is replaced by an overlay guard (owner decision)

The sandbox spike above shows D2 is buildable, but at four costs, and none of them goes
away:

- **No trip signal of its own.** A denial prints exactly like an assertion failure, so
  every proof needs two runs read as a pair.
- **Linux is unproven where it matters.** `bwrap` needs user namespaces that containers
  deny, and network denial there was never demonstrated.
- **Per-platform profiles.** macOS and Linux each need their own profile, and those
  profiles disagree unless tuned against each other.
- **Four groups a sandbox cannot see.** Clock, environment, randomness and terminal input
  are not syscalls it can deny. A Go tier 1 proof would therefore be weaker than python's
  or node's in exactly the groups that make a characterization test flaky.

A second mechanism was spiked on go 1.26.7/darwin and adopted instead: **`go test -overlay`
over the standard library**. The overlay file map replaces source files in the build
without touching them on disk — GOROOT's included — and adds new files to existing
packages. Every proof run:

1. generates hook files from the installed GOROOT;
2. injects a one-line hook at the top of each I/O primitive: `syscall`,
   `internal/syscall/unix`, `time`, `math/rand`, `crypto/internal/sysrand`,
   `database/sql`, and the `net`/`net/http` entry points;
3. adds a decision engine to `syscall`.

The engine walks `runtime.Callers` with the rule `io_guard.js` settled on:
- stdlib frames are transparent;
- the generated `_testmain.go` runner exempts the call;
- the first other frame is attributable;
- reaching the end of the stack exempts it.

Spike results, each against a real `go test`:

| probe | result |
|---|---|
| pure unit, tier 1 (testing's own `time.Now` and printing) | PASS, exit 0 |
| `time.Now` in the unit | `IOGuardViolation: clock … from calc.Now`, exit 1 → wrapper 3 |
| `os.Getenv` in the unit | trips `environment` |
| the same, wrapped in `recover()` | still trips: `syscall.Exit(3)` is not a panic |
| the same, on a goroutine the unit started | trips, attributed to the unit's function |
| `os.Getenv` in the package's `init` | trips at import, attributed to `calc.init` |
| `-trimpath` | unchanged: provenance reads the frame's package path, not its file |
| cold build (fresh `GOCACHE`), warm build, moved overlay dir | 5s, 0s, 0s — the cache keys on content |

What this buys over D2:
- **The three-outcome contract holds with no differential.** A trip has its own signal.
- **Per-group tiers.** The groups are python's and node's, so tier 2 can permit
  `filesystem` while blocking `network`.
- **Every platform Go runs on,** with no namespaces or profiles.

What it costs, stated in the plan and the shipped docs:
- **Anything that bypasses `syscall` is unseen** — raw syscalls, `golang.org/x/sys/unix`,
  cgo, assembly. The filter declines the raw and cgo shapes statically.
- **Some stdlib work is exempt.** When the stdlib does I/O on a goroutine it started
  itself, with no repo frame on it, the call passes; `net/http`'s entry points are hooked
  for that reason.
- **Hook targets are located by text in each Go release's sources.** A missing required
  target makes the guard refuse to run, never run unguarded.

The OS sandbox remains the answer to evaluate for **Rust**, whose `std` ships prebuilt
and cannot be overlaid. The Go plan is
`docs/superpowers/plans/2026-09-12-test-safety-net-go.md`.
