#!/usr/bin/env python3
"""factory_status.py: keep the README stage table inside its status rubric.

The root README.md has a table under "### Which skills cover which stage", after
the "**Status rubric.**" list. The rubric allows five statuses. Nothing read the
table, so a row could claim a status outside the rubric, or drop the text that
names its limits, and `make gate` stayed green.

Usage:
  python3 factory_status.py README.md

For each row of `| Stage | Skills | Status |`:
  - the status is exactly one of the five rubric values;
  - a row whose status is not `covered` has the text "Limits:" in its Skills cell;
  - the text after "Limits:" is a list of items separated by `;`, and each item
    starts with exactly one tag, `[decision]`, `[trust]` or `[open]`, followed
    by text, with no second tag in the item;
  - a row whose items are all `[decision]` has the status `covered`.

The check fails closed: no heading, no rubric or no table is a failure, not a pass.

Prints one FAIL: line per offending row (naming the stage) and a final
FACTORY_STATUS_RESULT: PASS (n rows) | FAIL (n) line. Exits 1 on failure, 2 on a
usage error. Stdlib only.
"""
import sys

HEADING = "### Which skills cover which stage"
RUBRIC = "**Status rubric.**"
HEADER_CELLS = ["Stage", "Skills", "Status"]
STATUSES = (
    "covered",
    "covered, with limits",
    "partly",
    "missing",
    "out of scope, by design",
)
LIMITS = "Limits:"
TAGS = ("[decision]", "[trust]", "[open]")


def cells(line):
    """Split a markdown table row into stripped cells."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def find_rows(lines):
    """Return (rows, error). Each row is (line number, cells)."""
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == HEADING)
    except StopIteration:
        return None, "heading %r not found" % HEADING
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("#")),
        len(lines),
    )
    section = range(start + 1, end)
    rubric = next((i for i in section if lines[i].startswith(RUBRIC)), None)
    if rubric is None:
        return None, "%r list not found under %r" % (RUBRIC, HEADING)
    header = next(
        (i for i in range(rubric + 1, end)
         if lines[i].lstrip().startswith("|") and cells(lines[i]) == HEADER_CELLS),
        None,
    )
    if header is None:
        return None, "table '| Stage | Skills | Status |' not found after the rubric"
    rows = []
    for i in range(header + 2, end):
        if not lines[i].lstrip().startswith("|"):
            break
        rows.append((i + 1, cells(lines[i])))
    if not rows:
        return None, "the stage table has no rows"
    return rows, None


def limit_tags(text):
    """Return (tags, bad items) for the text after "Limits:".

    tags holds the one tag of each well-formed item. A bad item is one with no
    leading tag, no text after its tag, or more than one tag.
    """
    tags, bad = [], []
    for item in (part.strip() for part in text.split(";")):
        lead = next((t for t in TAGS if item.startswith(t)), None)
        rest = item[len(lead):].strip() if lead else ""
        if lead is None or not rest or any(t in rest for t in TAGS):
            bad.append(item)
        else:
            tags.append(lead)
    return tags, bad


def check_limits(stage, lineno, status, skills):
    """Return the fails for the tagged items after "Limits:" in one row."""
    tags, bad = limit_tags(skills.split(LIMITS, 1)[1])
    fails = [
        "%s (line %d): limit item %r does not start with exactly one of %s"
        % (stage, lineno, item, ", ".join(TAGS))
        for item in bad
    ]
    if not bad and tags and all(t == "[decision]" for t in tags) and status != "covered":
        fails.append(
            "%s (line %d): every limit is [decision], so the status must be "
            "'covered', not %r" % (stage, lineno, status)
        )
    return fails


def check(lines):
    """Return (fails, row count). A fail is a printable reason."""
    rows, err = find_rows(lines)
    if err:
        return [err], 0
    fails = []
    for lineno, row in rows:
        if len(row) < 3:
            fails.append("line %d: row has %d cells, expected 3" % (lineno, len(row)))
            continue
        # A `|` inside the Skills cell would add cells; the stage is first, the
        # status last, and everything between belongs to Skills.
        stage, skills, status = row[0], " | ".join(row[1:-1]), row[-1]
        if status not in STATUSES:
            fails.append(
                "%s (line %d): status %r is not one of the five rubric values"
                % (stage, lineno, status)
            )
            continue
        if LIMITS not in skills:
            if status != "covered":
                fails.append(
                    "%s (line %d): status %r has no %r text in its Skills cell"
                    % (stage, lineno, status, LIMITS)
                )
            continue
        fails.extend(check_limits(stage, lineno, status, skills))
    return fails, len(rows)


def main(argv):
    if len(argv) != 2:
        print("usage: factory_status.py README.md", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        print("FAIL: cannot read %s: %s" % (argv[1], exc))
        print("FACTORY_STATUS_RESULT: FAIL (1)")
        return 1
    fails, n = check(lines)
    for f in fails:
        print("FAIL: " + f)
    if fails:
        print("FACTORY_STATUS_RESULT: FAIL (%d)" % len(fails))
        return 1
    print("FACTORY_STATUS_RESULT: PASS (%d rows)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
