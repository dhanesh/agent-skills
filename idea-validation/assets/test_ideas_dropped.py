"""The goalpost flag: an assumption that was critical in an earlier registration and is
now normal or deleted is surfaced, in status and in the handoff payload, never hidden.
Owner-proxy decision (Jev 0.97): flag it; do not refuse the handoff or mark results stale."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ideas_testkit import (GOOD, record, registered, run, verdict,  # noqa: E402
                           write_brief)

A2_STATEMENT = "Flatmates sign up before the app exists"
DEMOTED = GOOD.replace("- A2 [critical] [demand]", "- A2 [normal] [demand]")
# A3 made critical, so deleting it removes a critical assumption with no renumbering.
A3_CRITICAL = GOOD.replace("- A3 [normal] [channel]", "- A3 [critical] [channel]")
A3_DELETED = "\n".join(l for l in A3_CRITICAL.splitlines() if not l.startswith("- A3 ")) \
    .replace("Tests A3.", "Tests A1.") + "\n"


def reregister(root, text):
    write_brief(root, "flat", text)
    rc, out = run(root, "register", "ideas/flat.md", "--by", "Dana")
    assert rc == 0, out


def state(root):
    with open(os.path.join(root, ".skill-contract", "ideas", "flat", "state.json")) as f:
        return json.load(f)


def payload(root):
    rc, out = run(root, "decide", "flat", "--verdict", "go", "--by", "Dana", "--reason", "ok")
    assert rc == 0, out
    rc, out = run(root, "handoff", "flat", "--by", "Dana")
    assert rc == 0, out
    path = [l.split("with ", 1)[1] for l in out.splitlines() if l.startswith("NEXT: run spec")][0]
    with open(os.path.join(root, path), encoding="utf-8") as f:
        return json.load(f)["predicate"]["payload"], out


class DroppedCritical(unittest.TestCase):
    def test_none_dropped_gives_no_note_and_an_empty_list(self):
        root = registered()
        record(root, "A1", 7)
        record(root, "A2", 25)
        rc, out = run(root, "status", "flat")
        self.assertEqual(rc, 0)
        self.assertNotIn("dropped-critical", out)
        p, _ = payload(root)
        self.assertEqual(p["dropped_critical"], [])

    def test_demoted_after_a_result_is_noted_in_status(self):
        root = registered()
        first = state(root)["registrations"][0]["sha256"]
        record(root, "A1", 7)
        record(root, "A2", 1)  # A2 fails: the verdict is KILL
        self.assertEqual(verdict(run(root, "status", "flat")[1]), "KILL")
        reregister(root, DEMOTED)
        rc, out = run(root, "status", "flat")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE: dropped-critical A2", out)
        # the flag does not move the verdict: A2 is normal now, so only A1 counts
        self.assertEqual(verdict(out), "GO")
        p, hout = payload(root)
        self.assertEqual(p["dropped_critical"], [{
            "id": "A2", "statement": A2_STATEMENT, "last_critical_registration": first,
            "had_results": True}])
        self.assertIn("dropped-critical A2", hout)

    def test_demoted_before_any_result_has_no_results(self):
        root = registered()
        reregister(root, DEMOTED)
        record(root, "A1", 7)
        record(root, "A2", 25)  # recorded while A2 is normal: not under a critical registration
        p, _ = payload(root)
        self.assertEqual([(d["id"], d["had_results"]) for d in p["dropped_critical"]],
                         [("A2", False)])

    def test_deleted_critical_keeps_its_old_statement(self):
        root = registered(text=A3_CRITICAL)
        first = state(root)["registrations"][0]["sha256"]
        record(root, "A3", 3, kind="desk", by="agent:claude")
        reregister(root, A3_DELETED)
        rc, out = run(root, "status", "flat")
        self.assertIn("NOTE: dropped-critical A3", out)
        record(root, "A1", 7)
        record(root, "A2", 25)
        p, _ = payload(root)
        self.assertEqual(p["dropped_critical"], [{
            "id": "A3", "statement": "Flat-share groups allow posts about a new app",
            "last_critical_registration": first, "had_results": True}])
        self.assertNotIn("A3", [a["id"] for a in p["assumptions"]])

    def test_last_critical_registration_is_the_latest_one(self):
        root = registered()
        reregister(root, GOOD.replace("every week", "most weeks"))  # A2 still critical
        second = state(root)["registrations"][1]["sha256"]
        reregister(root, DEMOTED.replace("every week", "most weeks"))
        record(root, "A1", 7)
        p, _ = payload(root)
        self.assertEqual(p["dropped_critical"][0]["last_critical_registration"], second)

    def test_promoted_back_clears_the_flag(self):
        root = registered()
        reregister(root, DEMOTED)
        self.assertIn("dropped-critical", run(root, "status", "flat")[1])
        reregister(root, GOOD.replace("every week", "most weeks"))
        self.assertNotIn("dropped-critical", run(root, "status", "flat")[1])

    def test_decide_shows_the_flag_to_the_human(self):
        root = registered()
        reregister(root, DEMOTED)
        rc, out = run(root, "decide", "flat", "--verdict", "kill", "--by", "Dana",
                      "--reason", "x")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE: dropped-critical A2", out)

    def test_payload_entries_match_the_schema(self):
        root = registered()
        record(root, "A2", 1)
        reregister(root, DEMOTED)
        record(root, "A1", 7)
        p, _ = payload(root)
        with open(os.path.join(HERE, "schemas", "idea-brief.v1.json")) as f:
            schema = json.load(f)
        self.assertIn("dropped_critical", schema["required"])
        item = schema["properties"]["dropped_critical"]["items"]
        for d in p["dropped_critical"]:
            self.assertEqual(set(d), set(item["required"]))
            self.assertEqual(set(d), set(item["properties"]))
            self.assertRegex(d["last_critical_registration"], "^[0-9a-f]{64}$")

    def test_hostile_statement_is_a_code_span_in_status(self):
        hostile = GOOD.replace(A2_STATEMENT, "## Pwned `x` @maintainer")
        root = registered(text=hostile)
        reregister(root, hostile.replace("- A2 [critical] [demand]", "- A2 [normal] [demand]"))
        out = run(root, "status", "flat")[1]
        for line in out.splitlines():
            self.assertFalse(line.startswith("##"), line)


if __name__ == "__main__":
    unittest.main()
