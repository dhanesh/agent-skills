#!/bin/sh
# scan-leaks.sh <skill-dir> [--denylist <file>]
#
# Scans every file under <skill-dir> for:
#   Detector 1 — Secrets/credentials (S1, hard-fail, no override):
#     - Curated regex patterns: AWS keys, GitHub tokens, Slack tokens,
#       Google API keys, Stripe live keys, PEM private key blocks, JWTs,
#       and generic password/secret/api_key/token assignments to long
#       literals (keyword match is CASE-INSENSITIVE so uppercase env keys
#       like PASSWORD=/SECRET=/TOKEN= are caught). # scan-leaks:ignore
#     - Hex-secret detector: a [0-9a-fA-F]{32,} value assigned to a key
#       (covers 32/40/64-char hex API keys). Standalone hex in prose
#       (git SHAs) and UUIDs are NOT flagged.
#     - Connection-string credentials: scheme://user:password@host URIs # scan-leaks:ignore
#       (postgres/mongodb/redis/amqp/...) with an embedded password.
#     - High-entropy heuristic: secret-charset tokens whose Shannon
#       entropy is high. The asset allowlist is scoped PER TOKEN (only the
#       data-URI / image|cert|font filename token is skipped) so a real
#       secret sharing a line with an asset is still caught. Each candidate
#       is evaluated both raw (base64 '=' padding preserved) and as the
#       assignment-stripped value, so padding can never gut the token.
#   Detector 2 — Source identifiers (S2):
#     - If --denylist <file> given, any identifier from that file found
#       in any skill file is a FAIL.
#
# Output discipline: file path, line number, and secret TYPE are printed;
# the actual secret value is REDACTED (first 4 chars + ***).
#
# Exits 0 only if no findings; exits non-zero if any finding.
#
# Opportunistic: if gitleaks is on PATH, it is also run; its findings
# are folded in. Never required — the shell detectors are the floor.
# gitleaks findings honour the same per-line "scan-leaks:ignore" annotation
# as the shell detectors, so a documented false positive is suppressed once,
# in-file, whichever detector raised it.
#
set -eu

# ── Usage ──────────────────────────────────────────────────────────────────────
if [ "$#" -lt 1 ]; then
    printf 'Usage: %s <skill-dir> [--denylist <file>]\n' "$0" >&2
    exit 1
fi

SKILL_DIR="$1"
shift

DENYLIST_FILE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --denylist)
            if [ "$#" -lt 2 ]; then
                printf 'ERROR: --denylist requires a file argument\n' >&2
                exit 1
            fi
            DENYLIST_FILE="$2"
            shift 2
            ;;
        *)
            printf 'ERROR: Unknown argument: %s\n' "$1" >&2
            exit 1
            ;;
    esac
done

if [ ! -d "$SKILL_DIR" ]; then
    printf 'ERROR: skill-dir not found: %s\n' "$SKILL_DIR" >&2
    exit 1
fi

if [ -n "$DENYLIST_FILE" ] && [ ! -f "$DENYLIST_FILE" ]; then
    printf 'ERROR: denylist file not found: %s\n' "$DENYLIST_FILE" >&2
    exit 1
fi

# A real tab for IFS field-splitting of detector output records.
TAB="$(printf '\t')"

# ── Detector 1: Curated credential patterns + hex + entropy ───────────────────
# One awk pass per file. Each finding is emitted as a TAB-separated record:
#     <lineno>\t<type>\t<redacted-value>
# Redaction (first 4 chars + ***) happens INSIDE awk so raw secrets never
# leave this function.
#
# Curated patterns:
#   aws-access-key      AKIA[0-9A-Z]{16}
#   github-token        gh[pousr]_[A-Za-z0-9]{36,}
#   slack-token         xox[baprs]-[A-Za-z0-9-]+
#   google-api-key      AIza[0-9A-Za-z_-]{35}
#   stripe-live-key     sk_live_[0-9A-Za-z]{24,} | pk_live_[0-9A-Za-z]{24,}
#   pem-private-key     -----BEGIN [A-Z ]*PRIVATE KEY-----
#   jwt-token           eyJ[..].eyJ[..].[..]
#   generic-credential  (case-insensitive keyword) [=:] <16+ secret-charset>
#   hex-secret          <key> [=:] [0-9a-fA-F]{32,}
#   high-entropy-secret secret-charset token, high Shannon entropy, gated

scan_secrets() {
    _file="$1"

    awk '
    function redact_val(v) {
        return substr(v, 1, 4) "***"
    }
    function emit(lnum, type, val) {
        # TAB-separated so colons inside values are never split downstream.
        printf "%d\t%s\t%s\n", lnum, type, redact_val(val)
    }
    function emit_safe(lnum, type, safeval) {
        # Emit a value that is ALREADY safe to display (no further redaction).
        # Used where the leading bytes of the matched span are themselves
        # secret (e.g. scheme://:password@) so first-4-chars redaction would # scan-leaks:ignore
        # leak part of the credential.
        printf "%d\t%s\t%s\n", lnum, type, safeval
    }
    function entropy(s,    n, j, c, h, p) {
        n = length(s)
        if (n == 0) return 0
        delete ecnt
        for (j = 1; j <= n; j++) { c = substr(s, j, 1); ecnt[c]++ }
        h = 0
        for (c in ecnt) { p = ecnt[c] / n; h -= p * log(p) / log(2) }
        return h
    }
    # Evaluate one candidate token: must be >=24 chars, entirely base64/hex
    # charset, not pure-hex (the hex detector owns those), and Shannon entropy
    # >= 4.3. Calibrated so random base64 secrets exceed it while normal
    # kebab/path/identifier tokens stay below ~4.1. Emits a finding if it trips.
    function check_entropy_token(cand, lnum,    h) {
        if (length(cand) < 24) return
        if (cand !~ /^[A-Za-z0-9+\/=_-]+$/) return
        if (cand ~ /^[0-9a-fA-F]+$/) return
        h = entropy(cand)
        if (h >= 4.3) emit(lnum, "high-entropy-secret", cand)
    }
    {
        line = $0
        lnum = NR
        lower = tolower(line)

        # ── scan-leaks:ignore annotation (line-scoped suppression) ──────────
        # A line containing the literal token "scan-leaks:ignore" is skipped by
        # ALL detectors. This is the per-line, in-file, auditable allowlist for
        # documented false positives (e.g. regex literals in tooling, example
        # commands in docs). It does NOT disable the gate globally — only the
        # annotated line is suppressed. Reviewed in Stage 5.
        if (index(line, "scan-leaks:ignore") > 0) { next }

        # ── Curated regex patterns ──────────────────────────────────────────
        rest = line
        while (match(rest, /AKIA[0-9A-Z]{16}/)) {
            emit(lnum, "aws-access-key", substr(rest, RSTART, RLENGTH))
            rest = substr(rest, RSTART + RLENGTH)
        }
        rest = line
        while (match(rest, /gh[pousr]_[A-Za-z0-9]{36,}/)) {
            emit(lnum, "github-token", substr(rest, RSTART, RLENGTH))
            rest = substr(rest, RSTART + RLENGTH)
        }
        rest = line
        while (match(rest, /xox[baprs]-[A-Za-z0-9][-A-Za-z0-9]*/)) {
            emit(lnum, "slack-token", substr(rest, RSTART, RLENGTH))
            rest = substr(rest, RSTART + RLENGTH)
        }
        # Google API key: AIza + exactly 35 chars (anchored to a non-charset
        # boundary so a longer run is not silently truncated to a match).
        rest = line
        while (match(rest, /AIza[0-9A-Za-z_-]{35}/)) {
            val = substr(rest, RSTART, RLENGTH)
            nextpos = RSTART + RLENGTH
            nextch = substr(rest, nextpos, 1)
            # Only accept when the 35-char run ends (next char is not part of
            # the key charset), i.e. the key is exactly 39 chars total.
            if (nextch !~ /[0-9A-Za-z_-]/) {
                emit(lnum, "google-api-key", val)
            }
            rest = substr(rest, nextpos)
        }
        rest = line
        while (match(rest, /sk_live_[0-9A-Za-z]{24,}/)) {
            emit(lnum, "stripe-live-key", substr(rest, RSTART, RLENGTH))
            rest = substr(rest, RSTART + RLENGTH)
        }
        rest = line
        while (match(rest, /pk_live_[0-9A-Za-z]{24,}/)) {
            emit(lnum, "stripe-live-key", substr(rest, RSTART, RLENGTH))
            rest = substr(rest, RSTART + RLENGTH)
        }
        if (match(line, /-----BEGIN [A-Z ]*PRIVATE KEY-----/)) {
            emit(lnum, "pem-private-key", substr(line, RSTART, RLENGTH))
        }
        rest = line
        while (match(rest, /eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/)) {
            emit(lnum, "jwt-token", substr(rest, RSTART, RLENGTH))
            rest = substr(rest, RSTART + RLENGTH)
        }

        # ── Generic credential assignment (CASE-INSENSITIVE keyword) ─────────
        # Match on a lowercased copy of the line to catch PASSWORD=/SECRET=/
        # TOKEN= etc., but extract the value from the ORIGINAL line at the
        # same offset so the redaction shows the real leading chars.
        # An OPTIONAL identifier suffix is allowed between the keyword and the
        # separator so compound keys match: SECRET_KEY=, CLIENT_SECRET=,
        # PRIVATE_KEY=, DB_PASSWORD=, API_KEY_PROD=, ACCESS_TOKEN=, etc.
        lrest = lower
        orest = line
        while (match(lrest, /(password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?key|client[_-]?secret|private[_-]?key|auth[_-]?token|token)[a-z0-9_-]*[[:space:]]*[=:][[:space:]]*"?'\''?[a-z0-9+\/=_-]{12,}/)) {
            mstart = RSTART
            mlen = RLENGTH
            # Pull the matched span from the ORIGINAL line (same offsets;
            # tolower preserves length and positions).
            orig = substr(orest, mstart, mlen)
            # Isolate the value: everything after the FIRST = or : separator.
            cred = orig
            if (match(cred, /[=:]/)) {
                cred = substr(cred, RSTART + 1)
            }
            # Strip leading whitespace and an opening quote.
            sub(/^[[:space:]]+/, "", cred)
            sub(/^["'\'']/, "", cred)
            # Documentation-prose guard (false-positive suppression): if the
            # isolated value is a single all-lowercase dictionary-style word
            # (e.g. prose like "any secret = unconditional fail"), it is NOT a
            # plausible credential. Real secrets carry at least one digit,
            # uppercase letter, or symbol (-, _, +, /, =). Skip emitting when
            # cred matches ^[a-z]+$ so secret-handling PROSE in a generated
            # skill SKILL.md cannot spuriously hard-fail Gate 2. Curated
            # detectors (AWS/JWT/hex/entropy/connstring) are unaffected; only
            # the heuristic generic-credential branch applies this guard.
            if (cred ~ /^[a-z]+$/) {
                lrest = substr(lrest, mstart + mlen)
                orest = substr(orest, mstart + mlen)
                continue
            }
            emit(lnum, "generic-credential", cred)
            lrest = substr(lrest, mstart + mlen)
            orest = substr(orest, mstart + mlen)
        }

        # ── Connection-string credentials (scheme://user:password@host) ────── # scan-leaks:ignore
        # postgres://u:p@h, mongodb://u:p@h, redis://:p@h, amqp://u:p@h, etc. # scan-leaks:ignore
        # The userinfo segment between "://" and "@" containing a ":" carries
        # an embedded credential. Userinfo chars exclude "/", whitespace, ":",
        # "@" except for the single ":" separating user and password.
        rest = line
        while (match(rest, /[A-Za-z][A-Za-z0-9+.-]*:\/\/[^[:space:]\/:@]*:[^[:space:]\/@]+@/)) {
            # Capture the scheme that precedes "://" and emit only
            # "<scheme>://***" so NO byte of the userinfo (which may begin with
            # the password, as in redis://:pass@) is ever printed. # scan-leaks:ignore
            mspan = substr(rest, RSTART, RLENGTH)
            scheme = mspan
            sub(/:\/\/.*$/, "", scheme)
            emit_safe(lnum, "connection-string-credential", scheme "://***")
            rest = substr(rest, RSTART + RLENGTH)
        }

        # ── Hex-secret detector ──────────────────────────────────────────────
        # A pure-hex value of length >= 32 assigned to a key. Standalone hex
        # tokens (git SHAs in prose) are NOT flagged — assignment context is
        # required. UUIDs contain hyphens and never match a contiguous hex run
        # of >=32, so they are excluded naturally.
        hrest = line
        while (match(hrest, /[=:][[:space:]]*"?'\''?[0-9a-fA-F]{32,}/)) {
            hval = substr(hrest, RSTART, RLENGTH)
            sub(/^[=:][[:space:]]*["'\'']?/, "", hval)
            # Guard: ensure the hex run is not immediately preceded/followed by
            # more hex on the original (already handled — match starts at = / :).
            emit(lnum, "hex-secret", hval)
            hrest = substr(hrest, RSTART + RLENGTH)
        }

        # ── High-entropy heuristic (PER-TOKEN asset allowlist, context-gated) ─
        # Tokenize on whitespace and quotes/backticks ONLY (NOT = or :), so a
        # base64 token containing internal padding is not fragmented.
        # The asset allowlist is scoped to the SPECIFIC asset/data-URI token,
        # NOT the whole line — a real secret sharing a line with logo.png or a
        # data-URI is still evaluated. Each candidate token is checked BOTH as
        # the raw token (only surrounding quotes/brackets/whitespace trimmed,
        # so trailing base64 padding is preserved) AND as the
        # assignment-stripped value side, flagging if EITHER is high-entropy.
        n = split(line, toks, /[ \t"'\''`]/)
        for (i = 1; i <= n; i++) {
            t = toks[i]
            # Strip surrounding punctuation/brackets/quotes. The equals sign is
            # NOT stripped (valid base64 padding); underscore/hyphen/plus are
            # charset members and are likewise preserved.
            gsub(/^[][(){}<>,;"'\''`]+/, "", t)
            gsub(/[][(){}<>,;"'\''`]+$/, "", t)
            if (t == "") continue
            # PER-TOKEN allowlist: skip only this token if it is itself an
            # embedded asset (data-URI or a filename with an image/font/cert
            # extension). Other tokens on the line are still scanned.
            tl = tolower(t)
            if (tl ~ /^data:[a-z0-9\/.+-]*;base64,/) continue
            if (tl ~ /\.(png|jpe?g|gif|webp|bmp|ico|svg|woff2?|ttf|eot|crt|cer|der|p12|pfx)$/) continue
            # 1) Evaluate the RAW token (padding intact).
            check_entropy_token(t, lnum)
            # 2) Evaluate the assignment-stripped value side, if any. This is a
            #    SECOND chance, not a replacement — never let an assignment
            #    prefix (or base64 padding mistaken for one) hide a secret.
            v = t
            if (match(v, /^[A-Za-z0-9_.-]+[=:]/)) {
                v = substr(v, RLENGTH + 1)
                gsub(/^["'\'']/, "", v)
                gsub(/["'\'']$/, "", v)
                if (v != t) check_entropy_token(v, lnum)
            }
        }
    }
    ' "$_file"
}

# ── Detector 2: Denylist identifiers ──────────────────────────────────────────
# Emits TAB-separated records: <lineno>\t<identifier>
scan_denylist() {
    _file="$1"
    _denylist="$2"

    while IFS= read -r identifier || [ -n "$identifier" ]; do
        case "$identifier" in
            ''|\#*) continue ;;
        esac
        # -n prefixes "lineno:"; capture lineno without colon-splitting the
        # rest of the line (which may itself contain colons).
        grep -n -F -- "$identifier" "$_file" 2>/dev/null | while IFS= read -r hit; do
            lnum="${hit%%:*}"
            # Suppress lines carrying the scan-leaks:ignore annotation.
            linecontent="${hit#*:}"
            case "$linecontent" in
                *"scan-leaks:ignore"*) continue ;;
            esac
            printf '%s\t%s\n' "$lnum" "$identifier"
        done
    done < "$_denylist"
}

# ── Process all files ──────────────────────────────────────────────────────────
TMPFILE="$(mktemp)"

find "$SKILL_DIR" -type f | sort | while IFS= read -r filepath; do
    scan_secrets "$filepath" | while IFS="$TAB" read -r lnum type redacted; do
        [ -n "$lnum" ] || continue
        printf '%s\t%s\t%s\t%s\n' "$filepath" "$lnum" "$type" "$redacted"
    done

    if [ -n "$DENYLIST_FILE" ]; then
        scan_denylist "$filepath" "$DENYLIST_FILE" | while IFS="$TAB" read -r lnum identifier; do
            [ -n "$lnum" ] || continue
            printf '%s\t%s\tdenylist-identifier\t%s\n' "$filepath" "$lnum" "$identifier"
        done
    fi
done > "$TMPFILE"

# ── Opportunistic: gitleaks ────────────────────────────────────────────────────
# gitleaks (v8) scans a directory via `gitleaks dir`. We parse its pretty-printed
# JSON report by KEY (not by positional ':' split — file paths and secrets
# contain ':'). Fields are buffered per finding object and flushed at the
# object's closing context. Absence of gitleaks is fine; the shell detectors
# above are the floor.
if command -v gitleaks > /dev/null 2>&1; then
    GITLEAKS_TMP="$(mktemp)"
    # The `dir` subcommand scans a filesystem path (no git); --no-git is NOT a
    # valid flag for it in gitleaks v8. --exit-code 0 keeps gitleaks from
    # failing the pipeline (we read its JSON report instead of its exit code).
    gitleaks dir "$SKILL_DIR" --no-banner --exit-code 0 \
        --report-format json --report-path "$GITLEAKS_TMP" > /dev/null 2>&1 || true

    if [ -s "$GITLEAKS_TMP" ]; then
        awk -v TAB="$TAB" '
        function jval(s,    v) {
            v = s
            sub(/^[^:]*:[[:space:]]*/, "", v)   # drop the "key": prefix
            sub(/,[[:space:]]*$/, "", v)         # drop a trailing comma
            sub(/^"/, "", v)                     # drop opening quote
            sub(/"$/, "", v)                     # drop closing quote
            return v
        }
        function flush() {
            if (have) {
                printf "%s%s%s%sgitleaks-%s%s%s***\n", \
                    file, TAB, lnum, TAB, rule, TAB, substr(sec, 1, 4)
            }
            rule = ""; lnum = ""; file = ""; sec = ""; have = 0
        }
        # A new object starts at a line that is just "{" (within the array).
        /^[[:space:]]*\{[[:space:]]*$/ { flush() }
        /^[[:space:]]*"RuleID":/    { rule = jval($0) }
        /^[[:space:]]*"StartLine":/ { lnum = jval($0) }
        /^[[:space:]]*"File":/      { file = jval($0) }
        /^[[:space:]]*"Secret":/    { sec = jval($0); have = 1 }
        END { flush() }
        ' "$GITLEAKS_TMP" | while IFS="$TAB" read -r gfile glnum gtype gredacted; do
            [ -n "$gtype" ] || continue
            # Honour the SAME per-line suppression the shell detectors use. Without
            # this, gitleaks findings were the one class the documented
            # "scan-leaks:ignore" annotation could not silence — a documented false
            # positive had no in-file remedy at all. Fail-closed: if the source line
            # cannot be re-read (path not resolvable from the current directory), the
            # finding is KEPT rather than dropped.
            if [ -n "$gfile" ] && [ -n "$glnum" ] && [ -f "$gfile" ]; then
                gline="$(sed -n "${glnum}p" "$gfile" 2>/dev/null || true)"
                case "$gline" in
                    *"scan-leaks:ignore"*) continue ;;
                esac
            fi
            printf '%s\t%s\t%s\t%s\n' "$gfile" "$glnum" "$gtype" "$gredacted"
        done >> "$TMPFILE"
    fi
    rm -f "$GITLEAKS_TMP"
fi

# ── Emit findings and summary ──────────────────────────────────────────────────
finding_count=0

if [ -s "$TMPFILE" ]; then
    while IFS="$TAB" read -r filepath lnum type redacted; do
        [ -n "$type" ] || continue
        printf 'FINDING: %s:%s: [%s] %s\n' "$filepath" "$lnum" "$type" "$redacted"
        finding_count=$((finding_count + 1))
    done < "$TMPFILE"
fi

rm -f "$TMPFILE"

if [ "$finding_count" -eq 0 ]; then
    printf 'SCAN_RESULT: PASS — no secrets or denylist violations found in %s\n' "$SKILL_DIR"
    exit 0
else
    printf 'SCAN_RESULT: FAIL — %d finding(s) in %s\n' "$finding_count" "$SKILL_DIR"
    exit 1
fi
