#!/bin/sh
# mockstar-mock/assets/test_smoke_offline.sh — smoke.sh's route-checking logic.
# gate: offline — runs in `make gate`. No bunx, no docker, no network: a stub
# HTTP server on an ephemeral loopback port stands in for the mock server.
#
# Why this file exists: smoke.sh is the component whose entire job is
# verification, and its only behavioural test lived in a `# gate: integration`
# suite that the gate never runs. That is how it shipped silently dropping the
# LAST route in the routes file whenever the file lacked a trailing newline —
# `read` returns non-zero on a final partial line — and still exiting 0. A model
# writes that file at stage 5, so a missing final newline is realistic output,
# and the result was a project certified green with an unprobed endpoint.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
SMOKE="$DIR/smoke.sh"
WORK="$(mktemp -d)"
STUB=""
cleanup() {
  [ -n "$STUB" ] && kill "$STUB" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

rc=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1"; rc=1; }

# ── 1. Usage error without arguments ─────────────────────────────────────────
if sh "$SMOKE" >/dev/null 2>&1; then
  bad "usage errors without args"
else
  ok "usage errors without args"
fi

# ── Stub server on an EPHEMERAL port (a fixed port is forbidden here) ─────────
PORT="$(python3 -c "
import socket
s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")"
(cd "$WORK" && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
STUB=$!
BASE="http://127.0.0.1:$PORT"

# Wait for readiness rather than sleeping a fixed amount — determinism.
i=0
until curl -s -o /dev/null "$BASE/" 2>/dev/null || [ "$i" -ge 50 ]; do
  i=$((i + 1))
  python3 -c "import time; time.sleep(0.1)"
done
if [ "$i" -ge 50 ]; then
  echo "FAIL: stub server never came up on $PORT"
  exit 1
fi

R="$WORK/routes.tsv"

# ── 2. A matching expectation passes ─────────────────────────────────────────
printf 'GET\t/\t200\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  ok "smoke matches an expected 200"
else
  bad "smoke matches an expected 200"
fi

# ── 3. A mismatch fails (the check discriminates) ────────────────────────────
printf 'GET\t/does-not-exist\t200\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  bad "smoke detects a status mismatch"
else
  ok "smoke detects a status mismatch"
fi

# ── 4. THE REGRESSION: no trailing newline must not drop the last route ──────
printf 'GET\t/\t200\nGET\t/nope\t404' > "$R"          # deliberately no final \n
out="$(MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" 2>&1 || true)"
n="$(printf '%s\n' "$out" | grep -c '^PASS: ')"
if [ "$n" -eq 2 ]; then
  ok "routes file without a trailing newline: every route probed"
else
  bad "routes file without a trailing newline: every route probed (probed $n of 2)"
fi
if printf '%s\n' "$out" | grep -q 'probed 2/2 route(s)'; then
  ok "smoke reports its probed/declared route count"
else
  bad "smoke reports its probed/declared route count"
fi

# ── 5. A dropped route must FAIL, never pass quietly ─────────────────────────
# Guards the count itself: if the read loop ever regresses, probed < declared
# and the run must go red rather than reporting success on partial coverage.
printf 'GET\t/\t200\nGET\t/\t200\n# a comment line\n\n' > "$R"
out="$(MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" 2>&1 || true)"
if printf '%s\n' "$out" | grep -q 'probed 2/2 route(s)'; then
  ok "comments and blank lines are excluded from the declared count"
else
  bad "comments and blank lines are excluded from the declared count"
fi

# ── 6. Encoded responses (Content-Encoding) ──────────────────────────────────
# A second stub serves a gzip body, a body that claims gzip but is plain, a gzip
# body with no header, and a gzip body of the wrong content. Each smoke result
# must follow the bytes.
cat > "$WORK/enc_stub.py" <<'PYEOF'
import gzip, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
BODY = b'{"id":"pet_1","name":"Rex"}'
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/gz":
            data, enc = gzip.compress(BODY, mtime=0), "gzip"
        elif self.path == "/lie":        # header says gzip, bytes are plain
            data, enc = BODY, "gzip"
        elif self.path == "/noenc":      # gzip bytes, no Content-Encoding header
            data, enc = gzip.compress(BODY, mtime=0), None
        elif self.path == "/wrongbody":  # valid gzip of a different body
            data, enc = gzip.compress(b"other", mtime=0), "gzip"
        elif self.path == "/hdr" and self.headers.get("x-want-encoding") == "gzip":
            data, enc = gzip.compress(BODY, mtime=0), "gzip"   # needs a request header
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        if enc: self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PYEOF
EPORT="$(python3 -c "
import socket
s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")"
python3 "$WORK/enc_stub.py" "$EPORT" >/dev/null 2>&1 &
ESTUB=$!
trap 'kill "$ESTUB" 2>/dev/null || true; cleanup' EXIT INT TERM
EBASE="http://127.0.0.1:$EPORT"
i=0
until curl -s -o /dev/null "$EBASE/gz" 2>/dev/null || [ "$i" -ge 50 ]; do
  i=$((i + 1)); python3 -c "import time; time.sleep(0.1)"
done
printf '{"id":"pet_1","name":"Rex"}' > "$WORK/pet.body"

run_enc() {  # run_enc <path>: smoke one encoded route; sets $erc and $eout
  printf 'GET\t%s\t200\tgzip\tpet.body\n' "$1" > "$R"
  erc=0
  eout="$(MOCKSTAR_SMOKE_BASE_URL="$EBASE" sh "$SMOKE" --routes-only "$R" 2>&1)" || erc=$?
}

run_enc /gz
if [ "$erc" -eq 0 ] && printf '%s\n' "$eout" | grep -q 'decoded body matches'; then
  ok "encoded route: header and decoded body match"
else
  bad "encoded route: header and decoded body match"
fi
run_enc /lie
if [ "$erc" -ne 0 ]; then ok "encoded route: gzip header over plain bytes fails"
else bad "encoded route: gzip header over plain bytes fails"; fi
run_enc /noenc
if [ "$erc" -ne 0 ] && printf '%s\n' "$eout" | grep -q "Content-Encoding 'none'"; then
  ok "encoded route: missing Content-Encoding header fails"
else
  bad "encoded route: missing Content-Encoding header fails"
fi
run_enc /wrongbody
if [ "$erc" -ne 0 ] && printf '%s\n' "$eout" | grep -q 'differs'; then
  ok "encoded route: decoded body that differs fails"
else
  bad "encoded route: decoded body that differs fails"
fi
printf 'GET\t/hdr\t200\tgzip\tpet.body\tX-Want-Encoding: gzip\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$EBASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  ok "encoded route: optional HEADER column is sent"
else
  bad "encoded route: optional HEADER column is sent"
fi
printf 'GET\t/hdr\t200\tgzip\tpet.body\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$EBASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  bad "encoded route: without the HEADER column the header-gated route fails"
else
  ok "encoded route: without the HEADER column the header-gated route fails"
fi
printf 'GET\t/gz\t200\tzstd\tpet.body\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$EBASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  bad "encoded route: unknown encoding name fails"
else
  ok "encoded route: unknown encoding name fails"
fi
printf 'GET\t/gz\t200\tgzip\tmissing.body\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$EBASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  bad "encoded route: missing expected-body file fails"
else
  ok "encoded route: missing expected-body file fails"
fi

exit $rc
