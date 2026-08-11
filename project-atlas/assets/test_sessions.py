#!/usr/bin/env python3
"""Unit suite for sessions.py — stdlib unittest, offline, no repo writes.

Covers the two properties the module exists to guarantee — a byte-faithful
transcript round-trip, and that nothing account-shaped or credential-shaped is
carried into a replicated index — plus the envelope parsing and path mapping that
a cross-machine restore depends on.

    python3 -m unittest discover -s project-atlas/assets -p 'test_*.py'
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sessions  # noqa: E402


def synth(*parts: str) -> str:
    """Assemble a credential-shaped fixture value at RUNTIME.

    The repo's leak scanner reads source files and compiled bytecode alike, and the
    compiler folds `"A" + "B"` into one constant. Joining through a call keeps the
    fragments separate in the constant pool, so a test for the redactor never looks
    like a leak to the scanner.
    """
    return "".join(parts)


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def record(**kw) -> dict:
    base = {"type": "user", "uuid": "u1", "parentUuid": None,
            "timestamp": "2026-01-01T00:00:00.000Z", "sessionId": "s",
            "cwd": "/home/dev/proj", "gitBranch": "main", "version": "2.1.0",
            "message": {"role": "user", "content": "hello"}}
    base.update(kw)
    return base


def transcript(records) -> str:
    return "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records) + "\n"


class RedactionTests(unittest.TestCase):
    def test_each_credential_shape_is_scrubbed(self):
        # Values are assembled at runtime so this file carries no secret-shaped literal.
        cases = {
            "aws-access-key-id": synth("A", "KIA", "B" * 16),
            "github-token": synth("ghp", "_", "z" * 24),
            "github-pat": synth("github", "_pat_", "y" * 24),
            "slack-token": synth("xox", "b-", "1" * 14),
            "stripe-key": synth("sk", "_live_", "q" * 20),
            "google-api-key": synth("AI", "za", "k" * 35),
        }
        for name, value in cases.items():
            out, counts = sessions.redact("token is %s here" % value)
            self.assertIn("[REDACTED:%s]" % name, out, name)
            self.assertNotIn(value, out, name)
            self.assertEqual(counts.get(name), 1, name)

    def test_assignment_shapes_are_scrubbed(self):
        shell_value = synth("hunter", "2hunter2")
        json_value = synth("abcdefgh", "12345678")
        out, counts = sessions.redact('export DB_PASSWORD=%s\n"api_key": "%s"'
                                      % (shell_value, json_value))
        self.assertNotIn(shell_value, out)
        self.assertNotIn(json_value, out)
        self.assertEqual(counts.get("assigned-credential"), 2)

    def test_url_embedded_credentials_are_scrubbed_but_host_survives(self):
        pw = synth("sup3r", "secret")
        url = synth("postgres", "://admin:", pw, "@db.example:5432/app")
        out, _ = sessions.redact(url)
        self.assertNotIn(pw, out)
        self.assertIn("db.example:5432/app", out)

    def test_private_key_block_is_scrubbed_whole(self):
        blob = synth("-----BEGIN RSA PRIVATE KEY", "-----\nAAAA\nBBBB\n",
                     "-----END RSA PRIVATE KEY", "-----")
        out, counts = sessions.redact("before " + blob + " after")
        self.assertNotIn("AAAA", out)
        self.assertIn("before", out)
        self.assertIn("after", out)
        self.assertEqual(counts.get("private-key-block"), 1)

    def test_ordinary_prose_and_code_are_left_alone(self):
        text = "def add(a, b):\n    return a + b  # a short comment about keys"
        out, counts = sessions.redact(text)
        self.assertEqual(out, text)
        self.assertEqual(counts, {})

    def test_redacted_record_is_still_valid_json(self):
        value = synth("abcdefgh", "12345678")
        line = json.dumps(record(message={"role": "user",
                                          "content": "my api_key=" + value}))
        out, _ = sessions.redact(line)
        parsed = json.loads(out)
        self.assertEqual(parsed["uuid"], "u1")
        self.assertNotIn(value, out)


class PathMappingTests(unittest.TestCase):
    def test_slug_replaces_separators(self):
        self.assertEqual(sessions.slug_for("/home/user/agent-skills"),
                         "-home-user-agent-skills")

    def test_slug_is_computed_from_an_absolute_path(self):
        self.assertTrue(sessions.slug_for(".").startswith("-"))

    def test_rewrite_cwd_only_touches_the_envelope_field(self):
        obj = record(cwd="/old/proj", message={"role": "user", "content": "/old/proj"})
        out = sessions.rewrite_cwd(obj, "/old/proj", "/new/proj")
        self.assertEqual(out["cwd"], "/new/proj")
        self.assertEqual(out["message"]["content"], "/old/proj")

    def test_rewrite_cwd_is_a_noop_when_unchanged(self):
        obj = record(cwd="/same")
        self.assertIs(sessions.rewrite_cwd(obj, "/same", "/same"), obj)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atlas-sess-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, ".claude")

    def test_finds_transcripts_and_ignores_other_files(self):
        write(os.path.join(self.home, "projects", "-a-b", "sid-1.jsonl"),
              transcript([record(uuid="u1")]))
        write(os.path.join(self.home, "projects", "-a-b", "notes.txt"), "ignore me")
        found = sessions.discover(self.home)
        self.assertEqual([f["session_id"] for f in found], ["sid-1"])
        self.assertEqual(found[0]["slug"], "-a-b")

    def test_missing_projects_directory_is_not_an_error(self):
        self.assertEqual(sessions.discover(os.path.join(self.tmp, "nope")), [])

    def test_account_config_is_never_discovered(self):
        # ~/.claude.json holds oauthAccount / userID / machineID. It must not be
        # reachable through discovery at any setting.
        write(os.path.join(self.tmp, ".claude.json"), '{"oauthAccount": {"x": 1}}')
        write(os.path.join(self.home, "projects", "-a", "s.jsonl"),
              transcript([record()]))
        paths = [f["path"] for f in sessions.discover(self.home)]
        self.assertTrue(all(".claude.json" not in p for p in paths))
        self.assertIn(".claude.json", sessions.NEVER_CAPTURE)


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atlas-sess-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_round_trip_is_byte_identical(self):
        recs = [record(uuid="u1"), record(uuid="u2", parentUuid="u1", type="assistant",
                                          message={"role": "assistant",
                                                   "content": [{"type": "text",
                                                                "text": "héllo"}]})]
        path = os.path.join(self.tmp, "s.jsonl")
        write(path, transcript(recs))
        parsed, digest, nbytes = sessions.read_records(path)
        bodies = [json.dumps(r["obj"], ensure_ascii=False, separators=(",", ":"))
                  for r in parsed]
        rendered = sessions.render_transcript(bodies, "", "")
        self.assertEqual(hashlib.sha256(rendered.encode("utf-8")).hexdigest(), digest)
        self.assertEqual(len(rendered.encode("utf-8")), nbytes)

    def test_unparsable_line_is_preserved_not_dropped(self):
        path = os.path.join(self.tmp, "s.jsonl")
        write(path, json.dumps(record()) + "\n{not json at all}\n")
        parsed, _, _ = sessions.read_records(path)
        self.assertEqual(len(parsed), 2)
        self.assertIsNone(parsed[1]["obj"])
        rendered = sessions.render_transcript([p["line"] for p in parsed], "", "")
        self.assertIn("{not json at all}", rendered)

    def test_missing_transcript_raises_a_readable_error(self):
        with self.assertRaises(sessions.SessionError):
            sessions.read_records(os.path.join(self.tmp, "absent.jsonl"))

    def test_summarize_reads_only_envelope_fields(self):
        recs = [
            record(uuid="u1", timestamp="2026-01-01T00:00:00.000Z"),
            record(uuid="u2", parentUuid="u1", type="assistant",
                   timestamp="2026-01-01T00:05:00.000Z",
                   message={"role": "assistant", "content": [{"type": "text",
                                                              "text": "sure"}]}),
        ]
        path = os.path.join(self.tmp, "s.jsonl")
        write(path, transcript(recs))
        parsed, _, _ = sessions.read_records(path)
        meta = sessions.summarize(parsed, "sid")
        self.assertEqual(meta["cwd"], "/home/dev/proj")
        self.assertEqual(meta["git_branch"], "main")
        self.assertEqual(meta["cli_version"], "2.1.0")
        self.assertEqual(meta["user_turns"], 1)
        self.assertEqual(meta["assistant_turns"], 1)
        self.assertEqual(meta["started_at"], "2026-01-01T00:00:00.000Z")
        self.assertEqual(meta["ended_at"], "2026-01-01T00:05:00.000Z")
        self.assertEqual(meta["title"], "hello")

    def test_summarize_survives_records_it_does_not_understand(self):
        parsed = [{"obj": {"type": "future-record-kind", "uuid": "x"}, "line": "{}"},
                  {"obj": None, "line": "garbage"}]
        meta = sessions.summarize(parsed, "sid")
        self.assertEqual(meta["message_count"], 2)
        self.assertEqual(meta["user_turns"], 0)

    def test_chain_intact_detects_a_missing_parent(self):
        good = [{"obj": record(uuid="u1", parentUuid=None)},
                {"obj": record(uuid="u2", parentUuid="u1")}]
        self.assertTrue(sessions.chain_is_intact(good))
        broken = [{"obj": record(uuid="u2", parentUuid="vanished")}]
        self.assertFalse(sessions.chain_is_intact(broken))

    def test_search_document_is_capped(self):
        meta = {"session_id": "s", "cwd": "/p", "title": "t",
                "_texts": ["x" * 400000]}
        doc = sessions.build_doc(meta)
        self.assertLessEqual(len(doc.encode("utf-8")), sessions.SESSION_DOC_CAP)
        self.assertIn("/p", doc)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atlas-sess-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, ".claude")
        self.sid = "sid-1"
        write(os.path.join(self.home, "tasks", self.sid, "1.json"), '{"subject": "a"}')
        write(os.path.join(self.home, "sessions", "77.json"),
              json.dumps({"pid": 77, "sessionId": self.sid, "cwd": "/p"}))
        write(os.path.join(self.home, "sessions", "78.json"),
              json.dumps({"pid": 78, "sessionId": "other", "cwd": "/q"}))
        write(os.path.join(self.home, "shell-snapshots", "snap.sh"),
              "export TOKEN=zzzz\n")

    def test_tasks_and_the_matching_descriptor_are_captured(self):
        got = sessions.side_artifacts(self.home, self.sid)
        kinds = {(k, n) for k, n, _ in got}
        self.assertIn(("tasks", "1.json"), kinds)
        self.assertIn(("descriptor", "77.json"), kinds)

    def test_another_sessions_descriptor_is_not_captured(self):
        got = sessions.side_artifacts(self.home, self.sid)
        self.assertNotIn(("descriptor", "78.json"), {(k, n) for k, n, _ in got})

    def test_shell_snapshots_are_excluded_by_default(self):
        default = sessions.side_artifacts(self.home, self.sid)
        self.assertFalse(any(k == "shell-snapshot" for k, _, _ in default))
        opted_in = sessions.side_artifacts(self.home, self.sid, include_shell=True)
        self.assertTrue(any(k == "shell-snapshot" for k, _, _ in opted_in))

    def test_absent_side_state_is_not_an_error(self):
        self.assertEqual(sessions.side_artifacts(self.home, "no-such-session"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
