#!/bin/sh
# package-skill.sh <skill-dir> [--tar <output.tar.gz>]
#
# Produces a MANIFEST listing of all files in skill-dir (relative paths, sorted).
# Optionally packages into a gzipped tarball.
#
# Exit 0 on success, non-zero on error.
set -eu

# ── Usage ──────────────────────────────────────────────────────────────────────
if [ "$#" -lt 1 ]; then
    printf 'Usage: %s <skill-dir> [--tar <output.tar.gz>]\n' "$0" >&2
    exit 1
fi

SKILL_DIR="$1"
shift

TAR_OUTPUT=""

# Parse optional --tar argument
while [ "$#" -gt 0 ]; do
    case "$1" in
        --tar)
            if [ "$#" -lt 2 ]; then
                printf 'ERROR: --tar requires an output path argument\n' >&2
                exit 1
            fi
            TAR_OUTPUT="$2"
            shift 2
            ;;
        *)
            printf 'ERROR: Unknown argument: %s\n' "$1" >&2
            exit 1
            ;;
    esac
done

# ── Validate skill-dir ─────────────────────────────────────────────────────────
if [ ! -d "$SKILL_DIR" ]; then
    printf 'ERROR: skill-dir not found: %s\n' "$SKILL_DIR" >&2
    exit 1
fi

if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
    printf 'ERROR: %s/SKILL.md not found — not a valid skill directory\n' "$SKILL_DIR" >&2
    exit 1
fi

# ── Produce MANIFEST ──────────────────────────────────────────────────────────
# List all files under skill-dir as relative paths, sorted
file_count=0
# Use find, strip the leading skill-dir prefix, sort
manifest="$(find "$SKILL_DIR" -type f | sed "s|^${SKILL_DIR}/||" | sort)"

printf '%s\n' "$manifest"

file_count="$(printf '%s\n' "$manifest" | grep -c '.' || true)"

printf 'FILES: %d\n' "$file_count"

# ── Optional: create tarball ──────────────────────────────────────────────────
if [ -n "$TAR_OUTPUT" ]; then
    # tar czf from the parent of skill-dir so paths inside tarball are relative
    skill_basename="$(basename "$SKILL_DIR")"
    skill_parent="$(dirname "$SKILL_DIR")"
    # Use -C to change to parent dir so the tarball contains skill-basename/...
    tar czf "$TAR_OUTPUT" -C "$skill_parent" "$skill_basename"
    printf 'PACKAGED: %s\n' "$TAR_OUTPUT"
fi

exit 0
