#!/usr/bin/env python3
"""notes_harness.py — drive one isolated notes instance the way a client does. JSON out.

Run from the app root. Every command takes --instance; the instance's port comes from
verify_evidence.py's claim, its data store and pid file live under .verify-run/<instance>/.

    launch  --instance I             # starts app.py, waits for /health, prints {"port":..}
    doctor  --instance I [--record --verifier V]
    create  --instance I --title T --capture DIR   # POST /notes; request+response+rows
    list    --instance I
    stop    --instance I             # stops the pid this instance started
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER = os.path.join(HERE, "verify_evidence.py")
VERSION = "1.2.0"
READY_SECONDS = 10


def run_dir(instance):
    return os.path.join(".verify-run", instance)


def port_of(instance):
    out = subprocess.run([sys.executable, RECORDER, "port", "--instance", instance],
                         capture_output=True, text=True, check=True).stdout
    return int(out.split()[1])


def call(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def emit(doc, ok=True):
    print(json.dumps(doc, sort_keys=True))
    return 0 if ok else 1


def launch(a):
    d = run_dir(a.instance)
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    port = port_of(a.instance)
    log = open(os.path.join(d, "app.log"), "ab")
    p = subprocess.Popen([sys.executable, "app.py", "--port", str(port), "--data",
                          os.path.join(d, "data"), "--instance", a.instance],
                         stdout=log, stderr=log, start_new_session=True)
    with open(os.path.join(d, "pid"), "w") as f:
        f.write(str(p.pid))
    deadline = time.time() + READY_SECONDS
    while time.time() < deadline:
        try:
            status, body = call(port, "GET", "/health")
            if status == 200 and body.get("instance") == a.instance:
                return emit({"instance": a.instance, "port": port, "pid": p.pid, "ready": True})
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return emit({"instance": a.instance, "port": port, "ready": False}, ok=False)


def doctor(a):
    d = run_dir(a.instance)
    checks = {}
    try:
        with open(os.path.join(d, "pid")) as f:
            pid = int(f.read())
        os.kill(pid, 0)
        checks["process"] = "pass"
    except (OSError, ValueError):
        checks["process"] = "fail"
    port = port_of(a.instance)
    try:
        status, body = call(port, "GET", "/health")
    except OSError:
        status, body = None, {}
    checks["port"] = "pass" if status == 200 and body.get("port") == port else "fail"
    checks["instance"] = "pass" if body.get("instance") == a.instance else "fail"
    checks["version"] = "pass" if body.get("version") == VERSION else "fail"
    ok = all(v == "pass" for v in checks.values())
    if a.record:
        argv = [sys.executable, RECORDER, "doctor", "--instance", a.instance,
                "--ok" if ok else "--fail", "--verifier", a.verifier or "unknown"]
        for k, v in sorted(checks.items()):
            argv += ["--check", "%s=%s" % (k, v)]
        subprocess.run(argv, check=True, capture_output=True)
    return emit({"instance": a.instance, "ok": ok, "checks": checks}, ok)


def create(a):
    port = port_of(a.instance)
    status, body = call(port, "POST", "/notes", {"title": a.title})
    rows_path = os.path.join(run_dir(a.instance), "data", "notes.jsonl")
    rows = []
    if os.path.isfile(rows_path):
        with open(rows_path) as f:
            rows = [json.loads(x) for x in f if x.strip()]
    os.makedirs(a.capture, exist_ok=True)
    doc = {"request": {"method": "POST", "path": "/notes", "body": {"title": a.title}},
           "response": {"status": status, "body": body}, "rows": rows}
    with open(os.path.join(a.capture, "create.json"), "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    return emit(doc, status == 201 and any(r.get("title") == a.title for r in rows))


def list_notes(a):
    status, body = call(port_of(a.instance), "GET", "/notes")
    return emit({"status": status, "body": body}, status == 200)


def stop(a):
    d = run_dir(a.instance)
    try:
        with open(os.path.join(d, "pid")) as f:
            pid = int(f.read())
        os.killpg(pid, signal.SIGTERM)
        stopped = True
    except (OSError, ValueError):
        stopped = False
    return emit({"instance": a.instance, "stopped": stopped})


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("launch", "doctor", "create", "list", "stop"):
        s = sub.add_parser(name)
        s.add_argument("--instance", required=True)
        if name == "doctor":
            s.add_argument("--record", action="store_true")
            s.add_argument("--verifier")
        if name == "create":
            s.add_argument("--title", required=True)
            s.add_argument("--capture", required=True)
    a = p.parse_args()
    return {"launch": launch, "doctor": doctor, "create": create, "list": list_notes,
            "stop": stop}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
