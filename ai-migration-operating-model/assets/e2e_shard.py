#!/usr/bin/env python3
"""e2e_shard.py — replay the golden corpus against the new system in parallel lanes.

A serial e2e wave over a large corpus is the slowest step of a migration loop
(about 3 h for ~15k scenarios at ~0.7 s each). This runner splits the suites
over N isolated lanes and still judges the result as ONE corpus.

    python3 e2e_shard.py --golden migration/judge/golden --out-dir out/e2e \\
        --setup-cmd    'JUDGE_LANE={lane} ./judge.sh setup' \\
        --capture-cmd  'JUDGE_LANE={lane} ./judge.sh capture --base-url http://127.0.0.1:{port} --suite {suite} --out {out}' \\
        --teardown-cmd 'JUDGE_LANE={lane} ./judge.sh down' \\
        --port-base 18000 --port-stride 10 --lock out/judge.lock

Contract (references/control-pack.md, "Judge isolation contract"):
- Golden files are named <suite><sep><scenario>.json (default sep "__").
- Every command template may use {lane}, {port} (= port-base + stride*lane),
  {suite}, {out} and {run}. The lane id must key EVERY stateful resource the
  command touches; this runner cannot see inside it.
- Lane ids start at --first-lane (default 20), clear of development workers.

What it does:
1. Packs suites into lanes, longest first. A suite's weight is its last
   measured seconds (from <out-dir>/suite_seconds.json), else its scenario count
   x the mean measured s/scenario (0.75 s when nothing is measured yet).
2. Takes the machine-wide lock (machine_lock.py) unless --no-lock: a sharded
   run and a mutation proof together starve the container VM.
3. Runs every lane's setup ONE AT A TIME (setups often share tool state, for
   example one virtualenv sync), then the captures in parallel. Each lane runs
   its suites in order and times each one. Teardown always runs.
4. Merges every lane's capture into one directory and judges it with ONE
   parity_diff against the whole golden directory (or, with --suite, against
   exactly the selected suites). A suite that no lane captured shows up as
   "scenario missing in new"; it never passes silently.
5. --lane-echo TEMPLATE (repeatable) names values a lane echoes into its
   outputs, such as a topic suffix "-l{lane}" or a sink "127.0.0.1:{port}".
   Each lane's outputs are rewritten by exact replacement of THAT lane's
   rendered values with the template text, so goldens stay lane-independent.
   --golden-lane N applies the same rewrite to a normalised copy of the goldens.
6. Writes <run>/status.json: verdict, scenario, diff and unjudged counts, the
   failing scenarios. With --expect <status.json of a serial run on the same
   tree>, prints SHARD_TRUST: PASS only when the counts match exactly, and
   lists diffs seen only in this run: re-run those serially before calling them
   regressions (fixed sleeps for async side effects can miss under load).

Never deletes anything: every run writes a new directory under --out-dir.
Exit 0 iff PARITY_RESULT: PASS (and SHARD_TRUST: PASS when --expect is given).
Stdlib-only.
"""

import argparse
import concurrent.futures as cf
import datetime
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capacity_probe  # noqa: E402
import machine_lock  # noqa: E402
import parity_diff  # noqa: E402

DEFAULT_RATE = 0.75


def suite_counts(golden, sep, only=None):
    counts = {}
    for name in parity_diff._scenario_names(golden):
        if sep not in name:
            continue
        s = name.split(sep, 1)[0]
        counts[s] = counts.get(s, 0) + 1
    if only:
        unknown = sorted(set(only) - set(counts))
        if unknown:
            raise SystemExit("E2E_SHARD: unknown suite(s): %s" % " ".join(unknown))
        counts = {s: counts[s] for s in only}
    return counts


def weights(counts, secs):
    measured = [s for s in secs if s in counts]
    n = sum(counts[s] for s in measured)
    rate = (sum(secs[s] for s in measured) / n) if n else DEFAULT_RATE
    return {s: float(secs.get(s, c * rate)) for s, c in counts.items()}


def pack(w, lanes):
    """Longest-first greedy bin packing; returns non-empty suite lists."""
    bins = [[0.0, []] for _ in range(max(1, lanes))]
    for s, x in sorted(w.items(), key=lambda kv: (-kv[1], kv[0])):
        b = min(bins, key=lambda b: b[0])
        b[0] += x
        b[1].append(s)
    return [b[1] for b in bins if b[1]]


def render(tpl, **kw):
    return tpl.format(**{k: (shlex.quote(str(v)) if k in ("out", "suite") else v)
                         for k, v in kw.items()})


def _sh(cmd, log):
    with open(log, "a", encoding="utf-8") as f:
        f.write("$ %s\n" % cmd)
        f.flush()
        return subprocess.call(cmd, shell=True, stdout=f, stderr=subprocess.STDOUT)


def _replace_strings(v, pairs):
    if isinstance(v, str):
        for old, new in pairs:
            v = v.replace(old, new)
        return v
    if isinstance(v, list):
        return [_replace_strings(x, pairs) for x in v]
    if isinstance(v, dict):
        return {_replace_strings(k, pairs): _replace_strings(x, pairs) for k, x in v.items()}
    return v


def echo_pairs(echoes, lane, port):
    """(rendered value, template text) pairs, longest value first."""
    pairs = [(e.format(lane=lane, port=port), e) for e in echoes]
    return sorted(pairs, key=lambda p: -len(p[0]))


def copy_normalised(src, dst, pairs):
    """Copy one JSON file, rewriting lane echoes. Unparseable files copy as-is
    so the differ reports them instead of the merge hiding them."""
    if not pairs:
        shutil.copy2(src, dst)
        return
    try:
        with open(src, encoding="utf-8") as f:
            v = json.load(f)
    except ValueError:
        shutil.copy2(src, dst)
        return
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(_replace_strings(v, pairs), f, sort_keys=True)


def _atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def trust(status, expect):
    """Compare a sharded run with a serial reference; returns (ok, lines)."""
    lines, ok = [], True
    for key in ("scenarios", "diffs", "unjudged"):
        if status.get(key) != expect.get(key):
            ok = False
            lines.append("SHARD_TRUST: %s differs (sharded=%s, serial=%s)"
                         % (key, status.get(key), expect.get(key)))
    if status.get("tree_id") and expect.get("tree_id") \
            and status["tree_id"] != expect["tree_id"]:
        ok = False
        lines.append("SHARD_TRUST: different trees (%s vs %s)"
                     % (status["tree_id"], expect["tree_id"]))
    only = sorted(set(status.get("failing", [])) - set(expect.get("failing", [])))
    for s in only:
        lines.append("SHARD_ONLY: %s (re-run serially before calling it a regression)" % s)
    if only:
        ok = False
    lines.append("SHARD_TRUST: %s" % ("PASS" if ok else "FAIL"))
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parallel golden-corpus replay, judged as one corpus.")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--capture-cmd", required=True)
    ap.add_argument("--setup-cmd", default="")
    ap.add_argument("--teardown-cmd", default="")
    ap.add_argument("--lanes", type=int, help="default: capacity_probe.py's verdict")
    ap.add_argument("--first-lane", type=int, default=20)
    ap.add_argument("--port-base", type=int, default=18000)
    ap.add_argument("--port-stride", type=int, default=10)
    ap.add_argument("--sep", default="__")
    ap.add_argument("--suite", action="append", help="only these suites (impact.py output)")
    ap.add_argument("--lock", help="machine-wide lock dir shared with mutation proofs")
    ap.add_argument("--no-lock", action="store_true", help="the caller already holds the lock")
    ap.add_argument("--lane-echo", action="append", default=[])
    ap.add_argument("--golden-lane", type=int, help="lane id the goldens were captured with")
    ap.add_argument("--ignore", default="")
    ap.add_argument("--tolerance", type=float, default=0.0)
    ap.add_argument("--require", action="append", default=[])
    ap.add_argument("--tree-id", default="", help="for example a commit or manifest hash")
    ap.add_argument("--expect", help="status.json of a serial run on the same tree")
    a = ap.parse_args(argv)

    if not a.no_lock and not a.lock:
        ap.error("--lock is required unless --no-lock (the caller holds it)")
    lanes = a.lanes
    if lanes is None:
        lanes, why = capacity_probe.compute_lanes(*_probe())
        print("E2E_SHARD: probe says %d lane(s) (%s)" % (lanes, why))
    if lanes < 1:
        ap.error("--lanes must be at least 1")
    requires = parity_diff.parse_requires(a.require)

    os.makedirs(a.out_dir, exist_ok=True)
    timings_path = os.path.join(a.out_dir, "suite_seconds.json")
    run = "%s-%d" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"), os.getpid())
    run_dir = os.path.join(a.out_dir, run)
    os.makedirs(run_dir)
    counts = suite_counts(a.golden, a.sep, a.suite)
    w = weights(counts, _load_json(timings_path, {}))
    plan = pack(w, lanes)
    ids = [a.first_lane + i for i in range(len(plan))]
    port = {n: a.port_base + a.port_stride * n for n in ids}
    _atomic_json(os.path.join(run_dir, "plan.json"), {
        str(n): {"suites": p, "port": port[n], "est_s": round(sum(w[s] for s in p))}
        for n, p in zip(ids, plan)})
    print("E2E_SHARD: run %s, %d scenario(s) in %d suite(s), %d lane(s)"
          % (run, sum(counts.values()), len(counts), len(plan)), flush=True)

    if not a.no_lock:
        rc = machine_lock.acquire(a.lock, worker="e2e", task="e2e-shard %s" % run)
        if rc:
            return rc
    t0 = time.monotonic()
    secs, failed_setup = {}, []

    def run_lane(n, suites):
        got = {}
        for s in suites:
            out = os.path.join(run_dir, "lane-%d" % n, s)
            os.makedirs(out, exist_ok=True)
            t = time.monotonic()
            rc = _sh(render(a.capture_cmd, lane=n, port=port[n], suite=s, out=out, run=run),
                     os.path.join(run_dir, "lane-%d.log" % n))
            got[s] = round(time.monotonic() - t, 1)
            print("LANE %d: %s %ss%s" % (n, s, got[s], "" if rc == 0 else
                                          " (capture exited %d: judged as missing)" % rc),
                  flush=True)
        return got

    try:
        live = []
        for n, p in zip(ids, plan):  # sequential on purpose: shared tool state
            if a.setup_cmd:
                rc = _sh(render(a.setup_cmd, lane=n, port=port[n], run=run),
                         os.path.join(run_dir, "lane-%d.log" % n))
                if rc != 0:
                    failed_setup.append(n)
                    print("LANE %d: setup exited %d; its suites are judged as missing" % (n, rc))
                    continue
            live.append((n, p))
        with cf.ThreadPoolExecutor(max(1, len(live))) as ex:
            for got in ex.map(lambda np: run_lane(*np), live):
                secs.update(got)
    finally:
        if a.teardown_cmd:
            for n in ids:
                _sh(render(a.teardown_cmd, lane=n, port=port[n], run=run),
                    os.path.join(run_dir, "lane-%d.log" % n))
        if not a.no_lock:
            machine_lock.release(a.lock)
    wall = round(time.monotonic() - t0)

    prev = _load_json(timings_path, {})
    prev.update(secs)
    _atomic_json(timings_path, prev)

    merged = os.path.join(run_dir, "merged")
    os.makedirs(merged)
    for n in ids:
        pairs = echo_pairs(a.lane_echo, n, port[n])
        lane_root = os.path.join(run_dir, "lane-%d" % n)
        if not os.path.isdir(lane_root):
            continue
        for s in sorted(os.listdir(lane_root)):
            d = os.path.join(lane_root, s)
            for f in parity_diff._scenario_names(d) if os.path.isdir(d) else []:
                copy_normalised(os.path.join(d, f), os.path.join(merged, f), pairs)

    golden = a.golden
    gpairs = []
    if a.golden_lane is not None:
        gpairs = echo_pairs(a.lane_echo, a.golden_lane,
                            a.port_base + a.port_stride * a.golden_lane)
    if a.suite or gpairs:
        golden = os.path.join(run_dir, "golden-judged")
        os.makedirs(golden)
        keep = set(counts)
        for f in parity_diff._scenario_names(a.golden):
            if f.split(a.sep, 1)[0] in keep:
                copy_normalised(os.path.join(a.golden, f), os.path.join(golden, f), gpairs)

    buf = io.StringIO()
    ignore = frozenset(k for k in a.ignore.split(",") if k)
    scenarios, diffs = parity_diff.run(golden, merged, ignore, a.tolerance, out=buf)
    unjudged = sum(parity_diff.count_unjudged(golden, requires).values()) if requires else None
    log = buf.getvalue()
    failing = sorted(line.split(" ", 2)[1] for line in log.splitlines()
                     if line.startswith("SCENARIO:") and line.endswith(")") and "FAIL" in line)
    tail = "" if unjudged is None else ", unjudged: %d" % unjudged
    verdict = ("PARITY_RESULT: PASS (%d scenario(s), 0 diff(s)%s)" % (scenarios, tail)
               if diffs == 0 else
               "PARITY_RESULT: FAIL (%d diff(s) across %d scenario(s)%s)" % (diffs, scenarios, tail))
    with open(os.path.join(run_dir, "diff.log"), "w", encoding="utf-8") as f:
        f.write(log + verdict + "\n")
    for line in log.splitlines():
        if line.startswith("DIFF:"):
            print(line)
    print(verdict)
    print("E2E_SHARD: wall %ds; %s" % (wall, ", ".join(
        "lane %d=%ds" % (n, round(sum(secs.get(s, 0) for s in p))) for n, p in zip(ids, plan))))
    status = {"verdict": verdict, "scenarios": scenarios, "diffs": diffs, "unjudged": unjudged,
              "failing": failing, "lanes": len(plan), "wall_s": wall, "run": run,
              "suites": sorted(counts) if a.suite else "all", "tree_id": a.tree_id,
              "failed_setup": failed_setup}
    _atomic_json(os.path.join(run_dir, "status.json"), status)
    ok = diffs == 0
    if a.expect:
        t_ok, lines = trust(status, _load_json(a.expect, {}))
        for line in lines:
            print(line)
        ok = ok and t_ok
    return 0 if ok else 1


def _probe():
    host = os.cpu_count() or 1
    cpus, gib, src = capacity_probe.probe_vm()
    if cpus is None and src == "no container runtime answered":
        cpus, gib = host, capacity_probe._host_gib()
    try:
        disk = shutil.disk_usage(".").free / capacity_probe.GIB
    except OSError:
        disk = None
    return host, cpus, gib, disk


if __name__ == "__main__":
    sys.exit(main())
