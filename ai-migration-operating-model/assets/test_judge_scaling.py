#!/usr/bin/env python3
"""Unit tests for the judge-scaling tools — stdlib-only, offline, deterministic.

Covers capacity_probe.py, machine_lock.py, e2e_shard.py, impact.py,
mutant_select.py and parity_diff.py's --require (unjudged) option. No test
runs docker, colima, go or a real server: probes take injected values, and the
e2e captures are a small Python script that copies files.

Run standalone:
    cd ai-migration-operating-model/assets && python3 test_judge_scaling.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capacity_probe  # noqa: E402
import e2e_shard  # noqa: E402
import impact  # noqa: E402
import machine_lock  # noqa: E402
import mutant_select  # noqa: E402
import parity_diff  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="judge-scaling-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestCapacity(unittest.TestCase):
    def test_vm_not_host_is_the_limit(self):
        # 10 host cores but a 4-CPU / 6 GiB VM: the VM decides.
        lanes, why = capacity_probe.compute_lanes(10, 4, 6.0, 100)
        self.assertEqual(lanes, 3)
        self.assertIn("VM CPUs", why)
        lanes, why = capacity_probe.compute_lanes(10, 8, 3.2, 100)
        self.assertEqual(lanes, 2)
        self.assertIn("VM memory", why)

    def test_cap_and_host_ceiling(self):
        self.assertEqual(capacity_probe.compute_lanes(64, 32, 128, 500)[0], 6)
        self.assertEqual(capacity_probe.compute_lanes(4, 32, 128, 500)[0], 2)

    def test_serial_when_unmeasured_or_low_disk(self):
        lanes, why = capacity_probe.compute_lanes(10, None, None, 100)
        self.assertEqual((lanes, why.split(":")[0]), (1, "serial"))
        lanes, why = capacity_probe.compute_lanes(10, 8, 16, 2.0)
        self.assertEqual(lanes, 1)
        self.assertIn("free disk", why)

    def test_never_below_one(self):
        self.assertEqual(capacity_probe.compute_lanes(1, 1, 0.5, 100)[0], 1)

    def test_estimates(self):
        est = capacity_probe.estimate(3, scenarios=15000, secs_per_scenario=0.72,
                                      mutants=9, reaching=50)
        self.assertAlmostEqual(est["e2e_serial_s"], 10800)
        self.assertAlmostEqual(est["e2e_wall_s"], 3600)
        self.assertAlmostEqual(est["proof_wall_s"], 324)
        self.assertGreater(est["proof_full_suite_s"], est["proof_wall_s"])

    def test_cli_with_injected_values(self):
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            rc = capacity_probe.main(["--host-cpus", "10", "--vm-cpus", "4", "--vm-gib", "6",
                                      "--free-disk-gib", "50"])
        finally:
            sys.stdout = sys_stdout
        self.assertEqual(rc, 0)
        self.assertIn("LANES: 3 (parallel", out.getvalue())


class TestLock(TempCase):
    def test_owner_file_and_release(self):
        lock = os.path.join(self.tmp, "judge.lock")
        self.assertEqual(machine_lock.acquire(lock, "3", "prove x", out=io.StringIO()), 0)
        owner = machine_lock.read_owner(lock)
        self.assertEqual((owner["pid"], owner["worker"], owner["task"]), (os.getpid(), "3", "prove x"))
        self.assertIn("since", owner)
        machine_lock.release(lock)
        self.assertFalse(os.path.exists(lock))

    def test_stale_owner_is_taken_over(self):
        lock = os.path.join(self.tmp, "judge.lock")
        p = subprocess.Popen([PY, "-c", "pass"])
        p.wait()
        os.mkdir(lock)
        _write(os.path.join(lock, "owner"), {"pid": p.pid, "host": machine_lock.socket.gethostname(),
                                             "worker": "1", "task": "old", "since": "x"})
        out = io.StringIO()
        self.assertEqual(machine_lock.acquire(lock, "2", "new", wait_s=5, poll_s=0.05, out=out), 0)
        self.assertIn("stale", out.getvalue())
        machine_lock.release(lock)

    def test_live_owner_times_out(self):
        lock = os.path.join(self.tmp, "judge.lock")
        os.mkdir(lock)
        _write(os.path.join(lock, "owner"), {"pid": os.getpid(), "host": machine_lock.socket.gethostname(),
                                             "worker": "1", "task": "busy", "since": "x"})
        out = io.StringIO()
        rc = machine_lock.acquire(lock, "2", "t", wait_s=0, poll_s=0.01, out=out)
        self.assertEqual(rc, machine_lock.EXIT_TIMEOUT)
        self.assertIn("waiting for pid", out.getvalue())

    def test_run_returns_command_status_and_frees_lock(self):
        lock = os.path.join(self.tmp, "judge.lock")
        r = subprocess.run([PY, os.path.join(HERE, "machine_lock.py"), "run", "--lock", lock,
                            "--", PY, "-c", "import sys; sys.exit(3)"], capture_output=True)
        self.assertEqual(r.returncode, 3)
        self.assertFalse(os.path.exists(lock))


class TestRequire(TempCase):
    def test_missing_at_paths(self):
        g = {"effects": [{"kind": "http", "headers": {}}, {"kind": "http"}, {"kind": "bus"}]}
        self.assertEqual(parity_diff.missing_at(g, "effects[kind=http].headers"), 1)
        self.assertEqual(parity_diff.missing_at(g, "effects[].headers"), 2)
        self.assertEqual(parity_diff.missing_at(g, "cache_writes"), 1)
        self.assertEqual(parity_diff.missing_at({}, "effects[kind=http].headers"), 0)

    def test_bad_spec_rejected(self):
        with self.assertRaises(ValueError):
            parity_diff.parse_requires(["no-equals"])

    def test_verdict_tail_and_fail_flag(self):
        old, new = os.path.join(self.tmp, "o"), os.path.join(self.tmp, "n")
        for d in (old, new):
            _write(os.path.join(d, "a.json"), {"effects": [{"kind": "http"}]})
        args = [PY, os.path.join(HERE, "parity_diff.py"), old, new,
                "--require", "http-headers=effects[kind=http].headers"]
        r = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNJUDGED: http-headers 1", r.stdout)
        self.assertIn("0 diff(s), unjudged: 1)", r.stdout)
        r = subprocess.run(args + ["--fail-on-unjudged"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        # Without --require the verdict line is unchanged.
        r = subprocess.run(args[:4], capture_output=True, text=True)
        self.assertTrue(r.stdout.strip().endswith("PARITY_RESULT: PASS (1 scenario(s), 0 diff(s))"))


FAKE_CAPTURE = r'''
import json, os, sys
golden, suite, out, lane, broken = sys.argv[1:6]
if suite == broken:
    sys.exit(4)
for f in sorted(os.listdir(golden)):
    if f.startswith(suite + "__"):
        with open(os.path.join(golden, f)) as fh:
            v = json.load(fh)
        v["topic"] = v["topic"].replace("-l{lane}", "-l" + lane)
        with open(os.path.join(out, f), "w") as fh:
            json.dump(v, fh)
'''


class TestShard(TempCase):
    def _golden(self):
        g = os.path.join(self.tmp, "golden")
        for s, n in (("pay", 3), ("fees", 2), ("kyc", 1)):
            for i in range(n):
                _write(os.path.join(g, "%s__s%d.json" % (s, i)), {"id": i, "topic": "evt-l{lane}"})
        cap = os.path.join(self.tmp, "cap.py")
        _write(cap, FAKE_CAPTURE)
        return g, cap

    def _run(self, g, cap, broken="none", *extra):
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            rc = e2e_shard.main([
                "--golden", g, "--out-dir", os.path.join(self.tmp, "runs"),
                "--capture-cmd", "%s %s %s {suite} {out} {lane} %s" % (PY, cap, g, broken),
                "--lanes", "2", "--no-lock", "--lane-echo=-l{lane}", *extra])
        finally:
            sys.stdout = sys_stdout
        return rc, out.getvalue()

    def test_pack_longest_first(self):
        plan = e2e_shard.pack({"a": 10, "b": 6, "c": 5, "d": 1}, 2)
        self.assertEqual(plan, [["a", "d"], ["b", "c"]])

    def test_weights_fall_back_to_count_times_mean_rate(self):
        w = e2e_shard.weights({"a": 10, "b": 4}, {"a": 20.0})
        self.assertEqual(w, {"a": 20.0, "b": 8.0})
        self.assertEqual(e2e_shard.weights({"a": 2}, {}), {"a": 1.5})

    def test_echo_replacement_is_exact_per_lane(self):
        pairs = e2e_shard.echo_pairs(["_JUDGE_W{lane}_"], 2, 0)
        self.assertEqual(e2e_shard._replace_strings("_JUDGE_W2_x", pairs), "_JUDGE_W{lane}_x")
        self.assertEqual(e2e_shard._replace_strings("_JUDGE_W20_x", pairs), "_JUDGE_W20_x")

    def test_clean_sharded_run_passes_and_records_timings(self):
        g, cap = self._golden()
        rc, out = self._run(g, cap)
        self.assertEqual(rc, 0, out)
        self.assertIn("PARITY_RESULT: PASS (6 scenario(s), 0 diff(s))", out)
        timings = _read(os.path.join(self.tmp, "runs", "suite_seconds.json"))
        self.assertEqual(sorted(timings), ["fees", "kyc", "pay"])

    def test_failed_lane_capture_is_missing_not_skipped(self):
        g, cap = self._golden()
        rc, out = self._run(g, cap, "fees")
        self.assertEqual(rc, 1)
        self.assertIn("fees__s0.json — scenario missing in new", out)
        self.assertIn("PARITY_RESULT: FAIL", out)

    def test_selected_suites_judged_against_their_goldens_only(self):
        g, cap = self._golden()
        rc, out = self._run(g, cap, "none", "--suite", "kyc")
        self.assertEqual(rc, 0, out)
        self.assertIn("PARITY_RESULT: PASS (1 scenario(s)", out)

    def test_trust_requires_exact_match_with_serial(self):
        serial = {"scenarios": 6, "diffs": 1, "unjudged": 0, "failing": ["a.json"]}
        ok, _ = e2e_shard.trust(dict(serial), serial)
        self.assertTrue(ok)
        sharded = dict(serial, diffs=2, failing=["a.json", "b.json"])
        ok, lines = e2e_shard.trust(sharded, serial)
        self.assertFalse(ok)
        self.assertTrue(any(l.startswith("SHARD_ONLY: b.json") for l in lines))


class TestImpact(TempCase):
    def _repo(self):
        r = self.tmp
        for rel, body in {
            "internal/app/app.go": "wiring", "internal/pay/h.go": "p",
            "internal/fees/h.go": "f", "internal/money/m.go": "m",
            "internal/orphan/o.go": "o", "go.mod": "mod", "README.md": "x",
            "judge/golden/pay__a.json": "{}", "judge/golden/fees__a.json": "{}",
        }.items():
            _write(os.path.join(r, rel), body)
        cfg = {
            "manifest": "out/manifest.json",
            "track": ["internal/*", "go.mod", "judge/*", "README.md"],
            "full_on": ["internal/app/", "go.mod"],
            "suite_files": [r"^judge/golden/(?P<suite>[a-z]+)__"],
            "units": [r"^(?P<unit>internal/[^/]+)/[^/]+\.go$"],
            "golden": "judge/golden",
            "deps_cmd": "deps {suite}",
        }
        _write(os.path.join(r, "impact.json"), cfg)
        return impact.load_config(os.path.join(r, "impact.json"))

    DEPS = {"deps pay": {"internal/pay", "internal/money"}, "deps fees": {"internal/fees"}}

    def _sel(self, cfg):
        return impact.select(cfg, run_cmd=lambda cmd, root: self.DEPS.get(cmd))

    def _green(self):
        st = os.path.join(self.tmp, "status.json")
        _write(st, {"verdict": "PARITY_RESULT: PASS (2 scenario(s), 0 diff(s))", "suites": "all"})
        return st

    def test_no_manifest_is_all(self):
        self.assertEqual(self._sel(self._repo())[0], "ALL")

    def test_record_refuses_red_or_selected_runs(self):
        cfg = self._repo()
        bad = os.path.join(self.tmp, "bad.json")
        _write(bad, {"verdict": "PARITY_RESULT: FAIL (1 diff(s))", "suites": "all"})
        with self.assertRaises(ValueError):
            impact.record(cfg, bad)
        _write(bad, {"verdict": "PARITY_RESULT: PASS", "suites": ["pay"]})
        with self.assertRaises(ValueError):
            impact.record(cfg, bad)

    def test_selection_rules(self):
        cfg = self._repo()
        self.assertGreater(impact.record(cfg, self._green()), 0)
        self.assertEqual(self._sel(cfg), ([], []))
        _write(os.path.join(self.tmp, "internal/money/m.go"), "m2")
        self.assertEqual(self._sel(cfg)[0], ["pay"])
        _write(os.path.join(self.tmp, "judge/golden/fees__a.json"), '{"x":1}')
        self.assertEqual(self._sel(cfg)[0], ["fees", "pay"])
        _write(os.path.join(self.tmp, "internal/orphan/o.go"), "o2")
        sel, why = self._sel(cfg)
        self.assertEqual(sel, ["fees", "pay"])
        self.assertTrue(any("internal/orphan" in w for w in why))

    def test_fails_closed(self):
        cfg = self._repo()
        impact.record(cfg, self._green())
        _write(os.path.join(self.tmp, "internal/app/app.go"), "rewired")
        self.assertEqual(self._sel(cfg)[0], "ALL")
        impact.record(cfg, self._green())
        _write(os.path.join(self.tmp, "README.md"), "unmappable")
        self.assertEqual(self._sel(cfg)[0], "ALL")
        impact.record(cfg, self._green())
        _write(os.path.join(self.tmp, "judge/golden/newsuite__a.json"), "{}")
        self.assertEqual(self._sel(cfg)[0], ["newsuite"])  # a new suite selects itself
        cfg["suites"] = ["pay", "fees"]  # an explicit list: anything else is unknown
        self.assertEqual(self._sel(cfg)[0], "ALL")
        del cfg["suites"]
        impact.record(cfg, self._green())
        _write(os.path.join(self.tmp, "internal/fees/h.go"), "f2")
        sel, why = impact.select(cfg, run_cmd=lambda cmd, root: None)
        self.assertEqual(sel, "ALL")
        self.assertIn("deps_cmd failed", why[0])


class TestMutantSelect(TempCase):
    SRC = "import os\nRATE = 5\n\ndef fee(x):\n    if x > 0:\n        return x * RATE\n    return 0\n\ndef unused():\n    return 1\n"

    def _cov(self):
        return {"files": {"/stage/legacy/pay/fees.py": {
            "executed_lines": [1, 2, 4, 5, 6, 7, 9],
            "missing_lines": [10],
            "contexts": {"1": [""], "2": [""], "4": [""], "9": [""],
                         "5": ["pay__a", "pay__b"], "6": ["pay__a"], "7": ["pay__b"]}}}}

    def _run(self, mutants):
        _write(os.path.join(self.tmp, "src/pay/fees.py"), self.SRC)
        _write(os.path.join(self.tmp, "cov.json"), self._cov())
        _write(os.path.join(self.tmp, "m.json"), mutants)
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            rc = mutant_select.main(["--coverage", os.path.join(self.tmp, "cov.json"),
                                     "--mutants", os.path.join(self.tmp, "m.json"),
                                     "--src-root", os.path.join(self.tmp, "src"),
                                     "--plan", os.path.join(self.tmp, "plan.json")])
        finally:
            sys.stdout = sys_stdout
        return rc, out.getvalue()

    def test_subset_full_and_rejection(self):
        rc, out = self._run({
            "rate_body": {"file": "pay/fees.py", "old": "return x * RATE", "new": "return x"},
            "rate_const": ["pay/fees.py", "RATE = 5", "RATE = 6"],
            "dead": {"file": "pay/fees.py", "old": "return 1", "new": "return 2"},
        })
        plan = _read(os.path.join(self.tmp, "plan.json"))
        self.assertEqual(plan["rate_body"]["mode"], "subset")
        self.assertEqual(plan["rate_body"]["scenarios"], ["pay__a"])
        self.assertEqual(plan["rate_const"]["mode"], "full")
        self.assertEqual(plan["dead"]["mode"], "unreached")
        self.assertEqual(rc, 1)
        self.assertIn("MUTANT_SELECT: FAIL", out)

    def test_all_reachable_passes(self):
        rc, out = self._run({"guard": {"file": "pay/fees.py", "old": "if x > 0:", "new": "if x >= 0:"}})
        self.assertEqual(rc, 0, out)
        self.assertIn("MUTANT: guard subset 2 scenario(s)", out)

    def test_continuation_line_maps_to_statement_start(self):
        mode, scen = mutant_select.classify([8], {5: {"s1"}}, {5})
        self.assertEqual((mode, scen), ("subset", ["s1"]))

    def test_plain_map_format(self):
        _write(os.path.join(self.tmp, "plain.json"), {"pay/fees.py": {"6": ["pay__a"]}})
        ctx, measured = mutant_select.load_coverage(os.path.join(self.tmp, "plain.json"))
        self.assertEqual(ctx["pay/fees.py"][6], {"pay__a"})
        self.assertIsNone(measured["pay/fees.py"])
        # An unexecuted body line under an import-time def is unreached, not full.
        self.assertEqual(mutant_select.classify([7], {6: {""}}, None), ("unreached", []))

    def test_ambiguous_anchor_is_an_error(self):
        rc, out = self._run({"amb": {"file": "pay/fees.py", "old": "return", "new": "x"}})
        self.assertEqual(rc, 1)
        self.assertIn("anchor not found exactly once", out)


if __name__ == "__main__":
    unittest.main()
