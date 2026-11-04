"""Pick-policy registration (R7, decision D2): autopick refuses (exit 2) before the owner
registers .intake/pick-policy.json, runs after the registration, and refuses again after
any edit to the file until the owner registers again."""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "signals_ok.jsonl")
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


if __name__ == "__main__":
    unittest.main()
