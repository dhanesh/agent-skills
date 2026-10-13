#!/usr/bin/env python3
"""capacity_probe.py — how many judge lanes this machine can run at once.

Decide serial vs parallel from measurements, not hope. The judge's stateful
services (database, cache, message bus, document DB, object store) usually
run inside a container-runtime VM, not on the host, so the VM is the scarce
resource. A 10-core laptop with a 4-CPU / 6 GiB VM can run about 2 lanes;
sizing from host cores alone over-commits the VM and crashes a service under
load.

    python3 capacity_probe.py                       # probe, print the verdict
    python3 capacity_probe.py --json                # the same, as JSON
    python3 capacity_probe.py --scenarios 15000 --secs-per-scenario 0.7 \\
        --mutants 9 --reaching 120                  # add wall-clock estimates

Formula (each term is a ceiling, the smallest one wins):

    lanes = min(VM CPUs - 1, VM GiB / --gib-per-lane, host cores / 2,
                --max-lanes), at least 1

When free disk is below --min-free-gib, or the VM cannot be measured, the
verdict is 1 lane (serial) and the reason says why. The probe never guesses
upward. Override any measurement with --host-cpus, --vm-cpus, --vm-gib or
--free-disk-gib (for tests, or when the probe reads the wrong runtime).

Probes, in order: os.cpu_count() for the host; `docker info` (NCPU,
MemTotal: what the containers actually get), then `colima list --json` for
the VM; shutil.disk_usage() for free disk. When no container runtime answers,
the services run on the host, so the host's figures stand in for the VM's.

Output: CAPACITY: lines, ESTIMATE: lines when asked, and a final
"LANES: <n> (<serial|parallel>: <limiting reason>)" line. Exit 0.
Stdlib-only, offline (it only runs local CLIs), deterministic given its inputs.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

GIB = 2 ** 30


def compute_lanes(host_cpus, vm_cpus, vm_gib, free_disk_gib,
                  gib_per_lane=1.5, max_lanes=6, min_free_gib=10.0):
    """Pure sizing rule. Returns (lanes, reason); reason names the binding limit."""
    if vm_cpus is None or vm_gib is None:
        return 1, "serial: container VM not measured, refusing to guess upward"
    if free_disk_gib is not None and free_disk_gib < min_free_gib:
        return 1, ("serial: free disk %.1f GiB < %.1f GiB floor"
                   % (free_disk_gib, min_free_gib))
    ceilings = [
        (vm_cpus - 1, "VM CPUs - 1 (%d - 1)" % vm_cpus),
        (int(vm_gib / gib_per_lane),
         "VM memory %.1f GiB / %.1f GiB per lane" % (vm_gib, gib_per_lane)),
        (host_cpus // 2, "host cores / 2 (%d / 2)" % host_cpus),
        (max_lanes, "max lanes %d" % max_lanes),
    ]
    value, why = min(ceilings, key=lambda c: c[0])
    lanes = max(1, value)
    mode = "parallel" if lanes > 1 else "serial"
    return lanes, "%s: limited by %s" % (mode, why)


def estimate(lanes, scenarios=None, secs_per_scenario=None,
             mutants=None, reaching=None):
    """Wall-clock estimates in seconds: e2e wave and mutation proofs."""
    out = {}
    if scenarios and secs_per_scenario:
        out["e2e_serial_s"] = scenarios * secs_per_scenario
        out["e2e_wall_s"] = scenarios * secs_per_scenario / lanes
    if mutants and reaching and secs_per_scenario:
        # Proofs share one machine-wide lock, so they do not divide by lanes.
        out["proof_wall_s"] = mutants * reaching * secs_per_scenario
        if scenarios:
            out["proof_full_suite_s"] = mutants * scenarios * secs_per_scenario
    return out


def _run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def probe_vm():
    """Return (cpus, gib, source) for the container runtime, or (None, None, why)."""
    out = _run(["docker", "info", "--format", "{{json .}}"])
    if out:
        try:
            d = json.loads(out)
            if d.get("NCPU") and d.get("MemTotal"):
                return int(d["NCPU"]), int(d["MemTotal"]) / GIB, "docker info"
        except (ValueError, TypeError):
            pass
    out = _run(["colima", "list", "--json"])
    if out:
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("status") == "Running" and d.get("cpus"):
                mem = int(d.get("memory") or 0)
                return int(d["cpus"]), mem / GIB if mem else None, "colima list"
    return None, None, "no container runtime answered"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Size judge lanes from the scarcest resource.")
    ap.add_argument("--host-cpus", type=int)
    ap.add_argument("--vm-cpus", type=int)
    ap.add_argument("--vm-gib", type=float)
    ap.add_argument("--free-disk-gib", type=float)
    ap.add_argument("--no-vm", action="store_true",
                    help="services run on the host: use host figures for the VM")
    ap.add_argument("--disk-path", default=".", help="filesystem the run writes to")
    ap.add_argument("--gib-per-lane", type=float, default=1.5)
    ap.add_argument("--max-lanes", type=int, default=6)
    ap.add_argument("--min-free-gib", type=float, default=10.0)
    ap.add_argument("--scenarios", type=int)
    ap.add_argument("--secs-per-scenario", type=float)
    ap.add_argument("--mutants", type=int)
    ap.add_argument("--reaching", type=int,
                    help="mean scenarios that reach a mutated line (see mutant_select.py)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    host = a.host_cpus or os.cpu_count() or 1
    source = "flags"
    vm_cpus, vm_gib = a.vm_cpus, a.vm_gib
    if a.no_vm:
        vm_cpus = vm_cpus or host
        vm_gib = vm_gib or _host_gib()
        source = "host (no VM)"
    elif vm_cpus is None or vm_gib is None:
        p_cpus, p_gib, source = probe_vm()
        if p_cpus is None and source == "no container runtime answered":
            # Nothing containerised: the services share the host.
            p_cpus, p_gib, source = host, _host_gib(), "host (no container runtime)"
        vm_cpus = vm_cpus if vm_cpus is not None else p_cpus
        vm_gib = vm_gib if vm_gib is not None else p_gib
    disk = a.free_disk_gib
    if disk is None:
        try:
            disk = shutil.disk_usage(a.disk_path).free / GIB
        except OSError:
            disk = None

    lanes, reason = compute_lanes(host, vm_cpus, vm_gib, disk,
                                  a.gib_per_lane, a.max_lanes, a.min_free_gib)
    est = estimate(lanes, a.scenarios, a.secs_per_scenario, a.mutants, a.reaching)
    result = {"host_cpus": host, "vm_cpus": vm_cpus, "vm_gib": vm_gib,
              "vm_source": source, "free_disk_gib": disk, "lanes": lanes,
              "reason": reason, "estimate": est}
    if a.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print("CAPACITY: host cores %d" % host)
    print("CAPACITY: VM %s CPUs, %s GiB (from %s)" % (
        vm_cpus if vm_cpus is not None else "?",
        "%.1f" % vm_gib if vm_gib is not None else "?", source))
    print("CAPACITY: free disk %s GiB" % ("%.1f" % disk if disk is not None else "?"))
    for k, v in est.items():
        print("ESTIMATE: %s %.0f (%.1f h)" % (k, v, v / 3600))
    print("LANES: %d (%s)" % (lanes, reason))
    return 0


def _host_gib():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIB
    except (ValueError, OSError, AttributeError):
        return None


if __name__ == "__main__":
    sys.exit(main())
