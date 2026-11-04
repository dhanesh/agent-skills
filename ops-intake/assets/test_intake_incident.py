"""The `incident` kind of intake-signals-jsonl (spec R24, decision D8: an added value only).

incident-watch writes one intake-signals-jsonl line of kind `incident` when it opens an
incident record. These tests import such a line, pick the item and check that the pick
envelope passes the skill-contract checks and the intake-item schema, and that a pick
policy can name the kind. The older kinds still behave as before."""
import json, os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters as AD
import contract_check as CC
import intake as IN
from intake_testkit import repo, run, no_io

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = "2026-11-04T09:00:00Z"
SOURCE = "incident-watch"
CFG = {"sources": {SOURCE: {"enabled": True, "formats": ["intake-signals-jsonl"]}}}
CTX = {"source": SOURCE, "now": NOW}


def incident_line(rid="inc-20261104T085900Z-0a1b2c"):
    """One line in the shape incident.py writes (see incident-watch's signal_line())."""
    return json.dumps({
        "source": SOURCE, "source_id": rid, "url": "", "kind": "incident",
        "title": "incident %s: 2 consecutive failed production probes (health)" % rid,
        "severity": 4, "first_seen": "2026-11-04T08:58:00Z", "last_seen": "2026-11-04T08:59:00Z",
        "evidence": [{"text": "probe 2026-11-04T08:59:00Z: health fail rc=1; record "
                              ".skill-contract/incidents/records/%s.json" % rid,
                      "key": "%s:open" % rid}]}, sort_keys=True) + "\n"


def cmd(root, *argv):
    with mock.patch.object(IN, "_now", return_value=NOW), no_io():
        return run(root, *argv)


def import_file(root, text):
    path = os.path.join(root, "signals.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return cmd(root, "import", "--format", "intake-signals-jsonl", "--source", SOURCE, path)


class IncidentKindTests(unittest.TestCase):
    def test_adapter_accepts_kind_incident(self):
        sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](incident_line(), CTX)
        self.assertEqual(problems, [])
        self.assertEqual(len(sigs), 1)
        self.assertEqual((sigs[0]["kind"], sigs[0]["severity"], sigs[0]["trust"]),
                         ("incident", 4, "low"))
        self.assertIn("incident", AD.KINDS)
        self.assertIn("incident", AD.SIGNAL_KINDS)

    def test_older_kinds_still_import_and_others_stay_refused(self):
        for kind in ("release", "ci", "issue"):
            rec = json.loads(incident_line())
            rec["kind"] = kind
            sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](json.dumps(rec), CTX)
            self.assertEqual((len(sigs), problems), (1, []), kind)
        for kind in ("feedback", "Incident", "outage"):
            rec = json.loads(incident_line())
            rec["kind"] = kind
            sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](json.dumps(rec), CTX)
            self.assertEqual(sigs, [], kind)
            self.assertEqual(problems, ["line 1: kind must be release, ci, issue or incident"])

    def test_import_makes_one_item_and_a_reimport_adds_nothing(self):
        root = repo(config=CFG)
        rc, out = import_file(root, incident_line())
        self.assertEqual(rc, 0, out)
        self.assertIn("IMPORT: %s intake-signals-jsonl ok 1 problems 0" % SOURCE, out)
        items = IN.Queue.load(root).items
        self.assertEqual(len(items), 1)
        item = list(items.values())[0]
        self.assertEqual((item["kind"], item["severity"], item["count"]), ("incident", 4, 1))
        rc, out = import_file(root, incident_line())
        self.assertEqual(rc, 0, out)
        item = list(IN.Queue.load(root).items.values())[0]
        self.assertEqual(item["count"], 1)

    def test_picked_incident_item_is_a_valid_intake_item_envelope(self):
        root = repo(config=CFG)
        import_file(root, incident_line())
        iid = list(IN.Queue.load(root).items)[0]
        rc, out = cmd(root, "pick", iid, "--by", "Owner")
        self.assertEqual(rc, 0, out)
        env = out.split("NEXT: run spec-first-planning with ", 1)[1].strip()
        with open(os.path.join(root, env), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(CC.check_statement(st), [])
        self.assertEqual(st["predicate"]["payload"]["kind"], "incident")
        with open(os.path.join(HERE, "schemas", "intake-item.v1.json"), encoding="utf-8") as f:
            schema = json.load(f)
        self.assertIn("incident", schema["properties"]["kind"]["enum"])

    def test_pick_policy_can_name_kind_incident(self):
        self.assertIn("incident", IN.POLICY_KINDS)
        pol = {"kinds": ["incident"], "min_rank": 0, "formats": ["intake-signals-jsonl"],
               "max_picks_per_day": 1}
        self.assertEqual(IN.policy_problems(pol), [])


if __name__ == "__main__":
    unittest.main()
