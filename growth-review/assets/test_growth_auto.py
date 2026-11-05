"""growth-review: the unattended review (`watch` and `auto`).

`auto` reviews the export file the human registered with `watch`, but only when the file's
content changed since the last `auto` review. Its stdout is the same signal lines that
`signals` prints, so the same pipe into ops-intake takes them: these tests run that pipe
into a real ops-intake queue (the sibling skill, loaded from its directory) and read the
queue back."""
import json
import os
import unittest
from unittest import mock

from growth_testkit import (G, TODAY, day, data_lines, intake_items, intake_repo,
                            intake_run, no_io, rec, records, registered, repo, run, run_err,
                            write_plan, PLAN)

YDAY = "2026-10-11"  # the latest complete day on TODAY (2026-10-12)
NOW = "2026-10-12T09:00:00Z"
SKIP = "SKIP: export unchanged"

# signups breaches (70 <= 80); the other two metrics have no data for their periods.
BREACH = [rec("signups", YDAY, 70)]
CLEAN = [rec("signups", YDAY, 150)]


def state(root):
    with open(os.path.join(root, *G.GROWTH_DIR.split("/"), G.PLAN_STATE),
              encoding="utf-8") as f:
        return json.load(f)


def log_events(root):
    with open(os.path.join(root, *G.GROWTH_DIR.split("/"), G.LOG_FILE),
              encoding="utf-8") as f:
        return [json.loads(l)["event"] for l in f if l.strip()]


class AutoBase(unittest.TestCase):
    def setUp(self):
        self.root = registered()
        self.export = records(self.root, BREACH, name="export.jsonl")
        self.iroot = intake_repo()

    def watch(self, path=None, by="Dana"):
        return run(self.root, "watch", "--by", by, path or self.export)

    def auto(self, today=TODAY):
        return run_err(self.root, "auto", "--source", "growth", today=today)

    def pipe(self, today=TODAY, now=NOW):
        """growth auto | intake import, as the scheduled tick runs it."""
        rc, out, err = self.auto(today)
        path = os.path.join(self.iroot, "auto.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        irc, iout = intake_run(self.iroot, "import", "--format", "intake-signals-jsonl",
                               "--source", "growth", path, now=now)
        self.assertEqual(irc, 0, iout)
        return rc, out, err, iout


class ChangedExport(AutoBase):
    def test_a_changed_export_is_reviewed_and_the_breach_lands_in_the_queue(self):
        rc, out = self.watch()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("WATCH: "), out)
        rc, out, err, iout = self.pipe()
        self.assertEqual(rc, 0, err)  # the breach went to the queue, which is the alarm
        lines = err.splitlines()
        self.assertIn("IMPORT: added 1 corrected 0 unchanged 0 problems 0", lines)
        self.assertIn("METRIC: signups BREACH value=70 period=2026-10-11", lines)
        self.assertIn("REVIEW: ok=0 warn=0 breach=1 no_data=2 stale=no", lines)
        self.assertEqual(lines[-1], "SIGNALS: 1")
        self.assertNotIn(SKIP, lines)
        self.assertIn("ok 1 problems 0", iout)
        items = intake_items(self.iroot)
        self.assertEqual(len(items), 1)
        item = list(items.values())[0] if isinstance(items, dict) else items[0]
        text = json.dumps(item, sort_keys=True, default=str)
        self.assertIn("signups", text)
        self.assertIn("metric: signups breached", text)
        self.assertIn("signups:2026-10-11", text)
        self.assertEqual(len(data_lines(self.root)), 1)

    def test_auto_prints_the_same_signal_lines_as_signals(self):
        self.watch()
        rc, out, err = self.auto()
        self.assertEqual(rc, 0, err)
        rc2, sig, _ = run_err(self.root, "signals", "--source", "growth")
        self.assertEqual(rc2, 0)
        self.assertEqual(out, sig)
        self.assertTrue(all(json.loads(l) for l in out.splitlines()))

    def test_an_edited_export_is_reviewed_again(self):
        self.watch()
        self.pipe()
        records(self.root, BREACH + [rec("signups", day(YDAY, -1), 60)], name="export.jsonl")
        rc, out, err, iout = self.pipe()
        self.assertEqual(rc, 0, err)
        self.assertIn("IMPORT: added 1 corrected 0 unchanged 1 problems 0", err.splitlines())
        self.assertNotIn(SKIP, err)

    def test_a_clean_export_signals_nothing(self):
        records(self.root, CLEAN, name="export.jsonl")
        self.watch()
        rc, out, err, iout = self.pipe()
        self.assertEqual((rc, out), (0, ""), err)
        self.assertIn("SIGNALS: 0", err.splitlines())
        self.assertEqual(len(intake_items(self.iroot)), 0)

    def test_the_digest_is_an_added_field_and_register_keeps_it(self):
        self.watch()
        self.auto()
        st = state(self.root)
        self.assertEqual(st["version"], 1)
        self.assertEqual(len(st["registrations"]), 1)
        self.assertEqual(st["auto"]["export"], os.path.abspath(self.export))
        self.assertEqual(len(st["auto"]["last_sha256"]), 64)
        write_plan(self.root, PLAN.replace("| 100 | 80 |", "| 100 | 60 |"))
        rc, out = run(self.root, "register", "--by", "Dana", "--reason", "lower bar")
        self.assertEqual(rc, 0, out)
        self.assertEqual(state(self.root)["auto"], st["auto"])
        self.assertEqual(log_events(self.root),
                         ["register", "watch", "import", "auto", "register"])

    def test_a_stale_plan_still_signals_and_asks_for_the_human(self):
        self.watch()
        write_plan(self.root, PLAN.replace("| 100 | 80 |", "| 100 | 60 |"))
        rc, out, err, iout = self.pipe()
        self.assertEqual(rc, 3, err)
        self.assertIn("METRIC: signups BREACH +stale value=70 period=2026-10-11",
                      err.splitlines())
        self.assertEqual(len(intake_items(self.iroot)), 1)

    def test_import_problems_exit_3(self):
        records(self.root, BREACH + [rec("nope", YDAY, 1)], name="export.jsonl")
        self.watch()
        rc, out, err = self.auto()
        self.assertEqual(rc, 3, err)
        self.assertIn("IMPORT: added 1 corrected 0 unchanged 0 problems 1", err.splitlines())
        self.assertEqual(len(out.splitlines()), 1)

    def test_auto_opens_no_socket_and_runs_no_command(self):
        self.watch()
        with no_io():
            rc, out, err = self.auto()
            self.assertEqual(rc, 0, err)
            rc, out, err = self.auto()
            self.assertEqual(err.splitlines(), [SKIP])


class UnchangedExport(AutoBase):
    def test_an_unchanged_export_prints_skip_and_imports_nothing(self):
        self.watch()
        self.pipe()
        events = log_events(self.root)
        rc, out, err, iout = self.pipe()
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")  # an empty stdin imports as ok 0 in ops-intake
        self.assertEqual(err.splitlines(), [SKIP])
        self.assertIn("ok 0 problems 0", iout)
        self.assertEqual(log_events(self.root), events)
        self.assertEqual(len(intake_items(self.iroot)), 1)

    def test_a_touched_file_with_the_same_content_is_skipped(self):
        self.watch()
        self.auto()
        records(self.root, BREACH, name="export.jsonl")
        os.utime(self.export, (1, 1))
        rc, out, err = self.auto()
        self.assertEqual((rc, out, err.splitlines()), (0, "", [SKIP]))

    def test_watch_again_resets_the_digest(self):
        self.watch()
        self.auto()
        self.watch()
        rc, out, err = self.auto()
        self.assertEqual(rc, 0)
        self.assertNotIn(SKIP, err)
        self.assertIn("IMPORT: added 0 corrected 0 unchanged 1 problems 0", err.splitlines())


class Refusals(AutoBase):
    def test_no_registered_export_stops(self):
        rc, out, err = self.auto()
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("STOP: no export file is registered", err)

    def test_a_missing_export_stops(self):
        self.watch()
        os.unlink(self.export)
        rc, out, err = self.auto()
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("STOP: the export file", err)
        self.assertIn("is missing", err)

    def test_no_registered_plan_stops(self):
        root = repo()
        export = records(root, BREACH, name="export.jsonl")
        rc, out = run(root, "watch", "--by", "Dana", export)
        self.assertEqual(rc, 2)
        self.assertIn("STOP: no registered plan", out)
        rc, out, err = run_err(root, "auto", "--source", "growth")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("STOP: no registered plan", err)

    def test_watch_refuses_an_agent_name_and_a_missing_file(self):
        rc, out = self.watch(by="agent:x")
        self.assertEqual(rc, 2)
        self.assertIn("an agent: name cannot choose the export file", out)
        rc, out = self.watch(path=os.path.join(self.root, "nope.jsonl"))
        self.assertEqual(rc, 2)
        self.assertIn("is missing", out)
        self.assertNotIn("auto", state(self.root))

    def test_a_bad_source_stops(self):
        self.watch()
        rc, out, err = run_err(self.root, "auto", "--source", "release")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("STOP: --source", err)

    def test_a_malformed_auto_entry_stops(self):
        self.watch()
        st = state(self.root)
        st["auto"]["last_sha256"] = "x"
        path = os.path.join(self.root, *G.GROWTH_DIR.split("/"), G.PLAN_STATE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        rc, out, err = self.auto()
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("malformed", err)

    def test_auto_registers_no_threshold(self):
        self.watch()
        before = state(self.root)["registrations"]
        with mock.patch.object(G, "cmd_register", side_effect=AssertionError("register")):
            self.auto()
        self.assertEqual(state(self.root)["registrations"], before)


if __name__ == "__main__":
    unittest.main()
