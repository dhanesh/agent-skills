"""Characterisation tests: pin today's machine lines and exit codes of pick, sync,
resolve and status on the fixture queue (fixtures/signals_ok.jsonl).

These tests record what intake.py prints now. A later change to intake.py that alters
one of these lines fails here, so the change must be deliberate. The clock is fixed
through intake._now; the envelope name holds the real UTC time and a random suffix, so
the test pins only its shape."""
import json, os, re, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "signals_ok.jsonl")
CONFIG = {"sources": {"jira": {"enabled": True, "formats": ["intake-signals-jsonl"]}}}
A = "I84203d4a32"   # jira OPS-1, severity 3, `db slow`
B = "I8ba019757d"   # jira OPS-2, severity 1, `flaky`
OLD = "2026-12-01T00:00:00Z"     # 60 days after the fixture's last_seen: no recency points
RECENT = "2026-10-12T00:00:00Z"  # 10 days after it: 20 recency points
ENVELOPE_RE = re.compile(
    r"\ANEXT: run spec-first-planning with "
    r"(\.skill-contract/intake/envelopes/intake-item-v1-\d{8}T\d{6}Z-[0-9a-f]{6}\.json)\Z")
NO_CONFIG = "STOP: config: no config at .intake/config.json\n"


class Base(unittest.TestCase):
    now = OLD

    def setUp(self):
        p = mock.patch.object(IN, "_now", lambda: self.now)
        p.start()
        self.addCleanup(p.stop)
        self.root = repo(config=CONFIG)
        rc, out = self.run_cmd("import", "--format", "intake-signals-jsonl", "--source", "jira",
                               FIXTURE)
        self.assertEqual((rc, out), (0, "IMPORT: jira intake-signals-jsonl ok 2 problems 0\n"))

    def run_cmd(self, *argv):
        with no_io():
            return run(self.root, *argv)

    def assertLines(self, argv, rc, lines):
        got_rc, out = self.run_cmd(*argv)
        self.assertEqual((got_rc, out.splitlines()), (rc, lines), argv)

    def envelopes(self):
        d = os.path.join(self.root, IN.INTAKE_DIR, "envelopes")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


class StatusTests(Base):
    def test_status_on_the_fresh_fixture_queue(self):
        self.assertEqual(IN.item_id("jira", "OPS-1"), A)
        self.assertEqual(IN.item_id("jira", "OPS-2"), B)
        self.assertLines(["status"], 0, [
            "SYNC: 2 items new=2 picked=0 planned=0 resolved=0 dismissed=0 regressed=0"])

    def test_status_on_an_empty_queue(self):
        root = repo(config=CONFIG)
        with no_io():
            self.assertEqual(run(root, "status"), (0,
                "SYNC: 0 items new=0 picked=0 planned=0 resolved=0 dismissed=0 regressed=0\n"))

    def test_status_counts_follow_each_state_change(self):
        self.run_cmd("pick", A, "--by", "verifier")
        self.assertLines(["status"], 0, [
            "SYNC: 2 items new=1 picked=1 planned=0 resolved=0 dismissed=0 regressed=0"])
        self.run_cmd("resolve", A, "--by", "verifier")
        self.assertLines(["status"], 0, [
            "SYNC: 2 items new=1 picked=0 planned=0 resolved=1 dismissed=0 regressed=0"])

    def test_status_refusals(self):
        with no_io():
            self.assertEqual(run(repo(), "status"), (2, NO_CONFIG))
        with open(os.path.join(self.root, IN.INTAKE_DIR, "queue.json"), "w") as f:
            f.write("{not json")
        rc, out = self.run_cmd("status")
        self.assertEqual(rc, 2)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("STOP: queue-unreadable "), out)


class PickTests(Base):
    def test_pick_prints_the_item_and_the_envelope(self):
        rc, out = self.run_cmd("pick", A, "--by", "verifier")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2, out)
        self.assertEqual(lines[0], "ITEM: %s picked+low-trust 0 `db slow`" % A)
        m = ENVELOPE_RE.match(lines[1])
        self.assertTrue(m, lines[1])
        self.assertTrue(os.path.isfile(os.path.join(self.root, *m.group(1).split("/"))))
        self.assertTrue(os.path.isfile(os.path.join(self.root, IN.INTAKE_DIR, "items",
                                                     A + ".json")))
        self.assertEqual(IN.Queue.load(self.root).items[A]["envelope"], m.group(1))

    def test_pick_after_sync_shows_the_stored_rank(self):
        self.run_cmd("sync")
        rc, out = self.run_cmd("pick", B, "--by", "verifier")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], "ITEM: %s picked+low-trust 101 `flaky`" % B)

    def test_pick_refusals(self):
        self.run_cmd("pick", A, "--by", "verifier")
        before = self.envelopes()
        self.assertEqual(len(before), 1)
        self.assertLines(["pick", A, "--by", "verifier"], 2, [
            "STOP: only a new item can be picked; `%s` is picked+low-trust" % A])
        self.assertEqual(self.envelopes(), before)   # a refused pick writes no envelope
        self.assertLines(["pick", "Inope", "--by", "verifier"], 2, ["STOP: no item `Inope`"])
        with no_io():
            self.assertEqual(run(repo(), "pick", A, "--by", "verifier"), (2, NO_CONFIG))


class SyncTests(Base):
    def test_sync_line_and_stored_ranks_with_no_recency(self):
        self.assertLines(["sync"], 0, [
            "SYNC: 2 items new=2 picked=0 planned=0 resolved=0 dismissed=0 "
            "envelopes 0 problems 0"])
        self.assertLines(["list"], 0, [
            "ITEM: %s new+low-trust 301 `db slow`" % A,
            "ITEM: %s new+low-trust 101 `flaky`" % B])

    def test_sync_after_pick(self):
        self.run_cmd("pick", A, "--by", "verifier")
        self.assertLines(["sync"], 0, [
            "SYNC: 2 items new=1 picked=1 planned=0 resolved=0 dismissed=0 "
            "envelopes 0 problems 0"])
        self.assertLines(["list", "--all"], 0, [
            "ITEM: %s picked+low-trust 301 `db slow`" % A,
            "ITEM: %s new+low-trust 101 `flaky`" % B])

    def test_sync_after_resolve(self):
        self.run_cmd("resolve", A, "--by", "verifier")
        self.assertLines(["sync"], 0, [
            "SYNC: 2 items new=1 picked=0 planned=0 resolved=1 dismissed=0 "
            "envelopes 0 problems 0"])

    def test_sync_logs_one_event(self):
        self.run_cmd("sync")
        log = os.path.join(self.root, IN.INTAKE_DIR, "intake-log.jsonl")
        with open(log, encoding="utf-8") as f:
            events = [json.loads(l) for l in f if l.strip()]
        syncs = [e for e in events if e["event"] == "sync"]
        self.assertEqual(len(syncs), 1)
        self.assertEqual((syncs[0]["envelopes"], syncs[0]["problems"], syncs[0]["at"]),
                         (0, 0, OLD))

    def test_sync_refusals(self):
        with no_io():
            self.assertEqual(run(repo(), "sync"), (2, NO_CONFIG))
        with open(os.path.join(self.root, IN.INTAKE_DIR, "queue.json"), "w") as f:
            f.write("[]")
        rc, out = self.run_cmd("sync")
        self.assertEqual(rc, 2)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("STOP: queue-unreadable "), out)


class RecentSyncTests(Base):
    now = RECENT

    def test_sync_adds_recency_points(self):
        self.assertLines(["sync"], 0, [
            "SYNC: 2 items new=2 picked=0 planned=0 resolved=0 dismissed=0 "
            "envelopes 0 problems 0"])
        self.assertLines(["list"], 0, [
            "ITEM: %s new+low-trust 321 `db slow`" % A,
            "ITEM: %s new+low-trust 121 `flaky`" % B])


class ResolveTests(Base):
    def test_resolve_a_picked_item_after_sync(self):
        self.run_cmd("pick", A, "--by", "verifier")
        self.run_cmd("sync")
        self.assertLines(["resolve", A, "--by", "verifier"], 0, [
            "ITEM: %s resolved+low-trust 301 `db slow`" % A])
        it = IN.Queue.load(self.root).items[A]
        self.assertEqual((it["state"], it["state_by"], it["closed_at"]),
                         ("resolved", "verifier", OLD))

    def test_resolve_a_new_item_by_hand_before_sync(self):
        self.assertLines(["resolve", B, "--by", "verifier"], 0, [
            "ITEM: %s resolved+low-trust 0 `flaky`" % B])

    def test_resolve_refusals(self):
        self.run_cmd("resolve", A, "--by", "verifier")
        self.assertLines(["resolve", A, "--by", "verifier"], 2, ["STOP: already resolved"])
        self.assertLines(["resolve", "Inope", "--by", "verifier"], 2, ["STOP: no item `Inope`"])
        with no_io():
            self.assertEqual(run(repo(), "resolve", A, "--by", "verifier"), (2, NO_CONFIG))


if __name__ == "__main__":
    unittest.main()
