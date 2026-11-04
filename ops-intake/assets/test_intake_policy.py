"""Pick-policy registration (R7, decision D2): autopick refuses (exit 2) before the owner
registers .intake/pick-policy.json, runs after the registration, and refuses again after
any edit to the file until the owner registers again. Format filter (R8, S3): a low-trust
item never matches unless the policy names the format it was imported from."""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
FIXTURE = os.path.join(FIXTURES, "signals_ok.jsonl")
FEEDBACK = os.path.join(FIXTURES, "feedback_ok.jsonl")
CONFIG = {"sources": {"jira": {"enabled": True, "formats": ["intake-signals-jsonl"]}}}
POLICY = {"kinds": ["issue", "ci"], "min_rank": 0, "formats": ["intake-signals-jsonl"],
          "max_picks_per_day": 1}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CONFIG)
        self.assertEqual(self.cmd("import", "--format", "intake-signals-jsonl", "--source",
                                  "jira", FIXTURE)[0], 0)
        self.assertEqual(self.cmd("sync")[0], 0)
        self.write_policy(POLICY)

    def cmd(self, *argv):
        with no_io():
            return run(self.root, *argv)

    def write_policy(self, pol):
        with open(os.path.join(self.root, IN.POLICY_PATH), "w", encoding="utf-8") as f:
            f.write(pol if isinstance(pol, str) else json.dumps(pol, indent=2))

    def queue_bytes(self):
        with open(os.path.join(self.root, IN.INTAKE_DIR, IN.QUEUE_FILE), "rb") as f:
            return f.read()

    def test_unregistered_registered_edited_reregistered(self):
        before = self.queue_bytes()
        # 1. Before registration: refuse.
        rc, out = self.cmd("autopick")
        self.assertEqual(rc, 2, out)
        self.assertIn("STOP: policy-unregistered", out)
        self.assertNotIn("AUTOPICK:", out)
        # 2. After registration: runs.
        rc, out = self.cmd("register-policy", "--by", "Dhanesh")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"\AREGISTERED: [0-9a-f]{64} by `Dhanesh` at ")
        rc, out = self.cmd("autopick")
        self.assertEqual(rc, 0, out)
        self.assertIn("AUTOPICK: matched 2 picked 0", out)
        self.assertEqual(sum(l.startswith("MATCH: ") for l in out.splitlines()), 2)
        # 3. After an edit: refuse until the owner registers again.
        self.write_policy(dict(POLICY, min_rank=1000))
        rc, out = self.cmd("autopick")
        self.assertEqual(rc, 2, out)
        self.assertIn("STOP: policy-changed", out)
        self.assertNotIn("AUTOPICK:", out)
        # A whitespace-only edit changes the bytes, so it also needs a new registration.
        self.write_policy(json.dumps(dict(POLICY, min_rank=1000)) + "\n\n")
        self.assertEqual(self.cmd("autopick")[0], 2)
        rc, out = self.cmd("register-policy", "--by", "Dhanesh", "--reason", "raise the bar")
        self.assertEqual(rc, 0, out)
        rc, out = self.cmd("autopick")
        self.assertEqual((rc, out.splitlines()[-1]), (0, "AUTOPICK: matched 0 picked 0"))
        # autopick changes no queue state in this version.
        self.assertEqual(self.queue_bytes(), before)

    def test_reregister_without_change_is_a_no_op(self):
        self.assertEqual(self.cmd("register-policy", "--by", "Dhanesh")[0], 0)
        rc, out = self.cmd("register-policy", "--by", "Dhanesh")
        self.assertEqual(rc, 0)
        self.assertIn("(no change since the last registration)", out)
        with open(os.path.join(self.root, IN.INTAKE_DIR, IN.POLICY_STATE)) as f:
            self.assertEqual(len(json.load(f)["registrations"]), 1)

    def test_an_agent_cannot_register(self):
        for name in ("agent:claude", "Agent", "  ", "ag​ent"):
            rc, out = self.cmd("register-policy", "--by", name)
            self.assertEqual(rc, 2, name)
            self.assertTrue(out.startswith("STOP: "), out)
        self.assertEqual(self.cmd("autopick")[0], 2)

    def test_invalid_or_missing_policy_is_refused(self):
        bad = [dict(POLICY, kinds=[]), dict(POLICY, kinds=["bug"]),
               dict(POLICY, formats=["csv"]), dict(POLICY, min_rank=-1),
               dict(POLICY, min_rank=True), dict(POLICY, max_picks_per_day=0),
               dict(POLICY, max_picks_per_day=1.5), dict(POLICY, extra=1),
               {k: v for k, v in POLICY.items() if k != "kinds"}, "[]", "{not json"]
        for pol in bad:
            self.write_policy(pol)
            rc, out = self.cmd("register-policy", "--by", "Dhanesh")
            self.assertEqual(rc, 2, pol)
            self.assertIn("STOP: policy: ", out)
        os.unlink(os.path.join(self.root, IN.POLICY_PATH))
        rc, out = self.cmd("autopick")
        self.assertEqual(rc, 2)
        self.assertIn("STOP: policy: no pick policy at .intake/pick-policy.json", out)

    def test_policy_filters_kind_and_rank(self):
        self.write_policy(dict(POLICY, kinds=["issue"]))
        self.assertEqual(self.cmd("register-policy", "--by", "Dhanesh")[0], 0)
        rc, out = self.cmd("autopick")
        self.assertEqual(rc, 0)
        matches = [l for l in out.splitlines() if l.startswith("MATCH: ")]
        self.assertEqual([l.split()[1] for l in matches], [IN.item_id("jira", "OPS-1")])

    def test_needs_config(self):
        root = repo()
        with no_io():
            self.assertEqual(run(root, "autopick")[0], 2)
            self.assertEqual(run(root, "register-policy", "--by", "Dhanesh")[0], 2)


class FormatTests(unittest.TestCase):
    """R8: a pick policy never matches a low-trust item unless it names the item's format."""
    CFG = {"sources": {"jira": {"enabled": True, "formats": ["intake-signals-jsonl"]},
                       "fb": {"enabled": True, "formats": ["feedback-jsonl"]}}}
    ALL = {"kinds": ["issue", "ci", "feedback"], "min_rank": 0,
           "formats": ["intake-signals-jsonl", "feedback-jsonl"], "max_picks_per_day": 1}

    def setUp(self):
        self.root = repo(config=self.CFG)
        with no_io():
            self.assertEqual(run(self.root, "import", "--format", "feedback-jsonl", "--source",
                                 "fb", FEEDBACK)[0], 0)
            self.assertEqual(run(self.root, "import", "--format", "intake-signals-jsonl",
                                 "--source", "jira", FIXTURE)[0], 0)
            self.assertEqual(run(self.root, "sync")[0], 0)
        self.fb = sorted((i for i in self.queue()["items"].values() if i["source"] == "fb"),
                         key=lambda i: -IN._rank(i))
        self.assertEqual(len(self.fb), 2)
        self.high, self.low = self.fb
        self.assertGreater(IN._rank(self.high), IN._rank(self.low))

    def queue(self):
        with open(os.path.join(self.root, IN.INTAKE_DIR, IN.QUEUE_FILE), encoding="utf-8") as f:
            return json.load(f)

    def matches(self, pol):
        with open(os.path.join(self.root, IN.POLICY_PATH), "w", encoding="utf-8") as f:
            json.dump(pol, f)
        with no_io():
            self.assertEqual(run(self.root, "register-policy", "--by", "Dhanesh")[0], 0)
            rc, out = run(self.root, "autopick")
        self.assertEqual(rc, 0, out)
        return {l.split()[1] for l in out.splitlines() if l.startswith("MATCH: ")}

    def test_items_record_their_import_format(self):
        for it in self.queue()["items"].values():
            want = ["feedback-jsonl"] if it["source"] == "fb" else ["intake-signals-jsonl"]
            self.assertEqual((it.get("trust"), it.get("formats")), ("low", want), it["id"])

    def test_policy_without_feedback_jsonl_never_matches_feedback(self):
        pol = dict(self.ALL, formats=["intake-signals-jsonl"])
        got = self.matches(pol)
        self.assertNotIn(self.high["id"], got)
        self.assertNotIn(self.low["id"], got)
        self.assertTrue(got, "the intake-signals-jsonl items still match")
        # Any rank: a minimum rank between the two, and an empty format list.
        mid = (IN._rank(self.high) + IN._rank(self.low)) / 2
        self.assertFalse({self.high["id"], self.low["id"]} & self.matches(dict(pol, min_rank=mid)))
        self.assertEqual(self.matches(dict(self.ALL, formats=[])), set())

    def test_policy_naming_feedback_jsonl_matches_feedback(self):
        got = self.matches(dict(self.ALL, formats=["feedback-jsonl"]))
        self.assertEqual(got, {self.high["id"], self.low["id"]})
        mid = (IN._rank(self.high) + IN._rank(self.low)) / 2
        got = self.matches(dict(self.ALL, formats=["feedback-jsonl"], min_rank=mid))
        self.assertEqual(got, {self.high["id"]})

    def test_item_without_recorded_format_fails_closed(self):
        # An item imported before R8 has no formats field: no policy names its format.
        data = self.queue()
        for it in data["items"].values():
            it.pop("formats", None)
        normal = data["items"][self.low["id"]]
        normal["trust"] = "normal"  # a normal-trust item is not filtered by format
        with open(os.path.join(self.root, IN.INTAKE_DIR, IN.QUEUE_FILE), "w") as f:
            json.dump(data, f)
        self.assertEqual(self.matches(self.ALL), {self.low["id"]})

    def test_low_trust_item_needs_every_format_named(self):
        it = {"state": "new", "kind": "feedback", "trust": "low", "rank": 500,
              "formats": ["feedback-jsonl", "intake-signals-jsonl"]}
        self.assertFalse(IN.policy_matches(it, dict(self.ALL, formats=["feedback-jsonl"])))
        self.assertTrue(IN.policy_matches(it, self.ALL))


if __name__ == "__main__":
    unittest.main()
