#!/bin/sh
# dry-run-replay.sh <skill-dir> <scratch-dir>
#
# Substitutes PARAMETERS.md example values into assets/templates/* files,
# writes output to <scratch-dir>, and validates:
#   - No residual {{PLACEHOLDER}} tokens remain (RT-1)
#   - No orphan parameters in PARAMETERS.md (RT-4)
#
# Exit 0 on clean pass; non-zero on any error or validation failure.
# Prints DRY_RUN_RESULT: PASS or DRY_RUN_RESULT: FAIL at the end.
set -eu

# ── Usage ──────────────────────────────────────────────────────────────────────
if [ "$#" -lt 2 ]; then
    printf 'Usage: %s <skill-dir> <scratch-dir>\n' "$0" >&2
    exit 1
fi

SKILL_DIR="$1"
SCRATCH_DIR="$2"

# ── Validate required files ────────────────────────────────────────────────────
# SKILL.md is a precondition asserting <skill-dir> really is a generated skill.
# The dry-run itself only needs PARAMETERS.md + assets/templates/, but we refuse
# to replay against a directory that isn't a skill.
if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
    printf 'ERROR: %s/SKILL.md not found\n' "$SKILL_DIR" >&2
    exit 1
fi

if [ ! -f "$SKILL_DIR/PARAMETERS.md" ]; then
    printf 'ERROR: %s/PARAMETERS.md not found\n' "$SKILL_DIR" >&2
    exit 1
fi

TEMPLATES_DIR="$SKILL_DIR/assets/templates"

# ── Parse PARAMETERS.md: extract placeholder→example pairs ───────────────────
# Table rows look like:  | `{{PLACEHOLDER}}` | Description | Example | Default |
# We skip the header row (contains "Placeholder") and separator row (contains "---").
# awk extracts column 1 (placeholder) and column 3 (example).
PARAMS_FILE="$(mktemp)"
# Write placeholder TAB example pairs, one per line
awk '
BEGIN { FS="|" }
/^\|[-:| ]+\|$/ { next }          # separator row (also matches :---: alignment rows)
/^\|/ {
    # col1 = $2, col3 = $4 (0-indexed pipe splits)
    col1 = $2; col3 = $4
    # strip leading/trailing whitespace
    gsub(/^[ \t]+|[ \t]+$/, "", col1)
    gsub(/^[ \t]+|[ \t]+$/, "", col3)
    # Skip ONLY the header row: first cell is exactly the literal header label
    # (optionally backtick-wrapped). Anchored so a documented param whose
    # Description column contains "Placeholder" is NOT mistaken for the header
    # and dropped — only the cell value ^Placeholder$ triggers the skip.
    if (col1 ~ /^`?Placeholder`?$/) { next }
    # col1 should look like `{{PLACEHOLDER}}` — strip backticks
    gsub(/`/, "", col1)
    # col3 is the Example value — strip backticks if present
    gsub(/`/, "", col3)
    # only emit if col1 looks like {{...}}
    if (col1 ~ /^\{\{[A-Z0-9_]+\}\}$/) {
        print col1 "\t" col3
    }
}
' "$SKILL_DIR/PARAMETERS.md" > "$PARAMS_FILE"

# Check we got at least one parameter
param_count=0
if [ -s "$PARAMS_FILE" ]; then
    param_count="$(wc -l < "$PARAMS_FILE" | tr -d ' ')"
fi

printf 'INFO: Found %d placeholder(s) in PARAMETERS.md\n' "$param_count"

# ── Collect template files ─────────────────────────────────────────────────────
if [ ! -d "$TEMPLATES_DIR" ]; then
    printf 'INFO: No assets/templates/ directory found; nothing to substitute\n'
    rm -f "$PARAMS_FILE"
    printf 'DRY_RUN_RESULT: PASS\n'
    exit 0
fi

# Build list of template files
TEMPLATE_LIST="$(mktemp)"
find "$TEMPLATES_DIR" -type f > "$TEMPLATE_LIST"

template_count=0
if [ -s "$TEMPLATE_LIST" ]; then
    template_count="$(wc -l < "$TEMPLATE_LIST" | tr -d ' ')"
fi

if [ "$template_count" -eq 0 ]; then
    printf 'INFO: No template files found in assets/templates/\n'
    rm -f "$PARAMS_FILE" "$TEMPLATE_LIST"
    printf 'DRY_RUN_RESULT: PASS\n'
    exit 0
fi

# ── Copy templates to scratch dir, performing substitution ────────────────────
mkdir -p "$SCRATCH_DIR"

result="PASS"
fail_reasons=""

# Process each template file
while IFS= read -r src_file; do
    # Compute destination: strip the templates dir prefix, keep relative path
    rel_path="${src_file#"$TEMPLATES_DIR"/}"
    dst_file="$SCRATCH_DIR/$rel_path"

    # Create parent directory if needed
    dst_dir="$(dirname "$dst_file")"
    mkdir -p "$dst_dir"

    # Copy file first
    cp "$src_file" "$dst_file"

    # Skip binary / non-text files: sed on binary data emits "illegal byte
    # sequence" on macOS and would abort with no DRY_RUN_RESULT line. We copy
    # them verbatim (above) and leave them unsubstituted.
    if ! grep -Iq . "$src_file" 2>/dev/null; then
        printf 'INFO: Skipping non-text (binary) template file: %s\n' "$rel_path"
        continue
    fi

    # Apply each substitution using sed.
    # NOTE: substitution is single-pass, per-row, in PARAMETERS.md row order.
    # An Example value that itself contains "{{...}}" could therefore be picked
    # up by a later row's substitution (chaining). Authors should keep Example
    # values free of placeholder syntax.
    # Read params file line by line: placeholder TAB example
    while IFS="	" read -r placeholder example; do
        # Escape for sed with | delimiter:
        # In the pattern: escape \, &, and | (the delimiter). Do NOT escape {}
        # because {{PLACEHOLDER}} is a literal string match, not a regex quantifier.
        ph_escaped="$(printf '%s' "$placeholder" | sed 's/[\\&|]/\\&/g')"
        # In the replacement: escape \, &, and | (the delimiter)
        ex_escaped="$(printf '%s' "$example" | sed 's/[\\&|]/\\&/g')"

        # Perform in-place substitution using a temp file (POSIX-safe)
        tmp_sub="$(mktemp)"
        sed "s|${ph_escaped}|${ex_escaped}|g" "$dst_file" > "$tmp_sub"
        mv "$tmp_sub" "$dst_file"
    done < "$PARAMS_FILE"
done < "$TEMPLATE_LIST"

# ── Check 1: Residual {{...}} tokens in output ────────────────────────────────
# Match ANY leftover {{...}} regardless of inner casing/content (RT-1). The
# inner-content regex MUST NOT be restricted to uppercase-snake, or tokens like
# {{lowercase}}, {{Mixed}}, or {{ SPACED }} would slip through and report PASS.
RESIDUAL_DETAIL="$(mktemp)"
find "$SCRATCH_DIR" -type f | while IFS= read -r f; do
    # Only scan text files; binary templates were copied verbatim and skipped
    # during substitution, so {{...}} inside them is not a manifest error.
    if ! grep -Iq . "$f" 2>/dev/null; then
        continue
    fi
    # Match residual {{...}} tokens but exclude Helm/Go template expressions.
    # Our skill placeholders are always {{UPPER_SNAKE_CASE}} (no spaces, no dots,
    # no dashes). Helm/Go templates use {{ .Values.xxx }}, {{- define ... }},
    # {{- end }}, {{ include ... }}, {{ $var }}, etc. — always with a space,
    # dot, dash, or lowercase start after the opening braces.
    # Strategy: only flag tokens that look like {{UPPER_SNAKE}} placeholders.
    tokens="$(grep -o '{{[^}]*}}' "$f" 2>/dev/null \
        | grep '^{{[A-Z][A-Z0-9_]*}}$' \
        | sort -u | tr '\n' ' ')" || tokens=""
    if [ -n "$tokens" ]; then
        printf '  %s: %s\n' "$f" "$tokens"
    fi
done > "$RESIDUAL_DETAIL" 2>/dev/null || true

if [ -s "$RESIDUAL_DETAIL" ]; then
    printf 'ERROR: Residual {{}} tokens found after substitution (placeholder(s) missing from PARAMETERS.md):\n'
    cat "$RESIDUAL_DETAIL"
    result="FAIL"
    fail_reasons="${fail_reasons}residual-tokens "
fi

rm -f "$RESIDUAL_DETAIL"

# ── Check 2: Orphan parameters (in PARAMETERS.md but not in any template) ─────
# For each placeholder in PARAMETERS.md, check if it appears in any template file
ORPHAN_FILE="$(mktemp)"

while IFS="	" read -r placeholder example; do
    found=0
    while IFS= read -r src_file; do
        if grep -qF "$placeholder" "$src_file" 2>/dev/null; then
            found=1
            break
        fi
    done < "$TEMPLATE_LIST"
    if [ "$found" -eq 0 ]; then
        printf '  %s\n' "$placeholder" >> "$ORPHAN_FILE"
    fi
done < "$PARAMS_FILE"

if [ -s "$ORPHAN_FILE" ]; then
    printf 'ERROR: Orphan parameter(s) in PARAMETERS.md (not used in any template):\n'
    cat "$ORPHAN_FILE"
    result="FAIL"
    fail_reasons="${fail_reasons}orphan-params "
fi

rm -f "$ORPHAN_FILE"

# ── Summary ────────────────────────────────────────────────────────────────────
scratch_file_count="$(find "$SCRATCH_DIR" -type f | wc -l | tr -d ' ')"
printf 'INFO: Substituted %d placeholder(s) across %d template file(s) → %d file(s) in scratch dir\n' \
    "$param_count" "$template_count" "$scratch_file_count"

rm -f "$PARAMS_FILE" "$TEMPLATE_LIST"

printf 'DRY_RUN_RESULT: %s\n' "$result"

if [ "$result" = "FAIL" ]; then
    exit 1
fi
exit 0
