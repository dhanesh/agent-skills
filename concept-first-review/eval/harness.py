#!/usr/bin/env python3
"""Synthetic scenarios for the concept-first-review outcome eval.

Every fixture is a hand-built diff with a KNOWN answer, so the grader can be
model-free. Three arms:

  treatment  a realistic agent-written change carrying noise worth hiding, a
             cross-file relocation, a seeded N+1, and a layering violation;
  control    an equally sized change with none of those properties, so a
             grader that always fires would be caught;
  adversary  plans that must be REJECTED (invented text, one-sided move).
"""

# The change under review: an order-listing endpoint gains a batched read, a
# helper relocates from the web layer into core, and the web layer starts
# importing a third-party HTTP client it did not need before.
TREATMENT_DIFF = (
    "diff --git a/web/orders.py b/web/orders.py\n"          # 1
    "--- a/web/orders.py\n"                                  # 2
    "+++ b/web/orders.py\n"                                  # 3
    "@@ -1,12 +1,26 @@\n"                                    # 4
    " import os\n"                                           # 5
    "-import json\n"                                         # 6
    "+import json\n"                                         # 7
    "+import requests\n"                                     # 8
    " \n"                                                    # 9
    " from core.models import Order\n"                       # 10
    " \n"                                                    # 11
    " def list_orders(user):\n"                              # 12
    "-    resp = Response()\n"                                # 13
    "-    resp.user_id = user.id\n"                          # 14
    "-    resp.box_id = user.box_id\n"                       # 15
    "-    resp.tenant = user.tenant\n"                       # 16
    "-    resp.created_at = user.created_at\n"               # 17
    "-    normalize_tenant_prefix(user.tenant, resp.box_id)\n"   # 18
    "-    stamp_request_identity(resp, user.session_token)\n"    # 19
    "-    emit_orders_listed_metric(resp.tenant, user.id)\n"     # 20
    "+    rows = []\n"                                       # 21
    "+    for order in user.orders:\n"                       # 22
    "+        for item in order.items:\n"                    # 23
    "+            rows.append(db.query(\"select * from items where id = ?\", item.id))\n"  # 24
    "+    if not rows:\n"                                    # 25
    "+        raise Missing('no rows for tenant %s and user %s' % (user.tenant, user.id))\n"  # 26
    "+    return rows\n"                                     # 27
    "diff --git a/core/identity.py b/core/identity.py\n"     # 28
    "--- a/core/identity.py\n"                               # 29
    "+++ b/core/identity.py\n"                               # 30
    "@@ -1,4 +1,12 @@\n"                                     # 31
    " def build_identity(user):\n"                           # 32
    "+        normalize_tenant_prefix(user.tenant, resp.box_id)\n"   # 33
    "+        stamp_request_identity(resp, user.session_token)\n"    # 34
    "+        emit_orders_listed_metric(resp.tenant, user.id)\n"     # 35
    "+from web.render import as_json\n"                      # 36
)

# Line numbers the grader relies on, named so a fixture edit fails loudly.
TREATMENT = {
    "import_rows": [5, 6, 7, 8, 10, 36],
    "move_removed": [18, 20],
    "move_added": [33, 35],
    "nested_loop_line": 23,
    "n_plus_one_line": 24,
    "layering_import_line": 36,
    "noise_rows": [14, 15, 16, 17],
    "anchor_row": 13,
    "error_row": 26,
}

# The plan a competent reviewer would submit for TREATMENT_DIFF: collapse the
# mechanical field copies, trim the error-message prose, and treat both ends of
# the relocation the same way.
GOOD_PLAN = {
    "edits": [
        {"op": "collapse", "lines": "14-17"},
        {"op": "collapse", "lines": "18-20"},
        {"op": "collapse", "lines": "33-35"},
        {
            "op": "trim",
            "line": 26,
            "from": "'no rows for tenant %s and user %s' % (user.tenant, user.id)",
            "to": "...",
        },
    ],
    "headline": "Order listing batches item reads; identity helpers relocate into core.",
}

# Adversarial plans, each of which MUST be rejected.
INVENTED_TEXT_PLAN = {
    "edits": [{"op": "trim", "line": 27, "from": "return rows", "to": "return cached_rows"}],
}

ONE_SIDED_MOVE_PLAN = {
    "edits": [{"op": "collapse", "lines": "33-35"}],
}

# Collapses `rows = []` away while line 24 still appends to it — the reader
# would meet a variable that comes from nowhere.
HIDES_DEFINITION_PLAN = {
    "edits": [{"op": "collapse", "lines": "21-23"}],
}

# The control arm: same rough size, none of the seeded properties. No high
# signals should come out of it, and an empty plan should be a no-op.
CONTROL_DIFF = (
    "diff --git a/core/pricing.py b/core/pricing.py\n"
    "--- a/core/pricing.py\n"
    "+++ b/core/pricing.py\n"
    "@@ -1,8 +1,12 @@\n"
    " def _subtotal(items):\n"
    "-    total = 0\n"
    "+    total = Decimal(0)\n"
    "     for item in items:\n"
    "-        total += item.price\n"
    "+        total += item.price * item.quantity\n"
    "     return total\n"
    "diff --git a/tests/test_pricing.py b/tests/test_pricing.py\n"
    "--- a/tests/test_pricing.py\n"
    "+++ b/tests/test_pricing.py\n"
    "@@ -1,4 +1,8 @@\n"
    " def test_subtotal_multiplies_quantity():\n"
    "-    assert _subtotal([Item(2, 1)]) == 2\n"
    "+    assert _subtotal([Item(2, 3)]) == 6\n"
)

DESIGN_RULES = {
    "layers": {"core": ["core/"], "web": ["web/"]},
    "forbidden_edges": [["core", "web"]],
    "allowed_external": ["flask"],
    "budgets": {"max_loop_depth": 1},
    "invariants": ["core/ is framework-free and never imports web/"],
}

PARAGRAPH = (
    "Listing a user's orders now reads item rows directly in the request path instead of "
    "delegating to the response-building helpers, and the identity helpers those calls used "
    "have relocated from the web layer into core so that both entry points share them."
)


# ── Prior reviews, for the second-opinion arm ────────────────────────────────

# What a hurried agent produces: fluent, confident, and blind to everything the
# extractors found. Auditing it must surface the high-severity gaps.
PRIOR_THIN_REVIEW = """# Review

The change looks reasonable. The code is readable and consistent with the rest of
the module, and the new helper placement seems sensible enough to me.
"""


def prior_strong_review(sigs):
    """A prior review that engages every high signal by location and id."""
    lines = [
        "# Review of the order-listing change",
        "",
        PARAGRAPH,
        "",
        "The algorithm is now quadratic in items per order and the layering between",
        "the web module and the core domain has changed direction. Rollout risk sits",
        "with the dependency addition. Verdict: needs-changes.",
        "",
    ]
    for sig in sigs:
        if sig["severity"] != "high":
            continue
        where = "%s:%d" % (sig["file"], sig["line"]) if sig["file"] else "(whole change)"
        lines.append("- %s at %s: raised with the author." % (sig["id"], where))
    return "\n".join(lines) + "\n"


# What the change was asked to do, in the requester's words. Scope drift is only
# checkable against something like this — a diff cannot show what is missing.
INTENT = "batch the per-item reads in order listing and move the identity helpers into core"
