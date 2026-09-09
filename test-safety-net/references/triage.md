# Testability triage

**Never cut inward, climb outward.** Feathers' Legacy Code Dilemma: to change code safely you
need tests; to get tests you often must change code. Refactoring untested code to make it
testable inverts the safety property this skill exists to provide. This skill never rearranges
existing code to make it testable — it only ever **adds** a test file, and where a seam is
missing it reports the smallest additive one for `clean-code` to place. Sprout/wrap: add code
beside, never rearrange.

## Static triage is a filter, not the enforcement

`rank_risk.py`'s `triage()` looks at a unit's calls (resolved through the file's import-alias
map, chased transitively through same-module functions and methods) and buckets it into a tier.
Treat that tier as a **starting hypothesis**, not a verdict. Five rounds of review on this exact
classifier found fifteen-plus distinct constructions it called safe that actually reached real
I/O: an aliased import, a same-module helper, an argument default, a class body, a base-class
expression, a method call reached only through an ordinary variable, and a locally-shadowed
import, among others. Static analysis cannot decide reachability in Python from source alone —
`getattr`, dispatch tables, and dynamic imports are undecidable in general.

So the invariant this skill promises — **never write a test that performs real I/O** — is not
something the tiers guarantee. It is enforced at **runtime**, by the guard described below,
during the red→green proof in the main workflow. The tiers exist to keep that guard from firing
often (a good filter means fewer discarded attempts), not to replace it.

## The four tiers

| Tier | Situation | Action |
|---|---|---|
| **1 — direct** | deps passable, output returnable, no I/O reachable | Unit test. |
| **2 — wider boundary** | unit does I/O, but its module / handler / CLI can be driven with the boundary controlled | Pin at that boundary and **name it** in the report. Not a unit test — a narrow characterization test. As a change-detector, equally good. |
| **3 — needs a seam** | no honest boundary without adding code | Report the smallest **additive** seam (sprout/wrap) and hand to `clean-code`. Never performed by this skill. |
| **4 — not reachable** | global state, deep branch soup, or *uncontrollable* I/O at import time | Prioritized refactor list with the reason. |

A module that performs **controllable** I/O at import time (reading a config file, stamping a
timestamp) floors every unit in that file at **Tier 3**, not Tier 4: a fixture runs too late to
control a boundary crossed during `import`, so it needs a seam — making the module-level load
lazy — but it *is* reachable once that seam exists. Pure path algebra over `__file__`
(`os.path.join`/`dirname`/`abspath`) performs no I/O and does not floor anything; `os.path.exists`,
`os.listdir` and `os.stat` do.

Tiers 3 and 4 are **output, not failure**. A risk-ranked "here is what blocks testing, and the
smallest change that unblocks it" list is the missing input to `clean-code`, and is often worth
more to a human than the tests themselves.

## Tier 2 boundary controls

`rank_risk.py` only ever auto-assigns Tier 2 for four I/O groups — the ones it can control
without adding a seam:

| I/O kind | Control |
|---|---|
| filesystem | temp dir |
| clock | the language's own freeze hook |
| randomness | the language's own seed hook |
| environment variables | monkeypatch/override for the duration of the test |

**Database, HTTP, and subprocess are never auto-Tier-2.** The classifier buckets them as
uncontrollable and tiers any unit that reaches one at **Tier 3** ("needs a seam") by default —
even though a database or an HTTP call often does have a real, seam-free boundary control:

| I/O kind | Control, if you promote it |
|---|---|
| database | in-memory or throwaway instance, if the unit already accepts a connection/DSN rather than hard-wiring one |
| HTTP | hand off to **`mockstar-mock`** — already in this collection, exists for exactly this |

If inspection shows the boundary genuinely exists, promote the unit from Tier 3 to Tier 2 per
"Promoting a unit" below — do not treat the table above as license to write a Tier-2 test for a
database/HTTP unit the ranker tiered 3 without recording that promotion.

**Subprocess and process-replacing calls (`os.system`, `os.popen`, `os.posix_spawn`, `os.spawn*`,
`os.exec*`, `os.fork`) are never promotable.** There is no boundary control for them at all — see
"The runtime guard" below for why `os.exec*` in particular can never be pinned safely.

**A Tier 2 unit can hit more than one controllable group at once.** `rank_risk.py`'s
`tier_reason` string names only the alphabetically-first group a unit hits (a unit that touches
both the clock and the filesystem reports "clock" and says nothing about the filesystem). Do
not treat that string as the complete list of what to fake — it is one example, not an
inventory. Before writing a Tier 2 test, look at the unit itself (or its JSON `id`/`path`/
`lineno`) and identify every controllable group it actually reaches, then control all of them.
Faking only the group named in `tier_reason` while leaving a second, unnamed one live is exactly
the failure mode this note exists to prevent: the test would appear to pin behaviour while still
performing real I/O through the group nobody looked for.

## The runtime guard

The guard is what actually enforces "never real I/O." It runs during the red→green proof
(workflow step 4) and is **tier-aware**:

- **Tier 1 candidate** (claims to touch nothing): block *everything* during the proof run —
  filesystem, clock, randomness, environment, network, subprocess, and DB drivers. The unit
  claimed to touch nothing, so ANY touch falsifies that classification. If anything is blocked and
  raises, **reclassify to Tier 3 and discard the test** — unconditionally, and regardless of
  whether that run was red or green. Not "drop it a tier and retry": a guard trip is not a signal
  to go looking for a boundary, it is the classification being wrong, and turning it into a
  Tier 2 retry converts the one hard decline in this design into a loop.
- **Tier 2 candidate** (I/O at a boundary the test controls): block the **uncontrolled** groups
  always, plus every controllable group not named among what this test deliberately fakes. The
  controllable groups it does fake (a temp dir standing in for the filesystem, a frozen clock) are
  the point of the test, not a violation to block. A trip here means the same thing it means at
  Tier 1: reclassify to Tier 3 and discard the test.

**Patch the lowest layer, not the ergonomic wrapper.** Monkeypatching `builtins.open` or
`requests.get` alone is not enough — real code reaches I/O underneath those names. `io_guard.py`
patches, per group:

| group | what the guard replaces |
|---|---|
| filesystem | `builtins.open`, `io.open`, `io.open_code`, `io.FileIO`, **`_io.open`, `_io.open_code`, `_io.FileIO`** (the C module the frozen import machinery holds directly — `pkgutil.get_data` goes there and to no `io` name, so patching only the `io` re-exports left it reading real files), `codecs.open`, `mmap.mmap`, and the `os` primitives: the fd-level data family (`open`/`read`/`write`/`close`/`fdopen`/`pread`/`pwrite`/`readv`/`writev`/`sendfile`/`fsync`/`truncate`), the directory-and-metadata family (`listdir`/`scandir`/`walk`/`fwalk`/`stat`/`lstat`/`fstat`/`statvfs`/`access`/`readlink`/`pathconf`), and the mutation family (`remove`/`unlink`/`rename`/`renames`/`replace`/`mkdir`/`makedirs`/`rmdir`/`removedirs`/`link`/`symlink`/`mkfifo`/`mknod`/`chdir`/`chmod`/`chown`/`chflags`/`utime`/`*xattr`) |
| clock | `time.time`, `time.time_ns`, `time.sleep`, `time.localtime`, `time.gmtime`, `time.ctime`, and guarded subclasses of `datetime.datetime` / `datetime.date` (their `now`/`utcnow`/`today`/`fromtimestamp` are C classmethods and cannot be patched in place) |
| randomness | the module-level `random` methods, `uuid.uuid1/3/4/5`, the `secrets` token functions, `os.urandom` |
| environment | `os.getenv`, `os.getenvb`, `os.putenv`, `os.unsetenv`, and `os.environ` itself, rebound to a guarded mapping — its `.get`/`.pop`/`.setdefault`/`.items`/`.copy` are `MutableMapping` methods that bottom out in `__getitem__` and never call `os.getenv`, so patching the functions alone caught none of them |
| network | `socket.socket` (as a guarded SUBCLASS — see "A patched class stays a class" below), `socket.create_connection`, `socket.create_server`, `socket.socketpair`, `socket.getaddrinfo`, `socket.gethostbyname*` |
| subprocess | `subprocess.Popen` and its `run`/`call`/`check_*` wrappers, `os.system`, `os.popen`, `os.startfile`, and the whole `exec*`/`spawn*`/`fork*` family derived from `dir(os)` at runtime |
| database | `sqlite3.connect`, and `psycopg2`/`psycopg`/`pymysql`/`MySQLdb`/`pymongo`/`sqlalchemy` entry points — patched only once the target repo has imported them, never imported by the guard |

Why that list and not a shorter one, measured on CPython rather than assumed: `open(p)` reaches
`builtins.open` and **not** `os.open` or `io.FileIO`; `pathlib.Path.read_text` reaches `io.open`
and **not** `builtins.open`; `os.path.exists` is an `os.stat`; `os.walk` and `glob.glob` are
`os.scandir`; `subprocess.run` forks in C and is visible only at `subprocess.Popen`. A guard
scoped to `os.open`/`read`/`write` — the "syscall layer" as it is usually described — sees none
of the directory or metadata reads at all, so a Tier 1 unit calling `os.listdir` would pass both
the filter and the guard. That is the two-layer hole this split exists to prevent, which is why
the guard's coverage tables and `rank_risk.py`'s marker tables are cross-checked by a test rather
than kept in step by hand.

**Install the guard before the module under test is imported.** It must load ahead of collection,
not as a fixture inside the generated test file. A module that performs I/O at import time runs
those side effects during `import unit_module`, before any fixture body executes — once per proof
run, for every module, regardless of tier. A guard installed only inside a test function's
fixture never sees that.

**How the guard signals.** The proof run has **three** outcomes, not two:

- an `AssertionError` — the expectation is wrong; this is the RED half of red→green, or the
  correction went wrong and it's still wrong;
- **the guard's own exception type** — the *classification* is wrong, not the assertion; and
- neither raised — GREEN.

If the guard's exception appears at **any** point during the proof — the deliberately-wrong RED
run or the corrected GREEN run — the unit is reclassified Tier 3 and the test is discarded,
**regardless of whether that run was red or green**. Conflating a guard trip with an ordinary
assertion failure defeats the whole mechanism: a Tier 1 candidate that happens to touch the
filesystem could otherwise pass "RED" only because the guard's exception looked like the
deliberately-wrong assertion, then pass "GREEN" the same way once corrected — two runs that both
"succeeded" while never proving the classification safe.

**How the guard is loaded.** It **ships with this skill** as `assets/io_guard.py` — do not author
your own. It loads as a **pytest plugin, via `-p`**, not as a `conftest.py` written into the
target repo:

```sh
PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" TEST_SAFETY_NET_TIER=1 \
  pytest -p io_guard <path>::<test_name>

PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" \
  TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
  pytest -p io_guard <path>::<test_name>
```
**Copy the whole block, not the `pytest` line.** The `PYTHONPATH` assignment is what makes
`-p io_guard` resolvable at all; on its own, `pytest -p io_guard ...` dies with
`ImportError: Error importing plugin "io_guard"` before a single test runs. Both forms of the
command are exercised verbatim by `assets/test_io_guard.py`'s `TestTheDocumentedInvocation`,
which extracts them from *this file* and runs them, so what is printed here is what is tested.

 A plugin loads before collection — which is what makes pre-import blocking work —
and, because it is passed on the command line rather than written to disk, it touches nothing in
the user's tree: it cannot collide with a `conftest.py` the repo already has, and it cannot
outlive the proof run. That keeps Invariant 1 ("never modifies source") true with **no
carve-out** — a written `conftest.py` would have been exactly that carve-out.

**How the guard knows its tier.** The tier is passed **per invocation, by environment variable**
(`TEST_SAFETY_NET_TIER`, values `1` or `2`), read once at plugin import. This is sufficient — not
a limitation — precisely *because* the proof runs one unit at a time (workflow step 4): a single
proof run has a single tier, so the plugin never needs to dispatch per test the way a guard shared
across a whole suite run would. An absent or unparseable value falls back to tier 1, the strictest
setting: a misconfigured invocation must over-block, never under-block.

`TEST_SAFETY_NET_ALLOW` carries the second half of the Tier 2 rule — the comma-separated
controllable groups (`filesystem`, `clock`, `randomness`, `environment`) this particular test
deliberately fakes. Name **every** group the test controls, not just the one `tier_reason`
happened to print. Omitting the variable permits all four, which is the loosest reading of
Tier 2; `TEST_SAFETY_NET_ALLOW=none` fakes nothing. At Tier 1 it is ignored outright.

**How arming is scoped.** The guard is armed for the whole proof run — it has to be, or
import-time I/O runs before it exists — but it only *fires* on I/O initiated from the target
repo's own code: the test module and the unit under test. pytest's collection, assertion
rewriting, output capture and failure reporting run on stacks that never leave the interpreter's
library directories **or its console-script entry point** and are exempt, so a guarded run
reports an ordinary assertion failure ordinarily. Where the two directions trade off the guard
over-fires: a false trip costs one declined candidate, a missed one ships a test that performs
real I/O.

The entry point is in that list because leaving it out was a real defect, not a hypothetical:
`<prefix>/bin/pytest` is under none of the interpreter's library directories, its frame sits at
the base of every stack in a console-script run, and so the guard read pytest's OWN capture and
environment handling as the unit's. At Tier 1 pytest died inside its capture teardown with no
test result at all; at Tier 2 every test ERRORed on pytest setting `PYTEST_CURRENT_TEST`. Only
`python -m pytest` — which no document here tells you to run — was unaffected. The exemption is
narrow by construction: it applies to a **non-`.py`** `argv[0]`, which is what a console script
is, so `python3 some_module.py`, where `argv[0]` is the target repo's own code, can never be
exempted by it.

**A patched class stays a class.** `socket.socket`, `io.FileIO`, `_io.FileIO`, `mmap.mmap`,
`subprocess.Popen` and `pymongo.MongoClient` are classes, and the guard replaces each with a
guarded **subclass** — the same technique as `datetime.datetime`. Replacing them with plain
functions produced a *fourth* proof outcome this contract has no rule for: `ssl.py` does
`class SSLSocket(socket)` at module level, so `import ssl` — and with it `asyncio`,
`http.client`, `urllib.request`, `smtplib`, `requests`, `httpx` — raised
`TypeError: function() argument 'code' must be code, not str`, at both tiers, on a unit that was
correctly Tier 1. The subclass carries one stated caveat, the same one the clock patch carries:
while armed, an object constructed BEFORE arming is not an instance of the rebound name.

**A violation off the main thread is surfaced, not swallowed.** `Thread._bootstrap_inner`
catches `BaseException` and hands it to `threading.excepthook`, so a trip on a worker thread
could not reach the test result on its own — pytest turned it into a warning and reported the
run as PASSED, and the skill shipped a test whose captured value existed only because the guard
was armed. The guard now records every off-main-thread trip at the raise (which also sees a
`concurrent.futures` future nobody reads, where no excepthook fires at all) and re-raises it on
the main thread at `Thread.join`, at pytest teardown, or at `disarm()` — whichever comes first.
The residual is named in the list below.

**State the residuals honestly.** Six of them:

1. A test that spawns a subprocess which itself dials out to the network escapes an in-process
   guard — the guard patches this process's primitives, not a child process's.
2. A C extension that reaches the syscall directly (`numpy.fromfile`, `ctypes.CDLL(...)`) bypasses
   every Python name and is not intercepted by anything. Measured, not assumed: with the guard
   armed at Tier 1, `ctypes.CDLL(None)` open/read/close returns the real contents of `/etc/hosts`.
3. A reference bound *before* the guard armed keeps the original. Arming ahead of collection is
   what makes this rare rather than routine. The same shape covers `posix`/`nt` reached directly
   (`import posix; posix.stat(p)`), which bypasses the `os` re-exports the guard patches.
4. `os.environ` **reads** are intercepted — the subscript included, and `.get`/`.pop`/
   `.setdefault`/`.items`/`.copy`, which are the forms that never call `os.getenv`. What is not:
   `len(os.environ)` and `key in os.environ`, neither of which reads a value, and `os.environb`,
   a separate object. An earlier version of this list said the subscript was uninterceptable *and*
   that the filter shared the blind spot; both halves were false. The filter's `os.environ` marker
   DOES see `os.environ.get(...)` — so the two layers disagreed, in the direction where the filter
   tiered a unit 2 while the guard stayed silent, and an ordinary module alias (`ENV = os.environ`)
   hid it from the filter as well. That two-layer miss is what closing this residual removed.
5. A daemon thread that is never joined and outlives the proof run can trip after the last point
   at which the record could be re-raised. The pytest plugin fails the session on any record it
   still holds at `pytest_sessionfinish`, so the observable outcome is a failed run rather than a
   silent pass — but a thread still running when the interpreter exits takes its record with it.
6. If the unit under test is imported from site-packages rather than from the working tree, its
   frames are exempt and the guard under-fires. Run the proof against the working tree.

**Process-replacing calls are declined statically, and that is not redundancy.** The guard does
patch `os.exec*`, so a Python-level `os.execv` raises before the image is replaced. But an
`exec` reached any other way — a pre-bound reference, a C extension — takes every in-process
monkeypatch with it and leaves nothing behind to report the trip. The filter's static decline is
the line that holds in that case, which is why the `exec*`/`spawn*` family must be enumerated
completely in the marker table and not delegated to the guard.

## Promoting a unit

The ranker's triage is conservative by design — it would rather under-tier a unit (report it as
harder to test than it really is) than over-tier one into a false Tier 1. If inspection shows a
unit the ranker placed at Tier 3 or 4 is actually reachable at a controlled boundary, you may
promote it — but only by **recording** the promotion (which tier the ranker assigned, which tier
you're using instead, and why) in the report. Never silently treat a ranker tier as advisory and
proceed as if it read differently.
