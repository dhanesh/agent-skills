"use strict";
/**
 * io_guard.js -- the tier-aware runtime I/O guard for `test-safety-net`, node.
 *
 * This is the SOLE enforcement of the skill's headline invariant, "never
 * writes a test that performs real I/O", on the node stack. The static triage
 * in `stack_node.py` is a FILTER: it ranks candidates and declines the obvious
 * hazards, but JavaScript's dynamic dispatch (computed member access, dynamic
 * `import()`, a registry of handlers assembled at run time) makes reachability
 * undecidable from source, so the invariant is a RUNTIME property or it is
 * nothing.
 *
 * DOCUMENTED COMMAND -- copy the whole block. `--require` is what makes the
 * guard arm BEFORE the test file, and therefore before the unit under test, is
 * loaded; a guard that arms later cannot see import-time I/O, which is exactly
 * the case the filter floors to Tier 3. There is nothing to write into the
 * target repo, so Invariant 1 ("never modifies source") holds with no
 * carve-out:
 *
 *     TEST_SAFETY_NET_TIER=1 \
 *       node --require "$SKILL_DIR/assets/io_guard.js" \
 *       --test --test-name-pattern '^<test_name>$' <path>
 *
 *     TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
 *       node --require "$SKILL_DIR/assets/io_guard.js" \
 *       --test --test-name-pattern '^<test_name>$' <path>
 *
 * `assets/test_io_guard_node.sh` extracts those two lines from this header and
 * from every markdown file in the skill and runs whatever it finds, verbatim,
 * against a clean unit and a leaking one. That is not ceremony: the Python
 * guard's suite ran `python -m pytest` while every document printed `pytest`,
 * and 122 unit tests plus 34 eval checks were green over a guard whose only
 * user-facing invocation was broken three separate ways.
 *
 * Plain CommonJS, no dependencies, no build step, node 18+. It is loaded by
 * absolute path, so it never has to be resolvable as a package.
 *
 * WHY PROVENANCE, AND NOT JUST THE NAME
 * -------------------------------------
 * Node reads every `.js`, `.mjs` and `.json` file it loads through
 * `fs.readFileSync`. A guard that blocks on the NAME alone therefore kills a
 * test that touches no filesystem at all: the run dies inside
 * `defaultLoadImpl (node:internal/modules/cjs/loader)` before the test body
 * ever runs. The block decision is scoped by CALL PROVENANCE instead --
 * `initiatedByCodeUnderTest` walks the stack from the innermost frame outward,
 * skips this file, and answers "was the first attributable frame the target
 * repo's code, or the runtime's own?".
 *
 * The scan stops at the FIRST such frame, and that is the whole of the rule.
 * Scanning further -- asking "is the module loader anywhere below me?" --
 * answers yes for every module body in the process, because a module body
 * always runs with the loader's frames beneath it. The Python guard shipped
 * exactly that bug for a review round: `pkgutil.get_data` in a module body
 * read an arbitrary file at tier 1 and the run reported `1 passed`. Here the
 * equivalent is `test_io_guard_node.sh` assertion 5, a module body that reads
 * `/etc/hosts` while `node:internal/modules/cjs/loader` is on the stack.
 *
 * WHICH FRAMES ARE ATTRIBUTABLE, AND THE ONE THAT COST A ROUND
 * -----------------------------------------------------------
 * "First attributable frame" is doing all the work in that sentence, and the
 * first version of this file got it wrong in the OPPOSITE direction to the
 * Python guard. It treated every `node:internal/...` frame as the runtime's
 * own and stopped there. `util.promisify(fs.readFile)` returns a wrapper
 * DEFINED IN `node:internal/util`, so the innermost non-guard frame of every
 * promisified call is internal, and the walk concluded "not the code under
 * test" one frame before it would have found the unit. At tier 1, through the
 * command printed above, a unit could really read `/etc/hosts`, really write
 * `/tmp/x.txt` and really resolve DNS, and the run printed `# pass` and
 * exited 0.
 *
 * Python's bug was scanning TOO FAR and its fix was to stop early; this one
 * was stopping TOO EARLY, and copying Python's remedy would have made it
 * worse. The rule now inverts the default: an internal frame is TRANSPARENT
 * (keep walking outward) unless it names one of the four entries in
 * `RUNTIME_OWN_WORK`, which are the module loader, the builtin loader, the
 * test runner and the console. That is the same distinction `node:path`
 * already had, generalised -- see `RUNTIME_OWN_WORK` for what each member
 * buys and for the measurement that put it there.
 *
 * WHAT IT PATCHES, AND WHY THAT LAYER
 * -----------------------------------
 * The lowest layer reachable from JavaScript, per group, and BOTH faces of the
 * ones that have two:
 *
 *     fs.readFileSync(p)          -> node:fs
 *     fs.promises.readFile(p)     -> node:fs/promises (the SAME object as
 *                                    `fs.promises`, and a distinct set of
 *                                    functions from the callback face)
 *     new net.Socket()            -> node:net
 *     http.request / fetch        -> node:http, node:https, globalThis.fetch
 *     execSync / spawn / Worker   -> node:child_process, node:worker_threads,
 *                                    node:cluster
 *     dns.lookup / dns.promises   -> node:dns and node:dns/promises
 *
 * Third-party clients are not patched and do not need to be: `axios`, `got`,
 * `node-fetch`, `undici` and `superagent` all bottom out in `node:net` or
 * `globalThis.fetch`, and `fs-extra`/`graceful-fs` bottom out in `node:fs`.
 * Database DRIVERS are the exception -- they reach the network through their
 * own native bindings -- so they are patched the moment the target repo
 * requires one (`_hookModuleLoad`), and never required by this file: a stdlib
 * -only guard must not make `pg` a dependency.
 *
 * Every function is replaced by a `Proxy` with `apply` and `construct` traps
 * rather than by a wrapper function. `net.Socket`, `worker_threads.Worker` and
 * `fs.ReadStream` are CLASSES, and rebinding a class to a plain function
 * breaks `class X extends net.Socket`, `instanceof`, and every static property
 * -- which in the Python guard produced a FOURTH proof outcome that the
 * three-outcome contract has no rule for. A Proxy keeps the target's identity,
 * prototype, statics and `instanceof` intact while still intercepting the call
 * or the construction, which is the part that reaches I/O.
 *
 * HOW ARMING IS SCOPED, AND WHAT THAT COSTS
 * -----------------------------------------
 * The arming WINDOW is the whole process, and it has to be: a module that does
 * I/O at import time runs its side effects while the test file is being
 * loaded, before any test body executes. What is scoped is the BLOCK DECISION,
 * by provenance. Everything the test runner does on its own behalf -- reading
 * the test file, spawning one child process per test file, writing TAP to
 * stdout, arming its own timeouts -- runs on stacks that either reach an
 * entry in `RUNTIME_OWN_WORK` or contain no target-repo frame at all, and is
 * exempt.
 *
 * The cost is stated rather than implied, and it is now much narrower than it
 * was: a call the target repo makes is exempt only when it reaches a guarded
 * primitive THROUGH the module loader, the builtin loader, the test runner or
 * the console. `require("/etc/hosts")` is read by the loader before it fails
 * to parse, and `promise.then(fs.readFileSync)` -- the guarded function passed
 * as the continuation itself, with no user frame between it and the microtask
 * queue -- is exempt because the stack has nothing on it to attribute. Both
 * are the same shape as the Python guard's import exemption, and both are far
 * rarer than the case the exemption buys, which is "every test that imports
 * anything at all". What is NOT exempt any more, and used to be:
 * `util.promisify`, `util.callbackify`, and every other wrapper node defines
 * in an internal module the target repo can enter directly.
 *
 * WHY A VIOLATION IS ALSO RECORDED, NOT ONLY THROWN
 * ------------------------------------------------
 * JavaScript has no uncatchable exception. Python's `IOGuardViolation` derives
 * from `BaseException` precisely so that `except Exception:` inside the unit
 * cannot swallow it; `catch {}` catches everything, and
 * `try { fs.readFileSync(cache) } catch {}` is one of the commonest shapes in
 * real code. So every violation is RECORDED at the raise, and a `process.exit`
 * hook fails the run on any record that was not already fatal. Without that,
 * a swallowed trip is a green proof over a unit that really did read the
 * filesystem. The same record covers a violation raised on a later turn of the
 * event loop, which `node:test` reports as a failure of the FILE rather than
 * of the test -- the individual test still prints `ok`, so the exit status is
 * the only trustworthy signal and this hook is what guarantees it.
 *
 * WHY STDIN IS BLOCKED AT TIER 1, THOUGH IT IS IN NO MARKER TABLE
 * --------------------------------------------------------------
 * Terminal input (`readline`, `node:tty`, `process.stdin`) appears in neither
 * stack's I/O marker table, so the FILTER cannot decline a unit that reads it.
 * Under a proof run that unit does not fail -- it HANGS, waiting for a line
 * that is never typed, yielding no verdict at all and burning the user's wall
 * clock until they notice. A hang is strictly worse than a failure, so tier 1
 * makes it fail fast. It is deliberately NOT a group: the groups are
 * `stack_node.py`'s groups, verbatim, and inventing an eighth would break the
 * one-answer correspondence between the filter and this guard.
 *
 * RESIDUALS, STATED RATHER THAN IMPLIED
 * -------------------------------------
 * 1. A test that spawns a subprocess which itself dials out escapes an
 *    in-process guard. Naming it is the point. (`subprocess` is blocked at
 *    every tier, so this needs the spawn to be exempt to begin with.)
 * 2. A native addon (`.node`) or a WASI instance that reaches the syscall
 *    through its own bindings bypasses every JavaScript name.
 *    `process.loadEnvFile`, `process.binding` and `node:sqlite`'s C++ layer
 *    are the in-tree examples; `node:sqlite`'s `DatabaseSync` constructor IS
 *    patched, its bindings are not.
 * 3. A reference bound before the guard armed keeps the original. `--require`
 *    is what makes this rare rather than routine -- it runs before any user
 *    module is loaded, including the test file.
 * 4. A guarded primitive reached with no attributable frame between it and the
 *    runtime is exempt: see "HOW ARMING IS SCOPED" above. The two shapes are
 *    `require("<a data file>")`, which the loader reads before it fails to
 *    parse, and a guarded function used AS a continuation
 *    (`promise.then(fs.readFileSync)`), whose stack holds the microtask queue
 *    and nothing else. `util.promisify(fs.readFile)` and
 *    `util.callbackify(...)` are NOT in this class -- they were until the
 *    provenance rule inverted its default, and assertion 15 pins each of them.
 * 5. `process.argv` and `process.env` are intercepted on READ, but
 *    `"KEY" in process.env`, `Object.keys(process.env).length` and
 *    `process.env` destructured before arming are not: the first two read no
 *    value, and the third is residual 3.
 * 6. A violation on a worker thread cannot reach the parent's record. The
 *    `subprocess` group is blocked at every tier and `worker_threads.Worker`
 *    is one of its guarded constructors, so a worker cannot be started under
 *    the guard in the first place -- which is why this is a residual and not a
 *    hole.
 * 7. The provenance walk reads at most 64 frames. A guarded call reached with
 *    MORE than 64 runtime frames between it and the unit falls off the end of
 *    the walk and is exempt. The deepest stack this skill's suite produces is
 *    21, so the margin is large, but it is a limit and not a proof.
 * 8. NOT a residual any more, recorded because a reader will wonder: a target
 *    repo that replaces `Error.prepareStackTrace` (`source-map-support`, a
 *    Sentry SDK) used to make every frame unreadable, so the walk fell through
 *    to "the target repo" and a CLEAN unit tripped -- fail-safe, but a false
 *    positive on a mainstream dependency. The walk now forces V8's default
 *    formatter for its own `new Error()` and restores the repo's hook
 *    immediately. Assertion 16 pins both halves: clean passes, leaky trips,
 *    with the hook installed.
 */

const GROUPS_CONTROLLABLE = ["clock", "environment", "filesystem", "randomness"];
const GROUPS_UNCONTROLLABLE = ["database", "network", "subprocess"];
const GROUPS = GROUPS_CONTROLLABLE.concat(GROUPS_UNCONTROLLABLE).sort();

const TIER_ENV = "TEST_SAFETY_NET_TIER";
const ALLOW_ENV = "TEST_SAFETY_NET_ALLOW";

/**
 * A guarded I/O primitive was reached from the code under test.
 *
 * Deliberately NOT `assert.AssertionError`, and the distinction is not
 * cosmetic: the proof run has THREE outcomes, and two of them demand opposite
 * responses. An assertion failure is the RED half of red->green -- the
 * CAPTURED VALUE is wrong, so correct it and re-run. An `IOGuardViolation`
 * means the CLASSIFICATION is wrong: the unit reaches real I/O the tier said it
 * did not, so reclassify it to Tier 3 and discard the test, whether the run was
 * red or green. Conflating the two is exactly how a Tier 1 candidate that
 * touches the filesystem "passes" RED and then "passes" GREEN without either
 * run ever proving the classification safe.
 */
class IOGuardViolation extends Error {
  constructor(group, target, tier) {
    super(
      "test-safety-net io_guard: a tier " + tier + " candidate reached " +
      group + " I/O via " + target + ". This is a CLASSIFICATION failure, not " +
      "an assertion failure: reclassify the unit to Tier 3 and discard the " +
      "test, regardless of red or green (references/triage.md, 'How the guard " +
      "signals')."
    );
    this.name = "IOGuardViolation";
    this.group = group;
    this.target = target;
    this.tier = tier;
  }
}

// ── The filter <-> guard correspondence ──────────────────────────────────
//
// `stack_node.py`'s marker tables are what the FILTER declines on; these three
// maps are what this guard intercepts. `test_stack_node.py` asserts they
// PARTITION those tables exactly -- every marker appears in exactly one of the
// three -- so a marker added to the filter with no guard-layer intercept fails
// the gate rather than opening a silent two-layer hole. That hole is not
// hypothetical: the Python pair had one for two review rounds, where
// `pkgutil.get_data` was missed by the filter (the read lives in a module the
// analysed unit merely imports) AND exempted by the guard.
const FILTER_MARKER_INTERCEPTS = {
  // filesystem
  "fs": "every own function of node:fs",
  "fs/promises": "every own function of node:fs/promises (the SAME object as fs.promises, and a different set of functions from the callback face)",
  "fsPromises": "node:fs/promises -- the name a repo binds it to changes nothing",
  "fs-extra": "node:fs (fs-extra wraps it; it holds no bindings of its own)",
  "graceful-fs": "node:fs (it monkey-patches a COPY of fs at require time, which under --require is the already-guarded one)",
  "trace_events": "every own function of node:trace_events",
  "v8.writeHeapSnapshot": "v8.writeHeapSnapshot",
  // clock
  "Date.now": "Date.now",
  "new Date": "the Date constructor, and ONLY with no arguments -- `new Date(0)` reads no clock, and the marker table does not mark it either",
  "perf_hooks": "every own function of node:perf_hooks",
  "performance.now": "performance.now",
  "process.hrtime": "process.hrtime",
  "process.uptime": "process.uptime",
  "setImmediate": "globalThis.setImmediate and node:timers",
  "setInterval": "globalThis.setInterval and node:timers",
  "setTimeout": "globalThis.setTimeout and node:timers",
  "timers": "every own function of node:timers",
  "timers/promises": "every own function of node:timers/promises",
  // randomness
  "Math.random": "Math.random",
  "crypto.generateKey": "crypto.generateKey",
  "crypto.generateKeySync": "crypto.generateKeySync",
  "crypto.generateKeyPair": "crypto.generateKeyPair",
  "crypto.generateKeyPairSync": "crypto.generateKeyPairSync",
  "crypto.generatePrime": "crypto.generatePrime",
  "crypto.generatePrimeSync": "crypto.generatePrimeSync",
  "crypto.getRandomValues": "crypto.getRandomValues, on node:crypto and on globalThis.crypto",
  "crypto.randomBytes": "crypto.randomBytes",
  "crypto.randomFill": "crypto.randomFill",
  "crypto.randomFillSync": "crypto.randomFillSync",
  "crypto.randomInt": "crypto.randomInt",
  "crypto.randomUUID": "crypto.randomUUID, on node:crypto and on globalThis.crypto",
  "crypto.randomUUIDSync": "the random* family on node:crypto (no such export exists on any current node; patched if one appears)",
  // environment
  "os": "every own function of node:os",
  "process.argv": "the process.argv getter",
  "process.argv0": "the process.argv0 getter",
  "process.chdir": "process.chdir",
  "process.cwd": "process.cwd",
  "process.env": "a Proxy over process.env: every VALUE read, including destructuring",
  "process.umask": "process.umask",
  // network
  "EventSource": "globalThis.EventSource",
  "WebSocket": "globalThis.WebSocket",
  "XMLHttpRequest": "globalThis.XMLHttpRequest, where a repo has polyfilled one",
  "fetch": "globalThis.fetch",
  "dgram": "every own function of node:dgram",
  "dns": "every own function of node:dns",
  "dns/promises": "every own function of node:dns/promises",
  "http": "every own function of node:http",
  "http2": "every own function of node:http2",
  "https": "every own function of node:https",
  "inspector": "every own function of node:inspector",
  "inspector/promises": "every own function of node:inspector/promises",
  "net": "every own function of node:net, including the Socket and Server constructors",
  "tls": "every own function of node:tls",
  "axios": "node:http/node:https/globalThis.fetch (it has no transport of its own)",
  "got": "node:http/node:https",
  "node-fetch": "node:http/node:https",
  "request": "node:http/node:https",
  "superagent": "node:http/node:https",
  "undici": "node:net (its HTTP is written over a raw socket)",
  // subprocess
  "child_process": "every own function of node:child_process",
  "exec": "child_process.exec",
  "execFile": "child_process.execFile",
  "execFileSync": "child_process.execFileSync",
  "execSync": "child_process.execSync",
  "fork": "child_process.fork and cluster.fork",
  "spawn": "child_process.spawn",
  "spawnSync": "child_process.spawnSync",
  "worker_threads": "every own function of node:worker_threads, including the Worker constructor",
  "cluster": "cluster.fork / setupPrimary / setupMaster",
  // database -- patched on require, never required by this file
  "MongoClient": "mongodb.MongoClient (patched when the repo requires mongodb)",
  "PrismaClient": "prisma.PrismaClient / @prisma/client.PrismaClient (patched on require)",
  "better-sqlite3": "the module's whole export, which IS the constructor (patched on require)",
  "ioredis": "the module's whole export (patched on require)",
  "knex": "the module's whole export (patched on require)",
  "mongodb": "mongodb.MongoClient (patched on require)",
  "mongoose": "mongoose.connect / createConnection (patched on require)",
  "mysql": "mysql.createConnection / createPool (patched on require)",
  "mysql2": "mysql2.createConnection / createPool / createPoolCluster (patched on require)",
  "pg": "pg.Client / pg.Pool (patched on require)",
  "prisma": "prisma.PrismaClient (patched on require)",
  "redis": "redis.createClient / createCluster (patched on require)",
  "sequelize": "sequelize.Sequelize (patched on require)",
  "sqlite3": "sqlite3.Database / cached (patched on require)",
  "typeorm": "typeorm.DataSource / createConnection (patched on require)",
};

const PARTIALLY_INTERCEPTED = {
  "wasi": "the WASI constructor is guarded the moment the repo requires " +
          "node:wasi (never eagerly: requiring it prints an ExperimentalWarning " +
          "into every proof run). A WASI instance reaches its preopened " +
          "directories through its own bindings, which no JavaScript patch can " +
          "see, so what is enforced is that one cannot be BUILT.",
  "sqlite": "node:sqlite's DatabaseSync constructor is guarded on require, for " +
            "the same experimental-warning reason. Its C++ layer is not, so a " +
            "handle built before arming keeps working -- the shape every native " +
            "driver has.",
  "quic": "guarded on require where the build has node:quic at all; most do " +
          "not, and on those the marker can never fire.",
};

const NOT_INTERCEPTED = {};

const state = {
  armed: false,
  tier: null,
  blocked: new Set(),
  stdinBlocked: false,
  depth: 0,
  violations: [],
  notes: [],
};
const undo = [];

// ── Environment -> tier ──────────────────────────────────────────────────

/**
 * `{tier, allow, notes}` from the environment. Never throws.
 *
 * An absent or unparseable tier falls back to tier 1 -- the strictest setting
 * -- with a note, because the fail-safe direction for a guard is to over-block.
 * Silently defaulting to the permissive tier would let a misconfigured
 * invocation ship a test that performs real I/O. Identical rules to
 * `io_guard.py`'s `read_env`, deliberately: a user learns this contract once.
 */
function readEnv(env) {
  env = env || process.env;
  const notes = [];
  const raw = String(env[TIER_ENV] === undefined ? "" : env[TIER_ENV]).trim();
  let tier;
  if (raw === "1" || raw === "2") {
    tier = Number(raw);
  } else {
    tier = 1;
    notes.push(TIER_ENV + "=" + JSON.stringify(raw) + " is not 1 or 2; " +
               "defaulting to tier 1 (block everything), the fail-safe direction");
  }
  const rawAllow = env[ALLOW_ENV];
  let allow;
  if (rawAllow === undefined || String(rawAllow).trim() === "") {
    allow = null;
  } else if (String(rawAllow).trim().toLowerCase() === "none") {
    allow = [];
  } else {
    const wanted = String(rawAllow).replace(/;/g, ",").split(",")
      .map((g) => g.trim()).filter(Boolean);
    allow = wanted.filter((g) => GROUPS_CONTROLLABLE.indexOf(g) !== -1);
    const unknown = wanted.filter((g) => GROUPS_CONTROLLABLE.indexOf(g) === -1);
    if (unknown.length) {
      notes.push(ALLOW_ENV + " names unknown group(s) " + unknown.sort().join(", ") +
                 "; they are ignored and stay blocked");
    }
  }
  if (tier === 1 && allow !== null && allow.length) {
    notes.push("tier 1 ignores " + ALLOW_ENV + ": a unit that claimed to touch " +
               "nothing gets everything blocked");
  }
  return { tier: tier, allow: allow, notes: notes };
}

/**
 * The groups a run at `tier` blocks, given the groups it declares it fakes.
 *
 * Tier 1 blocks EVERYTHING, `allow` ignored: the unit claimed to touch nothing,
 * so any touch falsifies the classification. Tier 2 always blocks the
 * uncontrollable groups -- network, subprocess and database are never
 * permitted, however the allow list is spelled -- and permits only the
 * controllable groups the test says it deliberately fakes.
 */
function blockedGroups(tier, allow) {
  if (tier === 1) return new Set(GROUPS);
  if (allow === null || allow === undefined) return new Set(GROUPS_UNCONTROLLABLE);
  const permitted = new Set(allow.filter((g) => GROUPS_CONTROLLABLE.indexOf(g) !== -1));
  return new Set(GROUPS.filter((g) => !permitted.has(g)));
}

// ── Call provenance ──────────────────────────────────────────────────────

// The guard's own RESOLVED path, not its basename. A basename match would skip
// the frames of any file in the target repo whose path happens to contain
// `io_guard.js`, and skipped frames are unattributable frames.
const GUARD_FILE = __filename;

// A frame in a PUBLIC builtin -- `node:path:1201:24`, `node:fs:449:35`. Not
// `node:internal/...`, which is the runtime's own machinery.
const PUBLIC_BUILTIN_FRAME = /\bnode:[a-z0-9_/]+:\d+/;

/**
 * The internal modules that mean "the runtime is doing its own work".
 *
 * THE DEFAULT FOR AN INTERNAL FRAME IS TRANSPARENT, and this list is the whole
 * of the exception. It is spelled as an enumeration rather than as
 * `node:internal/` plus carve-outs because the carve-out shape is what shipped
 * the `util.promisify` hole: `util.promisify(fs.readFile)` returns a wrapper
 * DEFINED IN `node:internal/util`, so the innermost non-guard frame of every
 * promisified call is internal, and a rule that stopped at the first internal
 * frame exempted the canonical pre-`fs/promises` async idiom -- a real
 * filesystem write and a real DNS lookup, green, at tier 1, through the
 * documented command. Inverting the default closes the CLASS; adding
 * `node:internal/util` to a carve-out list would have closed one instance of
 * it and left `node:internal/fs/`, `node:internal/dns`, `node:internal/url`
 * and every wrapper a future node release introduces still exempt.
 *
 * Membership is EVIDENCE, not taxonomy: each entry is here because removing it
 * turns the runner or a clean unit red, and the four together are the whole of
 * what does. `assets/test_io_guard_node.sh` assertion 15e re-derives that both
 * ways on every run -- every promisified/callbackified route blocks, and the
 * runner still collects and reports -- so an entry added without a reason to
 * fails the suite rather than silently widening the exemption.
 *
 *   `node:internal/modules/`   The CJS and ESM loaders for the TARGET REPO's
 *                              own files. Node reads every file it loads
 *                              through `fs.readFileSync`, and a module body
 *                              always runs with the loader beneath it, so
 *                              without this every `require` inside every
 *                              module body is a filesystem violation. Removing
 *                              it: shell assertions 1, 2, 4, 12 go red.
 *   `node:internal/bootstrap/` The loader for node's OWN builtins --
 *                              `BuiltinModule.compileForInternalLoader`,
 *                              `requireBuiltin` in `bootstrap/realm`. The test
 *                              runner lazy-loads its bundled `minimatch` while
 *                              globbing for test files, and that module body
 *                              calls `Math.random` (the `randomness` group) --
 *                              through an `Array.map (<anonymous>)` frame,
 *                              which is unattributable and therefore blocks.
 *                              Same category as the line above: module
 *                              loading. Removing it: the runner cannot collect
 *                              a single test.
 *   `node:internal/test_runner/`
 *                              Collection, the execution harness and
 *                              reporting. `test()` is called FROM the target
 *                              repo's module body, so the harness's own clock
 *                              and filesystem work sits between an internal
 *                              frame and a user frame -- exactly the sandwich
 *                              this list exists for. Removing it: same, the
 *                              runner dies before the first test.
 *   `node:internal/console/`   `console.log` from a test reaches
 *                              `internal/util/colors.shouldColorize`, which
 *                              reads `process.env.FORCE_COLOR` -- the
 *                              `environment` group, blocked at tier 1. That is
 *                              node deciding whether to colourise, not the
 *                              unit reading its environment. Failing a unit
 *                              for PRINTING is not the invariant this guard
 *                              enforces, and it would make the verdict depend
 *                              on where the caller redirected stdout. Removing
 *                              it: any test that logs goes red.
 *
 * What is deliberately NOT here, and was removed once measured: `node:internal/util`
 * (promisify, callbackify, inspect), `node:internal/fs/` (the promises face),
 * `node:internal/dns`, `node:internal/timers`, `node:internal/url`,
 * `node:internal/streams/`, `node:internal/process/`, `node:internal/main/`,
 * `node:internal/crypto/`, `node:internal/deps/`. Every one of those is either
 * a route the TARGET REPO can enter directly, or sits at the bottom of a stack
 * that has no user frame on it at all -- and an all-internal stack already
 * exempts itself by reaching the end of the walk. `main/`, `process/` and
 * `streams/` were in the first draft of this list on plausibility alone; they
 * were dropped because no fixture needed them, and an exemption nothing needs
 * is a hole nothing guards.
 */
const RUNTIME_OWN_WORK = [
  "node:internal/modules/",
  "node:internal/bootstrap/",
  "node:internal/test_runner/",
  "node:internal/console/",
];

/**
 * True when the first ATTRIBUTABLE frame outward belongs to the target repo.
 *
 * Walks the stack innermost-first:
 *
 *   * this file's own frames are skipped -- deciding provenance must not read
 *     as provenance;
 *   * an internal frame naming one of `RUNTIME_OWN_WORK` STOPS the walk and
 *     exempts the call. That is what keeps the module loader alive: node reads
 *     every `.js` it loads through `fs.readFileSync`, so without it a test
 *     that touches no filesystem dies at `defaultLoadImpl
 *     (node:internal/modules/cjs/loader)`. It is also what exempts everything
 *     the test runner does on its own behalf -- globbing for test files,
 *     spawning a child per file, writing TAP, arming its own timeouts;
 *   * EVERY OTHER internal frame is TRANSPARENT: the walk continues outward.
 *     `node:internal/util` is the one that matters -- see `RUNTIME_OWN_WORK`
 *     above -- but the rule is general, so the next `node:internal/...`
 *     wrapper a node release puts between a unit and a primitive is
 *     transparent by default rather than exempt by default. The fail-safe
 *     direction for an unrecognised frame is to KEEP LOOKING for the unit, not
 *     to assume there is none;
 *   * a PUBLIC builtin frame (`node:path`, `node:fs`, `node:url`) is
 *     TRANSPARENT for the same reason. This is the distinction the rule is
 *     built on, and it is the one that generalised: the spike patched only
 *     `fs`, `net`, `http`, `child_process`, `dns` and `fetch`, all of which
 *     the code under test enters directly. Widening the guard to the
 *     `environment` group reached a primitive that the RUNTIME enters through
 *     a public builtin: `createTestFileList` -> `Glob.globSync` ->
 *     `path.resolve` -> `process.cwd`, whose innermost non-guard frame is
 *     `node:path`. Stopping there blocked the runner before it had collected a
 *     single test -- and the fix was not to exempt `node:path` but to keep
 *     walking, because the runner's stack has `node:internal/test_runner/`
 *     further out and a UNIT's does not;
 *   * anything else -- a file path, `[eval]`, `<anonymous>`, a data URL --
 *     is the target repo, and the call blocks. Unattributable frames block by
 *     design: a false positive costs one declined candidate, a false negative
 *     ships a test that performs real I/O.
 *
 * Reaching the end of the stack without an attributable frame means the call
 * came from the runtime on its own behalf: exempt. That end-of-stack case is
 * what makes the enumeration small -- an operation the runtime performs with
 * no user frame anywhere beneath it needs no entry here.
 *
 * `Error.stackTraceLimit` is raised to 64 for the duration of the walk, and
 * that number is part of the rule rather than a constant nobody chose. Under
 * the old stop-at-the-first-internal-frame rule the answer was always in the
 * first two or three frames, so 24 was generous. Walking OUTWARD past
 * transparent frames is deeper by construction -- the runner's own glob stack
 * is 14 frames before the first exempting one -- and a stack TRUNCATED before
 * the unit's frame falls off the end of the loop and exempts. 64 is chosen to
 * be past every frame count this suite has produced (the longest, the ESM
 * loader's, is 21) with room left over; the residual is stated in residual 7.
 */
function initiatedByCodeUnderTest() {
  const limit = Error.stackTraceLimit;
  const prepare = Error.prepareStackTrace;
  // V8's DEFAULT formatter, forced for the duration of the walk. A target repo
  // that installs `source-map-support` or a Sentry SDK replaces
  // `Error.prepareStackTrace`, and every frame this rule reads -- `node:`
  // prefixes, `node:internal/` paths, this file's own name -- is a property of
  // the default format. Without this line such a repo produces frames that
  // match nothing, the walk falls through to "the target repo", and a CLEAN
  // unit trips: fail-safe, but wrong and confusing. Setting the hook to
  // `undefined` and restoring it is two assignments per guarded call and makes
  // the rule independent of what the repo did to its own stacks.
  Error.stackTraceLimit = 64;
  Error.prepareStackTrace = undefined;
  let stack;
  try {
    stack = new Error().stack;
  } finally {
    Error.stackTraceLimit = limit;
    Error.prepareStackTrace = prepare;
  }
  const lines = String(stack).split("\n").slice(1);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.indexOf(GUARD_FILE) !== -1) continue;
    if (line.indexOf("node:internal/") !== -1) {
      let exempt = false;
      for (let j = 0; j < RUNTIME_OWN_WORK.length; j++) {
        if (line.indexOf(RUNTIME_OWN_WORK[j]) !== -1) { exempt = true; break; }
      }
      if (exempt) return false;
      continue;
    }
    if (PUBLIC_BUILTIN_FRAME.test(line)) continue;
    return true;
  }
  return false;
}

/**
 * Whether a guarded call trips, with the cheap checks first.
 *
 * `state.depth` is load-bearing rather than defensive: a guarded primitive's
 * own implementation may reach another guarded primitive (`fs.cpSync` walks
 * with `fs.readdirSync`), and one call by the code under test must produce one
 * decision, not one per internal hop. It is safe against the callback shape
 * because a callback the runtime invokes later runs on a fresh stack with the
 * depth back at zero.
 */
function shouldBlock(group) {
  if (!state.armed || state.depth > 0) return false;
  if (group !== "stdin" && !state.blocked.has(group)) return false;
  if (group === "stdin" && !state.stdinBlocked) return false;
  return initiatedByCodeUnderTest();
}

/**
 * Throw the violation -- and RECORD it first, at the raise.
 *
 * The record is what makes a swallowed or asynchronous trip survive: see "WHY A
 * VIOLATION IS ALSO RECORDED" in the header. Recording at the raise rather than
 * in an `uncaughtException` handler is deliberate -- a handler sees neither the
 * `catch {}` case nor a rejected promise nobody awaits.
 */
function violate(group, target) {
  const violation = new IOGuardViolation(group, target, state.tier);
  state.violations.push(violation);
  throw violation;
}

// ── Patching ─────────────────────────────────────────────────────────────

/**
 * `original`, wrapped in a Proxy that checks before calling OR constructing.
 *
 * A Proxy and not a wrapper function, because half these targets are classes:
 * see "WHAT IT PATCHES" in the header.
 */
function guarded(original, group, target) {
  return new Proxy(original, {
    apply(fn, thisArg, args) {
      if (shouldBlock(group)) violate(group, target);
      state.depth += 1;
      try {
        return Reflect.apply(fn, thisArg, args);
      } finally {
        state.depth -= 1;
      }
    },
    construct(fn, args, newTarget) {
      if (shouldBlock(group)) violate(group, target);
      state.depth += 1;
      try {
        return Reflect.construct(fn, args, newTarget);
      } finally {
        state.depth -= 1;
      }
    },
  });
}

function patchValue(obj, key, group, label) {
  if (!obj) return false;
  let original;
  try {
    original = obj[key];
  } catch (e) {
    return false;
  }
  if (typeof original !== "function") return false;
  const descriptor = Object.getOwnPropertyDescriptor(obj, key);
  if (descriptor && !descriptor.configurable && !descriptor.writable) return false;
  try {
    obj[key] = guarded(original, group, label || key);
  } catch (e) {
    return false;
  }
  undo.push(function () { obj[key] = original; });
  return true;
}

/** Patch every own function of a module -- derived, so a node release cannot
 *  quietly add a sibling this file has never heard of. `skip` names the
 *  handful that reach no I/O. */
function patchModuleFunctions(moduleName, group, skip) {
  let mod;
  try {
    mod = require(moduleName);
  } catch (e) {
    return 0;                       // not available on this node: fine
  }
  const skipped = new Set(skip || []);
  let n = 0;
  for (const key of Object.keys(mod)) {
    if (skipped.has(key)) continue;
    if (patchValue(mod, key, group, moduleName.replace(/^node:/, "") + "." + key)) n += 1;
  }
  return n;
}

/** Replace a property whose READ is the I/O (`process.env`, `process.argv`). */
function patchGetter(obj, key, group, label, factory) {
  const descriptor = Object.getOwnPropertyDescriptor(obj, key);
  if (!descriptor || !descriptor.configurable) return false;
  const value = descriptor.get ? undefined : descriptor.value;
  try {
    Object.defineProperty(obj, key, {
      configurable: true,
      enumerable: descriptor.enumerable,
      get: factory(descriptor, value),
    });
  } catch (e) {
    return false;
  }
  undo.push(function () { Object.defineProperty(obj, key, descriptor); });
  return true;
}

// ── The group patch sets ─────────────────────────────────────────────────

function patchFilesystem() {
  // Both faces. `fs.promises` and `node:fs/promises` are the SAME object, and
  // its functions are a different set from the callback face's -- patching one
  // and calling the other is the commonest way a filesystem guard misses.
  patchModuleFunctions("node:fs", "filesystem", ["promises"]);
  patchModuleFunctions("node:fs/promises", "filesystem", []);
  patchModuleFunctions("node:trace_events", "filesystem", []);
  patchValue(require("node:v8"), "writeHeapSnapshot", "filesystem",
             "v8.writeHeapSnapshot");
}

function patchNetwork() {
  for (const mod of ["node:net", "node:http", "node:https", "node:http2",
                     "node:dgram", "node:tls", "node:dns", "node:dns/promises",
                     "node:inspector", "node:inspector/promises"]) {
    patchModuleFunctions(mod, "network", ["promises"]);
  }
  const dns = (function () { try { return require("node:dns"); } catch (e) { return null; } })();
  if (dns && dns.promises) {
    for (const key of Object.keys(dns.promises)) {
      patchValue(dns.promises, key, "network", "dns.promises." + key);
    }
  }
  for (const key of ["fetch", "WebSocket", "EventSource", "XMLHttpRequest"]) {
    if (typeof globalThis[key] === "function") {
      patchValue(globalThis, key, "network", "globalThis." + key);
    }
  }
}

function patchSubprocess() {
  patchModuleFunctions("node:child_process", "subprocess", []);
  patchModuleFunctions("node:worker_threads", "subprocess", []);
  const cluster = (function () { try { return require("node:cluster"); } catch (e) { return null; } })();
  if (cluster) {
    for (const key of ["fork", "setupPrimary", "setupMaster"]) {
      patchValue(cluster, key, "subprocess", "cluster." + key);
    }
  }
}

// Entry points patched ONLY WHEN THE TARGET REPO LOADS THEM, never by this
// file. Two different reasons, both of which rule out an eager `require`:
//
//   * a database driver is a third-party package, and requiring `pg` here
//     would make a driver a dependency of a skill whose whole contract is no
//     dependencies;
//   * `node:sqlite` and `node:wasi` are EXPERIMENTAL, and merely requiring one
//     prints `ExperimentalWarning: SQLite is an experimental feature` into
//     every proof run. A guard that adds noise to the output an agent reads to
//     decide red from green is a guard that will be turned off.
//
// `names: null` means the module's ENTIRE export is the constructor
// (`better-sqlite3`, `ioredis`, `knex`), so it is returned wrapped rather than
// patched in place.
const LAZY_TARGETS = {
  "node:sqlite": { group: "database", names: ["DatabaseSync"] },
  "sqlite": { group: "database", names: ["DatabaseSync"] },
  "sqlite3": { group: "database", names: ["Database", "cached"] },
  "better-sqlite3": { group: "database", names: null },
  "pg": { group: "database", names: ["Client", "Pool"] },
  "mysql": { group: "database", names: ["createConnection", "createPool"] },
  "mysql2": { group: "database",
              names: ["createConnection", "createPool", "createPoolCluster"] },
  "mysql2/promise": { group: "database", names: ["createConnection", "createPool"] },
  "mongodb": { group: "database", names: ["MongoClient"] },
  "mongoose": { group: "database", names: ["connect", "createConnection"] },
  "redis": { group: "database", names: ["createClient", "createCluster"] },
  "ioredis": { group: "database", names: null },
  "knex": { group: "database", names: null },
  "sequelize": { group: "database", names: ["Sequelize"] },
  "typeorm": { group: "database", names: ["DataSource", "createConnection"] },
  "prisma": { group: "database", names: ["PrismaClient"] },
  "@prisma/client": { group: "database", names: ["PrismaClient"] },
  "node:wasi": { group: "filesystem", names: ["WASI"] },
  "wasi": { group: "filesystem", names: ["WASI"] },
  "node:quic": { group: "network", names: null },
  "quic": { group: "network", names: null },
};

/**
 * Patch a lazily-loaded target the moment the target repo requires it.
 *
 * `Module._load` is the one hook that sees every `require` before the caller
 * reads a property off the result, so `const { MongoClient } = require("mongodb")`
 * gets the guarded binding. RESIDUAL, stated: a native ESM package reached by
 * `import` does not pass through `Module._load`, so a repo that imports
 * `mongodb` as ESM keeps the real client. Its first connection still trips as
 * `network`, which is blocked at every tier -- the `database` label is what is
 * lost, not the enforcement.
 */
function hookModuleLoad() {
  let Module;
  try {
    Module = require("node:module");
  } catch (e) {
    return;
  }
  const original = Module._load;
  if (typeof original !== "function") return;
  Module._load = function (request) {
    const exports = original.apply(this, arguments);
    const target = Object.prototype.hasOwnProperty.call(LAZY_TARGETS, request)
      ? LAZY_TARGETS[request] : null;
    if (!state.armed || !target || !state.blocked.has(target.group)) return exports;
    if (target.names === null) {
      return typeof exports === "function"
        ? guarded(exports, target.group, request) : exports;
    }
    for (const key of target.names) {
      if (exports && typeof exports[key] === "function") {
        patchValue(exports, key, target.group, request + "." + key);
      }
    }
    return exports;
  };
  undo.push(function () { Module._load = original; });
}

function patchClock() {
  patchValue(globalThis.Date, "now", "clock", "Date.now");
  // `new Date()` with NO arguments reads the wall clock; `new Date(0)` does
  // not, and blocking it would make the guard stricter than the marker table
  // it has to agree with. A Proxy over the constructor is the only place that
  // distinction is visible, and it keeps `instanceof Date` working for every
  // date the unit builds.
  const RealDate = globalThis.Date;
  const guardedDate = new Proxy(RealDate, {
    construct(target, args, newTarget) {
      if (args.length === 0 && shouldBlock("clock")) violate("clock", "new Date()");
      return Reflect.construct(target, args, newTarget);
    },
    apply(target, thisArg, args) {
      if (args.length === 0 && shouldBlock("clock")) violate("clock", "Date()");
      return Reflect.apply(target, thisArg, args);
    },
  });
  globalThis.Date = guardedDate;
  undo.push(function () { globalThis.Date = RealDate; });

  for (const key of ["setTimeout", "setInterval", "setImmediate"]) {
    patchValue(globalThis, key, "clock", "globalThis." + key);
  }
  patchModuleFunctions("node:timers", "clock", ["promises"]);
  patchModuleFunctions("node:timers/promises", "clock", []);
  for (const key of ["hrtime", "uptime"]) {
    patchValue(process, key, "clock", "process." + key);
  }
  if (globalThis.performance) {
    patchValue(globalThis.performance, "now", "clock", "performance.now");
  }
  patchModuleFunctions("node:perf_hooks", "clock", ["performance", "constants"]);
}

function patchRandomness() {
  patchValue(Math, "random", "randomness", "Math.random");
  const crypto = (function () { try { return require("node:crypto"); } catch (e) { return null; } })();
  if (crypto) {
    for (const key of Object.keys(crypto)) {
      if (/^(random|generateKey|generatePrime)/.test(key) ||
          key === "getRandomValues") {
        patchValue(crypto, key, "randomness", "crypto." + key);
      }
    }
  }
  if (globalThis.crypto) {
    for (const key of ["getRandomValues", "randomUUID"]) {
      patchValue(globalThis.crypto, key, "randomness", "crypto." + key);
    }
  }
}

function patchEnvironment() {
  // `process.env.X` is a plain property read, so the only interception point
  // JavaScript offers is a Proxy over the mapping. What it covers: every VALUE
  // read, including destructuring and `Object.values`. What it does NOT: `in`
  // and `Object.keys(...).length`, neither of which reads a value.
  const realEnv = process.env;
  const guardedEnv = new Proxy(realEnv, {
    get(target, key) {
      if (typeof key === "string" && shouldBlock("environment")) {
        violate("environment", "process.env." + key);
      }
      return target[key];
    },
  });
  try {
    process.env = guardedEnv;
    undo.push(function () { process.env = realEnv; });
  } catch (e) { /* a node build that refuses the assignment: residual 5 */ }

  for (const key of ["argv", "argv0"]) {
    patchGetter(process, key, "environment", "process." + key, function (d, value) {
      return function () {
        if (shouldBlock("environment")) violate("environment", "process." + key);
        return d.get ? d.get.call(process) : value;
      };
    });
  }
  for (const key of ["cwd", "chdir", "umask"]) {
    patchValue(process, key, "environment", "process." + key);
  }
  patchModuleFunctions("node:os", "environment", ["constants", "EOL", "devNull"]);
}

/**
 * Terminal input, blocked at tier 1 only, and outside the group system.
 *
 * The `process.stdin` getter is the lowest reachable layer: `readline`,
 * `node:tty` and a hand-rolled `on("data")` all have to go through it. The
 * `readline` entry points are patched too, so a unit handed a stream from
 * somewhere else still fails rather than waits.
 */
function patchStdin() {
  patchGetter(process, "stdin", "stdin", "process.stdin", function (d, value) {
    return function () {
      if (shouldBlock("stdin")) violate("stdin", "process.stdin");
      return d.get ? d.get.call(process) : value;
    };
  });
  patchModuleFunctions("node:readline", "stdin", ["promises"]);
  patchModuleFunctions("node:readline/promises", "stdin", []);
}

// ── Arm / disarm ─────────────────────────────────────────────────────────

/** Install the guard for a proof run at `tier`. Idempotent. */
function arm(tier, allow) {
  if (state.armed) return;
  state.tier = tier;
  state.blocked = blockedGroups(tier, allow === undefined ? null : allow);
  state.stdinBlocked = tier === 1;
  state.depth = 0;
  state.violations.length = 0;
  state.armed = true;

  if (state.blocked.has("filesystem")) patchFilesystem();
  if (state.blocked.has("network")) patchNetwork();
  if (state.blocked.has("subprocess")) patchSubprocess();
  hookModuleLoad();   // the lazy targets span three groups; the hook checks
  if (state.blocked.has("clock")) patchClock();
  if (state.blocked.has("randomness")) patchRandomness();
  if (state.blocked.has("environment")) patchEnvironment();
  if (state.stdinBlocked) patchStdin();
}

/** Restore every patched name, innermost patch first. Idempotent. */
function disarm() {
  state.armed = false;
  state.depth = 0;
  while (undo.length) {
    const restore = undo.pop();
    try {
      restore();
    } catch (e) { /* a name the target repo froze under us: nothing to do */ }
  }
  state.tier = null;
  state.blocked = new Set();
  state.stdinBlocked = false;
}

function pendingViolations() {
  return state.violations.slice();
}

// ── The backstop ─────────────────────────────────────────────────────────
//
// A trip that was caught, or raised on a later turn of the event loop, must
// still cost the run its exit status. `node --test` reports an asynchronous
// failure against the FILE and prints `ok` for the test itself, so a proof loop
// reading the TAP line alone would be told the wrong thing; the exit status is
// the signal, and this is what makes it true.
process.on("exit", function () {
  if (!state.violations.length) return;
  // Only when the run would otherwise be GREEN. A violation that already
  // reached the test result has been reported by the runner, in the place a
  // reader looks first, and printing it a second time here buries the stack
  // that names the unit's own line under a duplicate. The backstop exists for
  // the trips that reached nothing: a `catch {}`, or a raise on a later turn of
  // the event loop.
  if (process.exitCode) return;
  const first = state.violations[0];
  process.exitCode = 1;
  // The NAME is printed, not just the message: a caller (and this skill's own
  // suite) recognises a trip by `IOGuardViolation`, and a record surfaced here
  // was never thrown past the unit, so nothing else prints it. The stack is
  // included because it is the only thing that says WHICH line of the unit
  // reached the primitive -- by the time this runs, the throw is long gone.
  console.error("test-safety-net io_guard: " + (state.violations.length > 1
    ? state.violations.length + " violations were recorded; the first:\n" : "") +
    (first.stack || (first.name + ": " + first.message)));
});

// ── Auto-arm ─────────────────────────────────────────────────────────────
//
// `--require` offers no hook to arm from, so loading IS arming: the module body
// is the only code that runs before the test file. Anything that wants the
// tables without the patches (`test_stack_node.py` reads them to prove they
// partition the filter's marker tables) requires this file and calls `disarm()`
// immediately, which is the same contract `pytest_unconfigure` has.
const _env = readEnv(process.env);
state.notes = _env.notes;
arm(_env.tier, _env.allow);

module.exports = {
  IOGuardViolation: IOGuardViolation,
  arm: arm,
  disarm: disarm,
  armed: function () { return state.armed; },
  tier: function () { return state.tier; },
  blockedGroups: blockedGroups,
  readEnv: readEnv,
  pendingViolations: pendingViolations,
  notes: function () { return state.notes.slice(); },
  FILTER_MARKER_INTERCEPTS: FILTER_MARKER_INTERCEPTS,
  PARTIALLY_INTERCEPTED: PARTIALLY_INTERCEPTED,
  NOT_INTERCEPTED: NOT_INTERCEPTED,
  GROUPS: GROUPS,
  CONTROLLABLE_GROUPS: GROUPS_CONTROLLABLE,
  UNCONTROLLABLE_GROUPS: GROUPS_UNCONTROLLABLE,
};
