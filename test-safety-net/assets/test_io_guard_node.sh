#!/bin/sh
# test-safety-net/assets/test_io_guard_node.sh
# gate: offline — runs in `make gate`. Must stay offline and deterministic: no
# network (the network fixtures CONSTRUCT a socket and never dial), no fixed
# ports, no wall-clock dependence, no writes outside $WORK.
#
# WHY THIS FILE EXISTS, AND WHY ITS FIRST ASSERTION IS THE ONE IT IS.
#
# `io_guard.js` is the sole enforcement of this skill's headline invariant,
# "never writes a test that performs real I/O", for node. The static triage in
# `stack_node.py` is a FILTER; this is the enforcement, so a guard that loads
# but blocks nothing is indistinguishable from no guard at all in every check
# that only asks "did the clean test pass".
#
# The Python guard learned that the expensive way. Its suite ran
# `python -m pytest`, every document printed `pytest`, and the two are not the
# same command: 122 unit tests and 34 eval checks were green over a guard whose
# only user-facing invocation was broken three separate ways. So assertion 1
# here EXTRACTS the invocation from the documents that print it and runs it
# verbatim, in both directions — a clean unit must pass, a unit that really
# does I/O must fail — and it is deliberately the first thing in the file.
set -eu

ASSETS="$(cd "$(dirname "$0")" && pwd)"
SKILL="$(cd "$ASSETS/.." && pwd)"
GUARD="$ASSETS/io_guard.js"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

rc=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1"; rc=1; }
note() { echo "      $1"; }

if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: no \`node\` on PATH -- io_guard.js is NOT exercised on this machine."
  echo "      Install node 18+ to run this suite."
  exit 0
fi

# ── Fixtures ─────────────────────────────────────────────────────────────
# A "unit" and a test for it, in each shape the guard has to tell apart.
cat > "$WORK/pure.js" <<'EOF'
function add(a, b) { return a + b; }
module.exports = { add };
EOF
cat > "$WORK/test_pure.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { add } = require("./pure.js");
test("adds", () => { console.log("captured output"); assert.strictEqual(add(2, 3), 5); });
test("subtracts", () => { assert.strictEqual(add(2, -3), -1); });
EOF

cat > "$WORK/leaky.js" <<'EOF'
const fs = require("node:fs");
function hosts() { return fs.readFileSync("/etc/hosts", "utf8").length; }
module.exports = { hosts };
EOF
cat > "$WORK/test_leaky.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { hosts } = require("./leaky.js");
test("adds", () => { assert.ok(hosts() >= 0); });
EOF

# I/O in the MODULE BODY. The node analogue of the hole the Python guard shipped
# for a round: every module body runs with the loader's own frames on the stack,
# so a provenance rule that scans for "is an import happening anywhere below me"
# exempts all of them, and a module that reads an arbitrary file at load time
# does so at tier 1 with a green proof.
cat > "$WORK/importleak.js" <<'EOF'
const fs = require("node:fs");
const SIZE = fs.readFileSync("/etc/hosts", "utf8").length;
function size() { return SIZE; }
module.exports = { size };
EOF
cat > "$WORK/test_importleak.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { size } = require("./importleak.js");
test("adds", () => { assert.ok(size() >= 0); });
EOF

# Network, without a network: constructing a socket is the guarded primitive,
# and nothing is ever dialled.
cat > "$WORK/netleak.js" <<'EOF'
const net = require("node:net");
function client() { return new net.Socket() !== null; }
module.exports = { client };
EOF
cat > "$WORK/test_netleak.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { client } = require("./netleak.js");
test("adds", () => { assert.ok(client()); });
EOF

cat > "$WORK/spawnleak.js" <<'EOF'
const { execSync } = require("node:child_process");
function run() { return String(execSync("echo hi")).trim(); }
module.exports = { run };
EOF
cat > "$WORK/test_spawnleak.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { run } = require("./spawnleak.js");
test("adds", () => { assert.strictEqual(run(), "hi"); });
EOF

# A violation the UNIT swallows. JavaScript has no `BaseException`: a bare
# `catch` catches everything, and `try { fs.readFileSync(cache) } catch {}` is
# one of the commonest shapes in real code. Without a record kept at the raise,
# this test passes, the runner exits 0, and the proof is green over a unit that
# really did read the filesystem.
cat > "$WORK/swallow.js" <<'EOF'
const fs = require("node:fs");
function cached() {
  try { return fs.readFileSync("/etc/hosts", "utf8"); } catch { return "default"; }
}
module.exports = { cached };
EOF
cat > "$WORK/test_swallow.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { cached } = require("./swallow.js");
test("adds", () => { assert.ok(cached().length > 0); });
EOF

# A violation raised on a later turn of the event loop.
cat > "$WORK/asyncleak.js" <<'EOF'
const fs = require("node:fs");
function later() {
  return new Promise((resolve) => {
    setImmediate(() => { resolve(fs.readFileSync("/etc/hosts", "utf8").length); });
  });
}
module.exports = { later };
EOF
cat > "$WORK/test_asyncleak.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { later } = require("./asyncleak.js");
test("adds", async () => { assert.ok(await later() >= 0); });
EOF

# Terminal input. In NEITHER stack's marker table, so the filter cannot decline
# it -- and a unit that reads stdin HANGS the proof run instead of failing it.
cat > "$WORK/stdinleak.js" <<'EOF'
const readline = require("node:readline");
function ask() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => rl.question("name? ", (a) => { rl.close(); resolve(a); }));
}
module.exports = { ask };
EOF
cat > "$WORK/test_stdinleak.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { ask } = require("./stdinleak.js");
test("adds", async () => { assert.ok(await ask()); });
EOF

for group in clock:'Date.now()' randomness:'Math.random()' environment:'process.env.APP_REGION || "none"'; do
  name="${group%%:*}"; expr="${group#*:}"
  cat > "$WORK/$name.js" <<EOF
function value() { return $expr; }
module.exports = { value };
EOF
  cat > "$WORK/test_$name.js" <<EOF
const { test } = require("node:test");
const assert = require("node:assert");
const { value } = require("./$name.js");
test("adds", () => { assert.ok(value() !== undefined); });
EOF
done

cat > "$WORK/catcher.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { hosts } = require("./leaky.js");
test("adds", () => {
  try { hosts(); } catch (e) {
    console.log("CAUGHT name=" + e.name
                + " isAssertion=" + (e instanceof assert.AssertionError)
                + " isError=" + (e instanceof Error));
  }
});
EOF

# ── Runners ──────────────────────────────────────────────────────────────
# One helper per shape, and both of them run the guard the way a proof run
# does: `--require` ahead of the test file, one test selected by name.
guard_run() {   # tier allow file pattern
  set +e
  OUT="$(cd "$WORK" && TEST_SAFETY_NET_TIER="$1" TEST_SAFETY_NET_ALLOW="$2" \
         node --require "$GUARD" --test --test-name-pattern "$4" "$3" 2>&1)"
  ST=$?
  set -e
}

# The same, with a deadline and a stdin that never delivers a line: a FIFO this
# script holds open for writing, so a real read blocks forever rather than
# seeing EOF. A guard that does not block stdin HANGS here, which is the
# failure this asserts against -- so the deadline is the assertion's teeth.
deadline_run() {   # seconds tier file pattern
  rm -f "$WORK/fifo" "$WORK/out"
  mkfifo "$WORK/fifo"
  exec 7<>"$WORK/fifo"
  set +e
  # The file is named by ABSOLUTE path, so both the runner and the child it
  # spawns carry $WORK in their argv -- which is what makes the watcher's
  # `pkill` below able to reach either of them. With a relative name only the
  # child matched, and a broken guard left the runner hung on the FIFO forever.
  ( cd "$WORK" && TEST_SAFETY_NET_TIER="$2" \
      node --require "$GUARD" --test --test-name-pattern "$4" "$WORK/$3" \
      >"$WORK/out" 2>&1 <"$WORK/fifo" ) &
  runner=$!
  ( i=0
    while [ "$i" -lt "$(( $1 * 10 ))" ]; do
      kill -0 "$runner" 2>/dev/null || exit 0
      sleep 0.1; i=$((i + 1))
    done
    echo "GUARD-TEST-TIMEOUT" >> "$WORK/out"
    pkill -9 -f "$WORK" 2>/dev/null
    kill -9 "$runner" 2>/dev/null ) &
  watcher=$!
  wait "$runner"; ST=$?
  kill "$watcher" 2>/dev/null
  set -e
  exec 7>&-
  OUT="$(cat "$WORK/out")"
}

tripped() { case "$OUT" in *IOGuardViolation*) return 0 ;; *) return 1 ;; esac; }

expect_pass() {   # label
  if [ "$ST" -eq 0 ] && ! tripped; then ok "$1"
  else bad "$1 (exit=$ST)"; note "$(printf '%s' "$OUT" | tail -6)"; fi
}
expect_trip() {   # label
  if [ "$ST" -ne 0 ] && tripped; then ok "$1"
  else bad "$1 (exit=$ST, no IOGuardViolation in output)"
       note "$(printf '%s' "$OUT" | tail -6)"; fi
}

# ── 1. THE DOCUMENTED COMMAND, EXTRACTED AND RUN VERBATIM ────────────────
# Every place this skill prints a node guard invocation is a place it can be
# wrong. The extraction below reads `io_guard.js`'s own header AND every
# markdown file in the skill, strips comment leaders, joins backslash
# continuations, and runs whatever comes out -- so a document that drifts from
# the guard fails this suite rather than a user's proof run.
extract_commands() {
  for f in "$GUARD" "$SKILL"/*.md "$SKILL"/references/*.md; do
    [ -f "$f" ] || continue
    sed -e 's/^[[:space:]]*\*[[:space:]]\{0,1\}//' -e 's/^[[:space:]]*//' "$f" \
      | awk '{ if (sub(/[\\]$/, "")) { printf "%s", $0 } else { print } }' \
      | grep -E '^TEST_SAFETY_NET_TIER=[12] .*node .*--require .*io_guard\.js' \
      | sed -e 's/[[:space:]]\{1,\}/ /g'
  done
}

COMMANDS="$(extract_commands | sort -u || true)"
if [ -z "$COMMANDS" ]; then
  bad "1 the documented node guard invocation exists and is runnable"
  note "no line matching a documented \`node --require ... io_guard.js\` invocation"
  note "was found in io_guard.js or in any of the skill's markdown files"
else
  n=0
  fail_doc=""
  # `SKILL_DIR` is the variable every document uses, because an agent runs from
  # the TARGET repo and a skill-relative path never resolves there.
  SKILL_DIR="$SKILL"; export SKILL_DIR
  while IFS= read -r command; do
    [ -n "$command" ] || continue
    n=$((n + 1))
    for arm in pass:test_pure.js trip:test_netleak.js; do
      want="${arm%%:*}"; file="${arm#*:}"
      cmd="$(printf '%s' "$command" \
             | sed -e "s|<path>|$file|g" -e "s|<test_name>|adds|g")"
      set +e
      OUT="$(cd "$WORK" && eval "$cmd" 2>&1)"; ST=$?
      set -e
      if [ "$want" = pass ]; then
        { [ "$ST" -eq 0 ] && ! tripped; } || fail_doc="$fail_doc [clean: $cmd -> exit $ST]"
      else
        { [ "$ST" -ne 0 ] && tripped; } || fail_doc="$fail_doc [leaky: $cmd -> exit $ST]"
      fi
    done
  done <<COMMANDS_EOF
$COMMANDS
COMMANDS_EOF
  if [ -z "$fail_doc" ]; then
    ok "1 the DOCUMENTED command, extracted from every document that prints it and run verbatim, passes a clean unit AND fails one that really does I/O ($n command(s), each run twice)"
  else
    bad "1 the DOCUMENTED command$fail_doc"
  fi
fi

# ── 2. The module loader still works ─────────────────────────────────────
# THE FIRST THING A NAIVE PORT BREAKS. Node reads every `.js` and `.mjs` file
# it loads through `fs.readFileSync`, so a guard that blocks on the NAME alone
# kills a test that touches no filesystem at all, at `defaultLoadImpl
# (node:internal/modules/cjs/loader)`, before the test body ever runs.
guard_run 1 "" test_pure.js '^adds$'
expect_pass "2 a clean unit passes at tier 1 -- the module loader's own reads are not the unit's"

# ── 3. A unit that really reads a file ───────────────────────────────────
guard_run 1 "" test_leaky.js '^adds$'
expect_trip "3 a unit that reads /etc/hosts trips at tier 1"
case "$OUT" in
  *"Tier 3"*) ok "3b the message says what to DO: reclassify to Tier 3 and discard" ;;
  *) bad "3b the message names no remedy"; note "$(printf '%s' "$OUT" | tail -4)" ;;
esac
case "$OUT" in
  *filesystem*) ok "3c the message names the GROUP that tripped" ;;
  *) bad "3c the message does not name the group" ;;
esac

# ── 4. A guard trip is not an assertion failure ──────────────────────────
guard_run 1 "" catcher.js '^adds$'
case "$OUT" in
  *"CAUGHT name=IOGuardViolation isAssertion=false"*)
    ok "4 IOGuardViolation is NOT an assert.AssertionError -- a trip means the CLASSIFICATION is wrong, an assertion failure means the captured VALUE is" ;;
  *) bad "4 IOGuardViolation is not distinguishable from an assertion failure"
     note "$(printf '%s' "$OUT" | grep CAUGHT || echo 'no CAUGHT line')" ;;
esac

# ── 5. I/O in a module body ──────────────────────────────────────────────
guard_run 1 "" test_importleak.js '^adds$'
expect_trip "5 a module BODY that reads a file trips, though the loader's frames are on the stack"

# ── 5b. A public builtin frame is transparent, not an exemption ──────────
# `path.resolve("./x")` reads the cwd through `node:path`, which is exactly the
# route the test runner's own file globbing takes. The runner's is exempt
# because a `node:internal/` frame sits below it; the unit's is not, because
# the walk keeps going outward and finds the unit.
cat > "$WORK/pathleak.js" <<'EOF'
const path = require("node:path");
function absolute(p) { return path.resolve(p); }
module.exports = { absolute };
EOF
cat > "$WORK/test_pathleak.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { absolute } = require("./pathleak.js");
test("adds", () => { assert.ok(absolute("./x").length > 2); });
EOF
guard_run 1 "" test_pathleak.js '^adds$'
expect_trip "5b a unit reaching process.cwd THROUGH node:path still trips -- a public builtin frame is transparent, not an exemption"

# ── 5c. The same, through ESM ────────────────────────────────────────────
# `--require` preloads CommonJS, and a reader could reasonably expect it to be
# invisible to an `.mjs` test file. It is not: the preload runs before the ESM
# loader starts, and a builtin's ESM namespace is built from the CJS object at
# first import, so `import { readFileSync } from "node:fs"` binds the GUARDED
# function. Pinned because the alternative -- documenting `--import` as a
# second invocation -- is a second command to keep true.
cat > "$WORK/leaky.mjs" <<'EOF'
import { readFileSync } from "node:fs";
export function hosts() { return readFileSync("/etc/hosts", "utf8").length; }
EOF
cat > "$WORK/test_leaky_esm.mjs" <<'EOF'
import { test } from "node:test";
import assert from "node:assert";
import { hosts } from "./leaky.mjs";
test("adds", () => { assert.ok(hosts() >= 0); });
EOF
cat > "$WORK/pure.mjs" <<'EOF'
export function add(a, b) { return a + b; }
EOF
cat > "$WORK/test_pure_esm.mjs" <<'EOF'
import { test } from "node:test";
import assert from "node:assert";
import { add } from "./pure.mjs";
test("adds", () => { assert.strictEqual(add(2, 3), 5); });
EOF
guard_run 1 "" test_pure_esm.mjs '^adds$'
expect_pass "5c a clean ESM unit passes -- the ESM loader's own reads are not the unit's either"
guard_run 1 "" test_leaky_esm.mjs '^adds$'
expect_trip "5d an ESM unit's NAMED import of readFileSync is the guarded binding, because --require runs before the loader"

# ── 6. The uncontrollable groups ─────────────────────────────────────────
guard_run 1 "" test_netleak.js '^adds$'
expect_trip "6 constructing a socket trips at tier 1"
guard_run 2 filesystem,clock,environment,randomness test_netleak.js '^adds$'
expect_trip "6b network is NEVER permitted, at any tier, however TEST_SAFETY_NET_ALLOW is spelled"
guard_run 2 filesystem test_spawnleak.js '^adds$'
expect_trip "6c a subprocess is never permitted either"

# ── 7. Swallowed and asynchronous violations still reach the result ──────
guard_run 1 "" test_swallow.js '^adds$'
expect_trip "7 a violation the unit CATCHES still fails the run (JavaScript has no uncatchable exception)"
guard_run 2 clock test_asyncleak.js '^adds$'
expect_trip "7b a violation raised on a later turn of the event loop still fails the run"

# ── 8. stdin fails fast instead of hanging ───────────────────────────────
deadline_run 20 1 test_stdinleak.js '^adds$'
case "$OUT" in
  *GUARD-TEST-TIMEOUT*)
    bad "8 a unit that reads stdin HUNG the proof run instead of failing it" ;;
  *) expect_trip "8 a unit that reads stdin fails fast at tier 1 rather than hanging the proof run" ;;
esac

# ── 9. Tiers ─────────────────────────────────────────────────────────────
guard_run 2 filesystem test_leaky.js '^adds$'
expect_pass "9 tier 2 permits the groups TEST_SAFETY_NET_ALLOW names"
guard_run 2 "" test_leaky.js '^adds$'
expect_pass "9b tier 2 with no allow list is the spec's baseline: block the UNCONTROLLABLE groups only"
guard_run 2 none test_leaky.js '^adds$'
expect_trip "9b2 TEST_SAFETY_NET_ALLOW=none tightens tier 2 to \"this test fakes nothing\""
guard_run 1 filesystem,clock test_leaky.js '^adds$'
expect_trip "9c tier 1 IGNORES the allow list: a unit that claimed to touch nothing gets everything blocked"
guard_run "" "" test_leaky.js '^adds$'
expect_trip "9d an absent tier defaults to tier 1, the fail-safe direction"
guard_run banana "" test_leaky.js '^adds$'
expect_trip "9e an unparseable tier defaults to tier 1 too"
guard_run 2 filesystem,teleportation test_leaky.js '^adds$'
expect_pass "9f an unknown group in the allow list is ignored, and the known one still works"
guard_run 2 teleportation test_leaky.js '^adds$'
expect_trip "9g an unknown group NEVER unblocks anything"

for group in clock randomness environment; do
  guard_run 1 "" "test_$group.js" '^adds$'
  expect_trip "10 the $group group is blocked at tier 1"
  guard_run 2 "$group" "test_$group.js" '^adds$'
  expect_pass "10b tier 2 permits $group when the test says it fakes it"
done

# ── 11. Teardown restores every patched name ─────────────────────────────
set +e
OUT="$(cd "$WORK" && node -e '
const fs = require("node:fs");
const cp = require("node:child_process");
const net = require("node:net");
const before = {
  read: fs.readFileSync, exec: cp.execSync, sock: net.Socket,
  date: globalThis.Date, rand: Math.random, fetch: globalThis.fetch,
  env: process.env, stdin: Object.getOwnPropertyDescriptor(process, "stdin").get,
};
process.env.TEST_SAFETY_NET_TIER = "1";
const guard = require(process.argv[1]);
const patched = Object.keys(before).filter((k) => {
  switch (k) {
    case "read": return fs.readFileSync !== before.read;
    case "exec": return cp.execSync !== before.exec;
    case "sock": return net.Socket !== before.sock;
    case "date": return globalThis.Date !== before.date;
    case "rand": return Math.random !== before.rand;
    case "fetch": return globalThis.fetch !== before.fetch;
    case "env": return process.env !== before.env;
    default: return Object.getOwnPropertyDescriptor(process, "stdin").get !== before.stdin;
  }
});
guard.disarm();
const left = [];
if (fs.readFileSync !== before.read) left.push("fs.readFileSync");
if (cp.execSync !== before.exec) left.push("child_process.execSync");
if (net.Socket !== before.sock) left.push("net.Socket");
if (globalThis.Date !== before.date) left.push("Date");
if (Math.random !== before.rand) left.push("Math.random");
if (globalThis.fetch !== before.fetch) left.push("fetch");
if (process.env !== before.env) left.push("process.env");
if (Object.getOwnPropertyDescriptor(process, "stdin").get !== before.stdin) left.push("process.stdin");
console.log("PATCHED=" + patched.length + " LEFTOVER=" + left.join(","));
' "$GUARD" 2>&1)"
ST=$?
set -e
case "$OUT" in
  *"PATCHED=8 LEFTOVER="*) ok "11 arm patches every family, and disarm restores all of them" ;;
  *) bad "11 arm/disarm did not round-trip (exit=$ST)"; note "$OUT" ;;
esac

# ── 12. WHO node --test BLAMES for an ASYNC violation ────────────────────
# The rule with no python equivalent, and the reason SKILL.md tells an agent to
# read `$?` and not the per-test lines. When a violation lands AFTER the test
# that caused it resolved, `node --test` does not blame that test: it prints
# `ok` for it and puts the `not ok` on a DIFFERENT entry -- the enclosing file
# on node 22, and one test further along on the build this rule was first
# reproduced against. Either way an agent reading per-test results keeps the
# test that performs real I/O and discards a clean one, which is exactly
# inverted and completely silent.
#
# So the assertion is deliberately shaped as "the culprit says ok AND somebody
# else carries the not ok", which holds for both observed shapes and fails the
# moment either half stops being true:
#   * a guard that never arms  -> the read succeeds, exit 0, no violation;
#   * node learning to blame the right test -> `blamed` becomes the culprit,
#     this goes red, and the prose in SKILL.md / references/stacks.md gets
#     WEAKER rather than quietly staying wrong.
#
# The whole FILE is run, with no `--test-name-pattern`: the batch is the unit
# the rule is about.
cat > "$WORK/asyncmisattrib.js" <<'EOF'
const fs = require("node:fs");
function scheduleRead() {
  setTimeout(() => { fs.readFileSync("/etc/hosts", "utf8"); }, 40);
  return true;
}
module.exports = { scheduleRead };
EOF
cat > "$WORK/test_asyncmisattrib.js" <<'EOF'
const { test } = require("node:test");
const assert = require("node:assert");
const { scheduleRead } = require("./asyncmisattrib.js");
test("fast_test_slow_violation", () => { assert.ok(scheduleRead()); });
test("second_test_keeps_process_alive", async () => {
  await new Promise((r) => setTimeout(r, 400));
  assert.ok(true);
});
EOF
set +e
OUT="$(cd "$WORK" && TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=clock \
       node --require "$GUARD" --test test_asyncmisattrib.js 2>&1)"
ST=$?
set -e
culprit_ok="$(printf '%s\n' "$OUT" | grep -cE '^ok [0-9]+ - fast_test_slow_violation$' || true)"
blamed="$(printf '%s\n' "$OUT" | grep -E '^not ok [0-9]+ - ' \
          | sed -e 's/^not ok [0-9]* - //' | head -1 || true)"
if [ "$ST" -ne 0 ] && tripped && [ "$culprit_ok" -eq 1 ] \
   && [ -n "$blamed" ] && [ "$blamed" != "fast_test_slow_violation" ]; then
  ok "12 an ASYNC violation exits nonzero but is blamed on the wrong entry (\"$blamed\", not fast_test_slow_violation, which printed ok) -- so the EXIT STATUS is the only trustworthy signal on this stack"
else
  bad "12 the async-misattribution shape SKILL.md documents did not reproduce (exit=$ST, culprit_ok=$culprit_ok, blamed='$blamed')"
  note "$(printf '%s' "$OUT" | grep -E '^(ok|not ok)' | head -4)"
fi

exit "$rc"
