#!/usr/bin/env python3
"""Collect each run's artifacts, scrub arm identity, re-key to an opaque hash.

A judge must be able to infer the scenario (it is in the content) but never the
arm. So variant paths, run ids and any 'variant-p/w' string are scrubbed before
the packet is written.
"""
import hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(ROOT, "runs")
PACKETS = os.path.join(ROOT, "packets")


def scrub(text, rid):
    text = text or ""
    text = text.replace(ROOT, "<harness>")
    text = re.sub(r"variant-[pw]", "the-tooling", text)
    text = re.sub(r"\bs[123]-[pw]-t[12]\b", "<run>", text)
    text = re.sub(r"/runs/<run>/", "/<run>/", text)
    return text


def dependency_docs(bundle):
    deps = os.path.join(bundle, "dependencies")
    out = []
    if not os.path.isdir(deps):
        return out
    for name in sorted(os.listdir(deps)):
        if name.endswith(".md") and name != "index.md":
            with open(os.path.join(deps, name), encoding="utf-8") as fh:
                out.append((name, fh.read()))
    return out


def other_written(bundle):
    """Teams and capabilities created during the run — for the no-fabrication check."""
    found = []
    teams = os.path.join(bundle, "teams")
    for dirpath, _, files in os.walk(teams):
        for name in sorted(files):
            if name.endswith(".md") and name != "index.md":
                rel = os.path.relpath(os.path.join(dirpath, name), bundle)
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    found.append((rel, fh.read()))
    return found


def main():
    os.makedirs(PACKETS, exist_ok=True)
    mapping = json.load(open(os.path.join(ROOT, "mapping.json")))
    index = {}
    for rid, meta in mapping["runs"].items():
        rdir = os.path.join(RUNS, rid)
        bundle = meta["bundle"]
        key = "artifact_" + hashlib.sha1(rid.encode()).hexdigest()[:10]
        transcript = ""
        tpath = os.path.join(rdir, "transcript.md")
        if os.path.isfile(tpath):
            transcript = open(tpath, encoding="utf-8").read()
        report = ""
        rpath = os.path.join(rdir, "agent_report.md")
        if os.path.isfile(rpath):
            report = open(rpath, encoding="utf-8").read()

        parts = ["# Artifact %s\n" % key,
                 "\n## 1. The interview transcript (every question the interviewer asked "
                 "the engineer, and the answer)\n\n",
                 transcript.strip() or "_(no questions were asked)_", "\n\n"]
        deps = dependency_docs(bundle)
        parts.append("## 2. Dependency documents written to the catalog\n\n")
        if deps:
            for name, body in deps:
                parts.append("### dependencies/%s\n\n```yaml\n%s\n```\n\n" % (name, body.strip()))
        else:
            parts.append("_(none were written)_\n\n")
        parts.append("## 3. Team and capability documents in the catalog afterwards\n\n")
        for rel, body in other_written(bundle):
            head = body.split("---")[1] if body.startswith("---") else body[:400]
            parts.append("### %s\n\n```yaml\n%s\n```\n\n" % (rel, head.strip()))
        parts.append("## 4. The interviewer's closing report\n\n")
        parts.append(report.strip() or "_(none recorded)_")
        parts.append("\n")

        packet = scrub("".join(parts), rid)
        with open(os.path.join(PACKETS, key + ".md"), "w", encoding="utf-8") as fh:
            fh.write(packet)
        index[key] = {"run": rid, "arm": meta["arm"], "tier": meta["tier"],
                      "scenario": meta["scenario"],
                      "questions_asked": transcript.count("**Interviewer:**")}
    with open(os.path.join(ROOT, "packet_index.json"), "w") as fh:
        json.dump(index, fh, indent=2)
    for key, meta in sorted(index.items(), key=lambda kv: kv[1]["run"]):
        print(f"{meta['run']:>10}  {key}  questions={meta['questions_asked']}")


if __name__ == "__main__":
    main()
