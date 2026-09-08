#!/bin/sh
# validate-skill.sh <skill-dir> [--spec <agent-skills-spec.md>]
#
# Validates a generated skill against the Agent Skills standard.
# Checks:
#   1. SKILL.md exists
#   1b. README.md exists (human-facing, always authored by repo2skill)
#   2. Frontmatter present (opening and closing ---)
#   3. `name` field: present, kebab-case, length <= 64
#   4. `description` field: present, non-empty, length <= 1024
#   5. Dangling references: referenced paths in SKILL.md body must exist
#   6. Placeholder bijection (RT-4): template placeholders <-> PARAMETERS.md
#   7. Spec-version drift (RT-5): if --spec given and skill declares version
#
# Prints PASS:/FAIL: per check, INFO:/SKIP: for advisory lines.
# Prints VALIDATION_RESULT: PASS or VALIDATION_RESULT: FAIL at end.
# Exits non-zero if ANY hard check fails.
set -eu

# ── Locale: ensure character-aware counting ──────────────────────────────────
# The name/description length checks use `wc -m`, which only counts CHARACTERS
# (not bytes) when LC_CTYPE is a UTF-8 locale. Under LC_ALL=C/POSIX (common in
# Alpine/minimal containers and many CI runners) wc -m regresses to byte
# counting, over-counting multibyte descriptions and falsely failing the limits.
# Probe for any available UTF-8 locale and pin it (macOS lacks C.UTF-8, so we
# must probe `locale -a` rather than hardcode a name). If wc -m already counts
# characters, leave the ambient locale alone. If no UTF-8 locale exists at all,
# degrade gracefully to byte counting (last-resort, no crash).
if [ "${LC_ALL:-}" = "C" ] || [ "${LC_ALL:-}" = "POSIX" ] || ! printf 'é' | wc -m | grep -q '^[[:space:]]*1'; then
    _utf8="$(locale -a 2>/dev/null | grep -iE '\.(utf-?8)$' | head -1)"
    [ -n "$_utf8" ] && export LC_ALL="$_utf8"
fi

# ── Usage ──────────────────────────────────────────────────────────────────────
if [ "$#" -lt 1 ]; then
    printf 'Usage: %s <skill-dir> [--spec <agent-skills-spec.md>]\n' "$0" >&2
    exit 1
fi

SKILL_DIR="$1"
shift

SPEC_FILE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --spec)
            if [ "$#" -lt 2 ]; then
                printf 'ERROR: --spec requires a file argument\n' >&2
                exit 1
            fi
            SPEC_FILE="$2"
            shift 2
            ;;
        *)
            printf 'ERROR: Unknown argument: %s\n' "$1" >&2
            exit 1
            ;;
    esac
done

# ── State tracking ─────────────────────────────────────────────────────────────
hard_failures=0

check_pass() {
    printf 'PASS: %s\n' "$1"
}

check_fail() {
    printf 'FAIL: %s\n' "$1"
    hard_failures=$((hard_failures + 1))
}

check_info() {
    printf 'INFO: %s\n' "$1"
}

check_skip() {
    printf 'SKIP: %s\n' "$1"
}

# ── Check 1: SKILL.md exists ───────────────────────────────────────────────────
SKILL_MD="$SKILL_DIR/SKILL.md"

if [ ! -f "$SKILL_MD" ]; then
    printf 'FAIL: SKILL.md not found in %s\n' "$SKILL_DIR"
    printf 'VALIDATION_RESULT: FAIL\n'
    exit 1
fi

check_pass "SKILL.md exists"

# ── Check 1b: README.md exists (HARD GATE) ────────────────────────────────────
# repo2skill always authors a human-facing README so the skill is legible when
# browsing the repo. Missing README is a hard failure, but non-fatal to the run
# so the remaining checks still report.
if [ -f "$SKILL_DIR/README.md" ]; then
    check_pass "README.md exists"
else
    check_fail "README.md not found in $SKILL_DIR (repo2skill always authors a human-facing README)"
fi

# ── Check 2: Frontmatter present ──────────────────────────────────────────────
# Line 1 must be exactly "---" and there must be a second "---" later.

first_line="$(head -1 "$SKILL_MD")"
if [ "$first_line" != "---" ]; then
    check_fail "frontmatter: SKILL.md does not start with ---"
    printf 'VALIDATION_RESULT: FAIL\n'
    exit 1
fi

# Count occurrences of --- lines; need at least 2
fm_close_count="$(awk '/^---$/ { count++ } END { print count+0 }' "$SKILL_MD")"
if [ "$fm_close_count" -lt 2 ]; then
    check_fail "frontmatter: no closing --- found in SKILL.md"
    printf 'VALIDATION_RESULT: FAIL\n'
    exit 1
fi

check_pass "frontmatter: present (opening and closing ---)"

# Extract the frontmatter block (lines between first and second ---)
FRONTMATTER="$(awk '
    /^---$/ { count++; next }
    count == 1 { print }
    count >= 2 { exit }
' "$SKILL_MD")"

# ── Check 3: `name` field ─────────────────────────────────────────────────────
# Extract name value from frontmatter
name_value="$(printf '%s\n' "$FRONTMATTER" | awk '
/^name:/ {
    # strip "name:" prefix, then leading/trailing whitespace and optional quotes
    val = $0
    sub(/^name:[ \t]*/, "", val)
    gsub(/^["'"'"']|["'"'"']$/, "", val)
    gsub(/^[ \t]+|[ \t]+$/, "", val)
    print val
    exit
}
')"

if [ -z "$name_value" ]; then
    check_fail "name: field not found in frontmatter"
else
    # Check kebab-case pattern: ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$
    # Allow single char names (just one alnum char, no hyphen needed)
    name_ok=1

    # Must start with lowercase letter or digit
    name_first="$(printf '%s' "$name_value" | cut -c1)"
    case "$name_first" in
        [a-z0-9]) ;;
        *) name_ok=0 ;;
    esac

    # Must end with lowercase letter or digit (if length > 1)
    # Count CHARACTERS not bytes. wc -m counts characters per the locale's
    # encoding (BSD/macOS awk length() counts bytes, so wc -m is the portable
    # choice for the spec's character limits).
    name_len="$(printf '%s' "$name_value" | wc -m | tr -d ' ')"
    if [ "$name_len" -gt 1 ]; then
        name_last="$(printf '%s' "$name_value" | awk '{print substr($0,length($0),1)}')"
        case "$name_last" in
            [a-z0-9]) ;;
            *) name_ok=0 ;;
        esac
    fi

    # Must contain only [a-z0-9-]
    invalid_chars="$(printf '%s' "$name_value" | tr -d 'a-z0-9-')"
    if [ -n "$invalid_chars" ]; then
        name_ok=0
    fi

    # Length check
    if [ "$name_len" -gt 64 ]; then
        check_fail "name: exceeds 64 characters (length=$name_len)"
        name_ok=0
    fi

    if [ "$name_ok" -eq 1 ]; then
        check_pass "name: valid kebab-case '$name_value'"
    else
        # Only emit the format FAIL if we haven't already emitted a length FAIL
        if [ "$name_len" -le 64 ]; then
            check_fail "name: invalid format '$name_value' (must be ^[a-z0-9]([a-z0-9-]*[a-z0-9])?\$)"
        fi
    fi
fi

# ── Check 4: `description` field ──────────────────────────────────────────────
# Extract the description value, INCLUDING a YAML block scalar (`>-`, `>`, `|`,
# `|-`, ...). Reading only the first line here was a silent bypass: for a folded
# scalar that line is the indicator itself, which is non-empty, so the emptiness
# guard never fired and the length check measured 2 characters. 11 of 18 skills
# were unmeasured that way and one shipped 163 characters over the platform
# limit with the gate reporting PASS. Folded blocks join with a space, literal
# blocks keep their newlines — either way every character is counted.
desc_value="$(printf '%s\n' "$FRONTMATTER" | awk '
function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
BEGIN { st = 0; out = ""; sq = sprintf("%c", 39) }
st == 0 && /^description:/ {
    val = $0
    sub(/^description:[ \t]*/, "", val)
    val = trim(val)
    if (val ~ /^[|>]/) { st = 1; blk = 1; mode = substr(val, 1, 1); next }
    if (val ~ /^".*"$/) { val = substr(val, 2, length(val) - 2) }
    else if (val ~ "^" sq ".*" sq "$") { val = substr(val, 2, length(val) - 2) }
    print val
    st = 2
    exit
}
st == 1 {
    if ($0 ~ /^[ \t]*$/) { if (out != "") out = out "\n"; next }
    if ($0 !~ /^[ \t]/) { st = 2; exit }          # dedent ends the block
    line = $0
    sub(/^[ \t]+/, "", line)
    if (out == "") out = line
    else out = out (mode == "|" ? "\n" : " ") line
}
END { if (blk && out != "") print out }
')"

desc_field_present="$(printf '%s\n' "$FRONTMATTER" | grep -c '^description:' || true)"

if [ "$desc_field_present" -eq 0 ]; then
    check_fail "description: field not found in frontmatter"
elif [ -z "$desc_value" ]; then
    check_fail "description: field is empty (must be non-empty after trimming)"
else
    # Count CHARACTERS not bytes so multibyte/unicode descriptions are measured
    # against the spec's character limit, not byte length. wc -m counts
    # characters per the locale encoding (BSD awk length() counts bytes).
    desc_len="$(printf '%s' "$desc_value" | wc -m | tr -d ' ')"
    if [ "$desc_len" -gt 1024 ]; then
        check_fail "description: exceeds 1024 characters (length=$desc_len)"
    else
        check_pass "description: present and valid (length=$desc_len)"
    fi
fi

# ── Check 5: Dangling references ──────────────────────────────────────────────
# Extract body of SKILL.md (after the frontmatter closing ---)
SKILL_BODY="$(awk '
    /^---$/ { count++; next }
    count >= 2 { print }
' "$SKILL_MD")"

# Find referenced bundled paths matching references/..., assets/..., or scripts/...
# A bundled ref is a LOCAL relative path. We extract two ways:
#   (a) backtick/quote-wrapped tokens whose VALUE starts with the bundled prefix
#       (a wrapped value is the whole token, so `references/x` is local, but a
#        wrapped URL `https://h/assets/x` does not start with the prefix).
#   (b) plain inline occurrences, but ONLY when the prefix is at a word boundary
#       and NOT preceded by '/' or ':' — this excludes URL path segments such as
#       https://example.com/assets/logo.png (the 'assets/' there follows '/').
REFS_TMP="$(mktemp)"
# (a) wrapped tokens — strip the surrounding `, ', or " then keep local-prefixed
printf '%s\n' "$SKILL_BODY" | grep -o '[`'"'"'"][^`'"'"'"]*[`'"'"'"]' \
    | tr -d '`'"'"'"' | grep -E '^(references|assets|scripts)/' >> "$REFS_TMP" 2>/dev/null || true
# (b) plain inline occurrences not preceded by '/' or ':' (URL-safe). The
#     negative-context is handled by requiring a preceding non-[/:] char or
#     start-of-field via word boundary: match (^|[^/:])prefix/... and capture.
printf '%s\n' "$SKILL_BODY" \
    | grep -oE '(^|[^/:[:alnum:]])(references|assets|scripts)/[A-Za-z0-9_./-]+' 2>/dev/null \
    | grep -oE '(references|assets|scripts)/[A-Za-z0-9_./-]+' >> "$REFS_TMP" 2>/dev/null || true

# Deduplicate
REFS_UNIQUE="$(sort -u "$REFS_TMP")"
rm -f "$REFS_TMP"

dangling_found=0
if [ -n "$REFS_UNIQUE" ]; then
    while IFS= read -r ref_path; do
        [ -n "$ref_path" ] || continue
        full_path="$SKILL_DIR/$ref_path"
        if [ ! -e "$full_path" ]; then
            check_fail "dangling reference: '$ref_path' referenced in SKILL.md does not exist"
            dangling_found=1
        fi
    done << EOF
$REFS_UNIQUE
EOF
fi

if [ "$dangling_found" -eq 0 ]; then
    check_pass "dangling references: none found"
fi

# ── Check 6: Placeholder bijection (RT-4) ─────────────────────────────────────
TEMPLATES_DIR="$SKILL_DIR/assets/templates"
PARAMS_MD="$SKILL_DIR/PARAMETERS.md"

has_templates=0
has_params=0

[ -d "$TEMPLATES_DIR" ] && has_templates=1
[ -f "$PARAMS_MD" ] && has_params=1

if [ "$has_templates" -eq 0 ] && [ "$has_params" -eq 0 ]; then
    check_skip "bijection: no assets/templates/ and no PARAMETERS.md — check skipped (skill has no code templates)"
elif [ "$has_templates" -eq 1 ] && [ "$has_params" -eq 0 ]; then
    # Before failing: check whether assets/templates/ contains ONLY *.tmpl files.
    # *.tmpl files are uninstantiated generator scaffolding (e.g. SKILL.md.tmpl).
    # They belong to the generator itself, not to a generated skill, so their
    # placeholders do NOT require a PARAMETERS.md in this skill.  Concrete
    # template files (any non-*.tmpl file) DO require full bijection coverage.
    non_tmpl_count="$(find "$TEMPLATES_DIR" -type f | grep -v '\.tmpl$' | wc -l | tr -d ' ')"
    if [ "$non_tmpl_count" -eq 0 ]; then
        check_skip "bijection: assets/templates/ contains only *.tmpl scaffolding — bijection check skipped (no PARAMETERS.md required)"
    else
        check_fail "bijection: assets/templates/ exists but PARAMETERS.md is missing"
    fi
elif [ "$has_templates" -eq 0 ] && [ "$has_params" -eq 1 ]; then
    check_fail "bijection: PARAMETERS.md exists but assets/templates/ directory is missing"
else
    # Collect all distinct {{UPPER_SNAKE}} tokens from CONCRETE template files only.
    # *.tmpl files are generator scaffolding (uninstantiated); they are NOT this
    # skill's parameterized output and must NOT contribute to the bijection check.
    TMPL_TOKENS_FILE="$(mktemp)"
    find "$TEMPLATES_DIR" -type f | grep -v '\.tmpl$' | while IFS= read -r f; do
        grep -o '{{[A-Z0-9_]*}}' "$f" 2>/dev/null || true
    done | sort -u > "$TMPL_TOKENS_FILE"

    # Collect declared placeholders from PARAMETERS.md (first column)
    PARAMS_TOKENS_FILE="$(mktemp)"
    awk '
    BEGIN { FS="|" }
    /^\|[-:| ]+\|$/ { next }                  # separator row (also matches :---: alignment rows)
    {
        col1 = $2
        gsub(/^[ \t]+|[ \t]+$/, "", col1)
        # Skip ONLY the header row: first cell is exactly the literal header
        # label (optionally backtick-wrapped). Anchored so a documented param
        # like {{PLACEHOLDER_X}} is NOT mistaken for the header and dropped.
        if (col1 ~ /^`?Placeholder`?$/) { next }
        gsub(/`/, "", col1)
        if (col1 ~ /^\{\{[A-Z0-9_]+\}\}$/) {
            print col1
        }
    }
    ' "$PARAMS_MD" | sort -u > "$PARAMS_TOKENS_FILE"

    bijection_ok=1

    # Check for undocumented: in templates but not in PARAMETERS.md
    UNDOC_FILE="$(mktemp)"
    while IFS= read -r token; do
        [ -n "$token" ] || continue
        if ! grep -qF "$token" "$PARAMS_TOKENS_FILE" 2>/dev/null; then
            printf '%s\n' "$token" >> "$UNDOC_FILE"
        fi
    done < "$TMPL_TOKENS_FILE"

    if [ -s "$UNDOC_FILE" ]; then
        while IFS= read -r token; do
            check_fail "bijection: undocumented placeholder $token in templates but not in PARAMETERS.md"
        done < "$UNDOC_FILE"
        bijection_ok=0
    fi
    rm -f "$UNDOC_FILE"

    # Check for orphans: in PARAMETERS.md but not in any template
    ORPHAN_FILE="$(mktemp)"
    while IFS= read -r token; do
        [ -n "$token" ] || continue
        if ! grep -qF "$token" "$TMPL_TOKENS_FILE" 2>/dev/null; then
            printf '%s\n' "$token" >> "$ORPHAN_FILE"
        fi
    done < "$PARAMS_TOKENS_FILE"

    if [ -s "$ORPHAN_FILE" ]; then
        while IFS= read -r token; do
            check_fail "bijection: orphan placeholder $token in PARAMETERS.md but not in any template"
        done < "$ORPHAN_FILE"
        bijection_ok=0
    fi
    rm -f "$ORPHAN_FILE"

    rm -f "$TMPL_TOKENS_FILE" "$PARAMS_TOKENS_FILE"

    if [ "$bijection_ok" -eq 1 ]; then
        check_pass "bijection: all template placeholders documented; no orphan parameters"
    fi
fi

# ── Check 7: Spec-version drift (RT-5) ────────────────────────────────────────
if [ -z "$SPEC_FILE" ]; then
    check_skip "spec-version: no --spec file provided — drift check skipped"
else
    if [ ! -f "$SPEC_FILE" ]; then
        check_fail "spec-version: --spec file not found: $SPEC_FILE"
    else
        # Read spec file's declared version: line matching "spec_version: <N>"
        spec_ver="$(grep '^spec_version:' "$SPEC_FILE" | head -1 | sed 's/^spec_version:[ \t]*//' | tr -d ' \t')"

        if [ -z "$spec_ver" ]; then
            check_info "spec-version: spec file declares no spec_version — drift check skipped"
        else
            # Read skill's declared spec version from frontmatter:
            # Look for x-spec-version: <N> or metadata: containing spec_version: <N>
            skill_ver="$(printf '%s\n' "$FRONTMATTER" | grep '^x-spec-version:' | head -1 | sed 's/^x-spec-version:[ \t]*//' | tr -d ' \t')"

            if [ -z "$skill_ver" ]; then
                # Try metadata: block style — a (possibly indented) spec_version
                # YAML key WITHIN the frontmatter scope only.
                skill_ver="$(printf '%s\n' "$FRONTMATTER" | grep -E '^[ \t]*spec_version:' | head -1 | sed 's/^[ \t]*spec_version:[ \t]*//' | tr -d ' \t')"
            fi

            if [ -z "$skill_ver" ]; then
                check_info "spec-version: skill declares no spec version — drift check skipped (spec is $spec_ver)"
            elif [ "$skill_ver" = "$spec_ver" ]; then
                check_pass "spec-version: skill ($skill_ver) matches spec ($spec_ver)"
            else
                check_fail "spec-version: skill declares $skill_ver but spec is $spec_ver — drift detected"
            fi
        fi
    fi
fi

# ── Summary ────────────────────────────────────────────────────────────────────
if [ "$hard_failures" -eq 0 ]; then
    printf 'VALIDATION_RESULT: PASS\n'
    exit 0
else
    printf 'VALIDATION_RESULT: FAIL (%d hard failure(s))\n' "$hard_failures"
    exit 1
fi
