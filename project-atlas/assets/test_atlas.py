#!/usr/bin/env python3
"""Unit suite for atlas.py — stdlib unittest, offline, no repo writes.

Covers the discovery rules, the durability guarantees (idempotent upsert,
tombstoning), the WAL invariant, the FTS5-or-LIKE search contract, the doc cap,
and every constraint the generated Litestream config has to satisfy.

    python3 -m unittest discover -s project-atlas/assets -p 'test_*.py'
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas  # noqa: E402


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def git(cwd: str, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"] + list(args),
        cwd=cwd, check=True, capture_output=True, text=True, timeout=30,
    )


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = atlas.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atlas-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "index", "atlas.db")
        self.tree = os.path.join(self.tmp, "tree")
        os.makedirs(self.tree)

    def rows(self, sql="SELECT * FROM projects ORDER BY path"):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()


class DiscoveryTests(TempCase):
    def test_git_and_manifest_roots_found_vendor_dirs_pruned(self):
        write(os.path.join(self.tree, "alpha", "pyproject.toml"), "[project]\n")
        write(os.path.join(self.tree, "beta", "Cargo.toml"), "[package]\n")
        write(os.path.join(self.tree, "node_modules", "pkg", "package.json"), "{}\n")
        write(os.path.join(self.tree, "beta", "target", "dep", "Cargo.toml"), "{}\n")
        found = sorted(atlas.find_projects([self.tree]))
        self.assertEqual(
            found, [os.path.join(self.tree, "alpha"), os.path.join(self.tree, "beta")]
        )
        self.assertFalse(any("node_modules" in p or "target" in p for p in found))

    def test_weak_marker_alone_is_not_a_project(self):
        write(os.path.join(self.tree, "scratch", "Makefile"), "all:\n")
        self.assertEqual(list(atlas.find_projects([self.tree])), [])

    def test_weak_marker_recorded_as_toolchain_next_to_a_strong_one(self):
        langs, tools, is_root = atlas.classify({"pyproject.toml", "Makefile", "Dockerfile"})
        self.assertTrue(is_root)
        self.assertIn("python", langs)
        self.assertIn("make", tools)
        self.assertIn("docker", tools)

    def test_project_root_not_descended_by_default_but_nested_opts_in(self):
        write(os.path.join(self.tree, "mono", "package.json"), "{}\n")
        write(os.path.join(self.tree, "mono", "pkgs", "a", "package.json"), "{}\n")
        self.assertEqual(
            list(atlas.find_projects([self.tree])), [os.path.join(self.tree, "mono")]
        )
        self.assertEqual(len(list(atlas.find_projects([self.tree], nested=True))), 2)

    def test_depth_limit_stops_the_walk(self):
        deep = os.path.join(self.tree, "a", "b", "c", "d", "e")
        write(os.path.join(deep, "go.mod"), "module x\n")
        self.assertEqual(list(atlas.find_projects([self.tree], max_depth=2)), [])
        self.assertEqual(len(list(atlas.find_projects([self.tree], max_depth=8))), 1)


class ScanTests(TempCase):
    def make_repo(self, name="repo", remote="https://example.invalid/repo.git"):
        root = os.path.join(self.tree, name)
        write(os.path.join(root, "README.md"), "# %s\n\nstripe webhook retries\n" % name)
        write(os.path.join(root, "pyproject.toml"), "[project]\n")
        write(os.path.join(root, "src", "main.py"), "print(1)\n")
        git(root, "init", "-q", ".")
        if remote:
            git(root, "remote", "add", "origin", remote)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        return root

    def test_scan_records_git_and_language_metadata(self):
        self.make_repo()
        code, out, _ = run("scan", "--db", self.db, self.tree)
        self.assertEqual(code, 0, out)
        row = self.rows()[0]
        self.assertEqual(row["vcs"], "git")
        self.assertEqual(row["git_remote"], "https://example.invalid/repo.git")
        self.assertTrue(row["git_branch"])
        self.assertEqual(len(row["git_head"]), 40)
        self.assertTrue(row["git_head_at"])
        self.assertIn("python", row["languages"])
        self.assertIn("git", row["toolchains"])
        self.assertIn("pyproject", row["toolchains"])
        self.assertGreater(row["file_count"], 0)

    def test_rescan_is_idempotent(self):
        self.make_repo()
        run("scan", "--db", self.db, self.tree)
        first = self.rows()[0]
        run("scan", "--db", self.db, self.tree)
        after = self.rows()
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["first_seen"], first["first_seen"])
        self.assertGreaterEqual(after[0]["last_scanned"], first["last_scanned"])

    def test_vanished_project_is_tombstoned_not_deleted(self):
        root = self.make_repo()
        write(os.path.join(self.tree, "other", "go.mod"), "module x\n")
        run("scan", "--db", self.db, self.tree)
        self.assertEqual(len(self.rows()), 2)
        shutil.rmtree(root)
        code, out, _ = run("scan", "--db", self.db, self.tree)
        self.assertEqual(code, 0, out)
        rows = {r["path"]: r for r in self.rows()}
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[os.path.abspath(root)]["missing_since"])

    def test_tombstoning_only_touches_the_roots_just_scanned(self):
        write(os.path.join(self.tree, "here", "go.mod"), "module x\n")
        elsewhere = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(elsewhere, "there", "go.mod"), "module y\n")
        run("scan", "--db", self.db, self.tree, elsewhere)
        shutil.rmtree(elsewhere)
        run("scan", "--db", self.db, self.tree)  # elsewhere is out of scope now
        rows = {r["path"]: r for r in self.rows()}
        self.assertIsNone(rows[os.path.join(elsewhere, "there")]["missing_since"])

    def test_reappearing_project_clears_the_tombstone(self):
        root = self.make_repo()
        run("scan", "--db", self.db, self.tree)
        shutil.rmtree(root)
        run("scan", "--db", self.db, self.tree)
        self.assertIsNotNone(self.rows()[0]["missing_since"])
        self.make_repo()
        run("scan", "--db", self.db, self.tree)
        self.assertIsNone(self.rows()[0]["missing_since"])

    def test_dry_run_writes_nothing(self):
        self.make_repo()
        code, out, _ = run("scan", "--db", self.db, "--dry-run", self.tree)
        self.assertEqual(code, 0)
        self.assertIn("WOULD_INDEX:", out)
        self.assertFalse(os.path.exists(self.db))

    def test_scan_survives_a_git_repo_with_no_commits_or_remote(self):
        root = os.path.join(self.tree, "empty")
        write(os.path.join(root, "go.mod"), "module x\n")
        git(root, "init", "-q", ".")
        code, out, _ = run("scan", "--db", self.db, self.tree)
        self.assertEqual(code, 0, out)
        row = self.rows()[0]
        self.assertEqual(row["vcs"], "git")
        self.assertEqual(row["git_head"], "")

    def test_scan_rejects_a_nonexistent_root(self):
        code, _, err = run("scan", "--db", self.db, os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)


class WalTests(TempCase):
    def test_connect_puts_the_index_in_wal(self):
        conn = atlas.connect(self.db)
        conn.close()
        self.assertEqual(atlas.journal_mode(self.db), "wal")

    def test_connect_fails_when_wal_cannot_be_set(self):
        # A rollback-journal database on a read-only directory cannot convert;
        # Litestream would silently replicate nothing, so atlas refuses first.
        path = os.path.join(self.tmp, "ro", "fixed.db")
        os.makedirs(os.path.dirname(path))
        seed = sqlite3.connect(path)
        seed.execute("CREATE TABLE t(x)")
        seed.commit()
        seed.close()
        os.chmod(os.path.dirname(path), 0o500)
        self.addCleanup(os.chmod, os.path.dirname(path), 0o700)
        if os.access(os.path.dirname(path), os.W_OK):
            self.skipTest("running as root — the directory stays writable")
        with self.assertRaises(atlas.AtlasError) as ctx:
            atlas.connect(path)
        self.assertIn("wal", str(ctx.exception).lower())

    def test_search_on_a_missing_index_is_a_readable_error(self):
        code, _, err = run("search", "--db", self.db, "anything")
        self.assertEqual(code, 1)
        self.assertIn("scan", err)


class SearchTests(TempCase):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.tree, "payments", "package.json"), "{}\n")
        write(os.path.join(self.tree, "payments", "README.md"),
              "# payments\n\nStripe webhook retry queue.\n")
        write(os.path.join(self.tree, "ingest", "go.mod"), "module ingest\n")
        write(os.path.join(self.tree, "ingest", "docs", "design.md"),
              "kafka consumer group rebalancing\n")
        run("scan", "--db", self.db, self.tree)

    def test_fts_and_like_engines_agree_on_the_same_hit(self):
        code, out, _ = run("search", "--db", self.db, "stripe")
        self.assertEqual(code, 0)
        self.assertIn("payments", out)
        engine_line = [l for l in out.splitlines() if l.startswith("SEARCH_RESULT")][0]
        self.assertIn("engine=fts5", engine_line)
        code, out2, _ = run("search", "--db", self.db, "--no-fts", "stripe")
        self.assertEqual(code, 0)
        self.assertIn("payments", out2)
        self.assertIn("engine=like", out2)

    def test_docs_directory_text_is_searchable(self):
        code, out, _ = run("search", "--db", self.db, "kafka")
        self.assertEqual(code, 0)
        self.assertIn("ingest", out)

    def test_path_fragments_are_searchable(self):
        code, out, _ = run("search", "--db", self.db, "payments")
        self.assertEqual(code, 0)
        self.assertIn("payments", out)

    def test_malformed_fts_query_degrades_instead_of_crashing(self):
        # A stray quote is a user typo, not a reason to exit non-zero.
        code, out, _ = run("search", "--db", self.db, 'stripe"(')
        self.assertEqual(code, 0)
        self.assertIn("engine=like", out)

    def test_like_wildcards_are_escaped_not_interpreted(self):
        code, out, _ = run("search", "--db", self.db, "--no-fts", "%")
        self.assertEqual(code, 0)
        self.assertIn("SEARCH_RESULT: 0 hit(s)", out)

    def test_json_output_is_machine_readable(self):
        import json
        code, out, _ = run("search", "--db", self.db, "--json", "stripe")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["engine"], "fts5")
        self.assertEqual(len(payload["hits"]), 1)
        self.assertIn("path", payload["hits"][0])

    def test_missing_projects_are_flagged_in_results(self):
        shutil.rmtree(os.path.join(self.tree, "payments"))
        run("scan", "--db", self.db, self.tree)
        code, out, _ = run("search", "--db", self.db, "--no-fts", "payments")
        self.assertEqual(code, 0)
        self.assertIn("[MISSING]", out)


class DocCapTests(TempCase):
    def test_oversized_readme_is_truncated_but_still_matches_its_head(self):
        root = os.path.join(self.tree, "huge")
        body = "MARKERWORD stripe\n" + ("filler paragraph text\n" * 20000)
        write(os.path.join(root, "package.json"), "{}\n")
        write(os.path.join(root, "README.md"), body)
        self.assertGreater(len(body.encode()), 200000)
        run("scan", "--db", self.db, self.tree)
        stored = self.rows("SELECT body FROM docs")[0]["body"]
        self.assertLessEqual(len(stored.encode("utf-8")), atlas.DOC_BYTE_CAP)
        code, out, _ = run("search", "--db", self.db, "MARKERWORD")
        self.assertEqual(code, 0)
        self.assertIn("huge", out)

    def test_doc_body_leads_with_path_and_name(self):
        root = os.path.join(self.tree, "tiny")
        write(os.path.join(root, "package.json"), "{}\n")
        body = atlas.doc_body(root, "tiny")
        self.assertTrue(body.startswith(root))
        self.assertIn("tiny", body)

    def test_undecodable_bytes_do_not_break_the_indexer(self):
        root = os.path.join(self.tree, "binary")
        write(os.path.join(root, "package.json"), "{}\n")
        with open(os.path.join(root, "README.md"), "wb") as fh:
            fh.write(b"caf\xe9 not-utf8 \xff\xfe marker\n")
        code, out, _ = run("scan", "--db", self.db, self.tree)
        self.assertEqual(code, 0, out)
        code, out, _ = run("search", "--db", self.db, "marker")
        self.assertEqual(code, 0)
        self.assertIn("binary", out)


def cfg_args(**kw):
    base = dict(db="/tmp/atlas.db", target="s3", bucket="b", path="project-atlas",
                account_id="", endpoint="", region="", replica_path="",
                sync_interval=atlas.DEFAULT_SYNC_INTERVAL, out="")
    base.update(kw)
    return argparse.Namespace(**base)


class LitestreamConfigTests(TempCase):
    def test_uses_the_singular_replica_key_not_the_deprecated_list(self):
        text, _ = atlas.render_config(cfg_args())
        self.assertIn("    replica:", text)
        self.assertNotIn("replicas:", text)

    def test_r2_endpoint_always_carries_an_https_scheme(self):
        text, url = atlas.render_config(cfg_args(target="r2", account_id="acct123"))
        self.assertIn("endpoint=https://acct123.r2.cloudflarestorage.com", text)
        self.assertIn("region=auto", url)

    def test_r2_bare_host_endpoint_is_upgraded(self):
        _, url = atlas.render_config(
            cfg_args(target="r2", endpoint="acct.r2.cloudflarestorage.com"))
        self.assertIn("endpoint=https://acct.r2.cloudflarestorage.com", url)

    def test_http_endpoint_is_promoted_to_https(self):
        self.assertEqual(atlas.ensure_https("http://x.example"), "https://x.example")
        self.assertEqual(atlas.ensure_https("https://x.example"), "https://x.example")

    def test_r2_without_account_or_endpoint_is_rejected(self):
        with self.assertRaises(atlas.AtlasError):
            atlas.render_config(cfg_args(target="r2"))

    def test_b2_carries_the_settings_its_api_requires(self):
        _, url = atlas.render_config(
            cfg_args(target="b2", endpoint="s3.us-west-004.backblazeb2.com"))
        self.assertIn("sign-payload=true", url)
        self.assertIn("force-path-style=true", url)
        self.assertIn("endpoint=https://", url)

    def test_sync_interval_is_always_explicit(self):
        text, _ = atlas.render_config(cfg_args())
        self.assertIn("sync-interval: 10s", text)
        text, _ = atlas.render_config(cfg_args(sync_interval="1m"))
        self.assertIn("sync-interval: 1m", text)

    def test_credentials_are_env_expansions_only(self):
        text, _ = atlas.render_config(cfg_args())
        self.assertIn("${LITESTREAM_ACCESS_KEY_ID}", text)
        self.assertIn("${LITESTREAM_SECRET_ACCESS_KEY}", text)

    def test_generator_refuses_to_emit_a_live_secret(self):
        secret = synth("NOT-A-REAL-", "VALUE-", "0123456789")
        os.environ["AWS_SECRET_ACCESS_KEY"] = secret
        self.addCleanup(os.environ.pop, "AWS_SECRET_ACCESS_KEY", None)
        code, out, err = run("litestream-config", "--db", self.db, "--target", "s3",
                             "--bucket", "b", "--path", secret)
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)
        self.assertNotIn(secret, out)

    def test_file_target_uses_a_path_replica(self):
        dest = os.path.join(self.tmp, "backup")
        text, url = atlas.render_config(
            cfg_args(target="file", replica_path=dest, bucket=""))
        self.assertIn("path: %s" % dest, text)
        self.assertTrue(url.startswith("file://"))
        self.assertNotIn("access-key-id", text)

    def test_runbook_repeats_the_replica_url(self):
        code, out, err = run("litestream-config", "--db", self.db, "--target", "r2",
                             "--bucket", "atlas", "--account-id", "acct")
        self.assertEqual(code, 0)
        restore = [l for l in err.splitlines() if l.startswith("litestream restore")]
        self.assertTrue(restore)
        self.assertTrue(all("acct.r2.cloudflarestorage.com" in l for l in restore))
        self.assertIn("litestream replicate", err)
        # The YAML goes to stdout alone, so it can be redirected into a file.
        self.assertTrue(out.lstrip().startswith("#"))
        self.assertNotIn("litestream restore", out)

    def test_out_flag_writes_the_config_and_names_it_in_the_runbook(self):
        dest = os.path.join(self.tmp, "conf", "litestream.yml")
        code, out, _ = run("litestream-config", "--db", self.db, "--target", "s3",
                           "--bucket", "atlas", "--out", dest)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(dest))
        self.assertIn("litestream replicate -config %s" % dest, out)

    def test_object_targets_require_a_bucket(self):
        code, _, err = run("litestream-config", "--db", self.db, "--target", "s3")
        self.assertEqual(code, 2)
        self.assertIn("--bucket", err)


class DoctorTests(TempCase):
    def test_doctor_json_carries_the_probed_capabilities(self):
        import json
        atlas.connect(self.db).close()
        code, out, _ = run("doctor", "--db", self.db, "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        for key in ("fts5", "journal_mode", "litestream", "git", "sqlite", "python"):
            self.assertIn(key, report)
        self.assertEqual(report["journal_mode"], "wal")

    def test_requiring_an_absent_binary_exits_one(self):
        self.addCleanup(os.environ.update, {"PATH": os.environ["PATH"]})
        os.environ["PATH"] = os.path.join(self.tmp, "empty-bin")
        code, out, _ = run("doctor", "--db", self.db, "--require", "litestream")
        self.assertEqual(code, 1)
        self.assertIn("DOCTOR_RESULT: FAIL", out)

    def test_requirements_may_be_comma_separated(self):
        atlas.connect(self.db).close()
        code, out, _ = run("doctor", "--db", self.db, "--require", "wal,fts5")
        expected = 0 if atlas.fts5_available() else 1
        self.assertEqual(code, expected, out)

    def test_unknown_capability_is_reported_not_ignored(self):
        code, _, err = run("doctor", "--db", self.db, "--require", "telepathy")
        self.assertEqual(code, 1)
        self.assertIn("unknown capability", err)

    def test_wal_requirement_fails_before_any_index_exists(self):
        code, out, _ = run("doctor", "--db", self.db, "--require", "wal")
        self.assertEqual(code, 1)
        self.assertIn("NO-DB", out)


class StatsTests(TempCase):
    def test_stats_counts_projects_and_languages(self):
        import json
        write(os.path.join(self.tree, "a", "go.mod"), "module a\n")
        write(os.path.join(self.tree, "a", "main.go"), "package main\n")
        run("scan", "--db", self.db, self.tree)
        code, out, _ = run("stats", "--db", self.db, "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["projects"], 1)
        self.assertEqual(payload["missing"], 0)
        self.assertIn("go", payload["languages"])


def synth(*parts):
    """Runtime-assembled fixture value; see test_sessions.synth for why."""
    return "".join(parts)


def jsonl(records):
    import json as _json
    return "\n".join(_json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                     for r in records) + "\n"


def rec(**kw):
    base = {"type": "user", "uuid": "u1", "parentUuid": None,
            "timestamp": "2026-01-01T00:00:00.000Z", "sessionId": "sid-alpha",
            "cwd": "/home/dev/payments", "gitBranch": "main", "version": "2.1.0",
            "message": {"role": "user", "content": "how do I retry a stripe webhook"}}
    base.update(kw)
    return base


class SessionCommandTests(TempCase):
    """The chats side of the index: capture, search, and cross-machine restore."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "claude-home")
        self.sid = "sid-alpha"
        self.cwd = "/home/dev/payments"
        self.records = [
            rec(uuid="u1"),
            rec(uuid="u2", parentUuid="u1", type="assistant",
                timestamp="2026-01-01T00:01:00.000Z",
                message={"role": "assistant",
                         "content": [{"type": "text", "text": "call the retry endpoint"}]}),
        ]
        write(os.path.join(self.home, "projects", "-home-dev-payments",
                           self.sid + ".jsonl"), jsonl(self.records))
        write(os.path.join(self.home, "tasks", self.sid, "1.json"),
              '{"subject": "wire the retry"}')

    def ingest(self, *extra):
        return run("sessions", "ingest", "--db", self.db, "--home", self.home, *extra)

    def test_capture_records_envelope_metadata(self):
        code, out, _ = self.ingest()
        self.assertEqual(code, 0, out)
        row = self.rows("SELECT * FROM agent_sessions")[0]
        self.assertEqual(row["session_id"], self.sid)
        self.assertEqual(row["cwd"], self.cwd)
        self.assertEqual(row["git_branch"], "main")
        self.assertEqual(row["cli_version"], "2.1.0")
        self.assertEqual(row["user_turns"], 1)
        self.assertEqual(row["assistant_turns"], 1)
        self.assertEqual(row["redacted"], 1)
        self.assertEqual(len(row["transcript_sha256"]), 64)

    def test_every_event_is_stored_in_order(self):
        self.ingest()
        events = self.rows("SELECT * FROM session_events ORDER BY seq")
        self.assertEqual([e["uuid"] for e in events], ["u1", "u2"])
        self.assertEqual([e["seq"] for e in events], [0, 1])

    def test_side_state_is_captured(self):
        self.ingest()
        arts = self.rows("SELECT * FROM session_artifacts")
        self.assertIn(("tasks", "1.json"), {(a["kind"], a["name"]) for a in arts})

    def test_recapture_replaces_rather_than_duplicates(self):
        self.ingest()
        self.ingest()
        self.assertEqual(len(self.rows("SELECT * FROM agent_sessions")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM session_events")), 2)

    def test_rewound_transcript_does_not_leave_stale_events(self):
        self.ingest()
        write(os.path.join(self.home, "projects", "-home-dev-payments",
                           self.sid + ".jsonl"), jsonl(self.records[:1]))
        self.ingest()
        self.assertEqual(len(self.rows("SELECT * FROM session_events")), 1)

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.ingest("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("WOULD_CAPTURE:", out)
        self.assertFalse(os.path.exists(self.db))

    def test_chats_are_searchable(self):
        self.ingest()
        code, out, _ = run("sessions", "search", "--db", self.db, "stripe")
        self.assertEqual(code, 0)
        self.assertIn(self.sid[:8], out)
        code, out, _ = run("sessions", "search", "--db", self.db, "--no-fts", "webhook")
        self.assertEqual(code, 0)
        self.assertIn("engine=like", out)

    def test_list_and_show_report_the_capture(self):
        self.ingest()
        code, out, _ = run("sessions", "list", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn(self.sid[:8], out)
        code, out, _ = run("sessions", "show", "--db", self.db, "sid-al")
        self.assertEqual(code, 0)
        self.assertIn("transcript_sha256", out)

    def test_ambiguous_prefix_is_refused(self):
        self.ingest()
        write(os.path.join(self.home, "projects", "-home-dev-payments",
                           "sid-alpha-two.jsonl"),
              jsonl([rec(uuid="z1", sessionId="sid-alpha-two")]))
        self.ingest()
        code, _, err = run("sessions", "show", "--db", self.db, "sid-a")
        self.assertEqual(code, 1)
        self.assertIn("matches 2 sessions", err)

    def test_an_exact_id_wins_over_an_ambiguous_prefix(self):
        self.ingest()
        write(os.path.join(self.home, "projects", "-home-dev-payments",
                           "sid-alpha-two.jsonl"),
              jsonl([rec(uuid="z1", sessionId="sid-alpha-two")]))
        self.ingest()
        code, out, _ = run("sessions", "show", "--db", self.db, self.sid)
        self.assertEqual(code, 0)
        self.assertIn(self.sid, out)

    # ── the security guarantees ──────────────────────────────────────────────

    def test_credentials_in_a_transcript_are_redacted_before_storage(self):
        secret = synth("hunter2", "hunter2", "hunter2")
        write(os.path.join(self.home, "projects", "-home-dev-payments",
                           self.sid + ".jsonl"),
              jsonl([rec(uuid="u1", message={"role": "user",
                                             "content": "export DB_PASSWORD=" + secret})]))
        self.ingest()
        stored = "".join(r["body"] for r in self.rows("SELECT body FROM session_events"))
        self.assertNotIn(secret, stored)
        self.assertIn("[REDACTED:", stored)
        docs = "".join(r["body"] for r in self.rows("SELECT body FROM session_docs"))
        self.assertNotIn(secret, docs)

    def test_no_redact_is_opt_in_and_warns(self):
        secret = synth("hunter2", "hunter2", "hunter2")
        write(os.path.join(self.home, "projects", "-home-dev-payments",
                           self.sid + ".jsonl"),
              jsonl([rec(uuid="u1", message={"role": "user",
                                             "content": "export DB_PASSWORD=" + secret})]))
        code, _, err = self.ingest("--no-redact")
        self.assertEqual(code, 0)
        self.assertIn("WARNING", err)
        stored = "".join(r["body"] for r in self.rows("SELECT body FROM session_events"))
        self.assertIn(secret, stored)
        self.assertEqual(self.rows("SELECT redacted FROM agent_sessions")[0]["redacted"], 0)

    def test_account_config_never_reaches_the_index(self):
        # ~/.claude.json carries oauthAccount / userID / machineID.
        write(os.path.join(os.path.dirname(self.home), ".claude.json"),
              '{"oauthAccount": {"emailAddress": "person@example.invalid"}, '
              '"userID": "abc123", "machineID": "def456"}')
        self.ingest()
        conn = sqlite3.connect(self.db)
        try:
            dump = "\n".join(conn.iterdump())
        finally:
            conn.close()
        self.assertNotIn("oauthAccount", dump)
        self.assertNotIn("person@example.invalid", dump)
        self.assertNotIn("machineID", dump)

    def test_shell_snapshots_are_excluded_unless_requested(self):
        write(os.path.join(self.home, "shell-snapshots", "snap.sh"),
              "export SOME_VALUE=zzzz\n")
        self.ingest()
        kinds = {r["kind"] for r in self.rows("SELECT kind FROM session_artifacts")}
        self.assertNotIn("shell-snapshot", kinds)
        self.ingest("--include-shell-snapshots")
        kinds = {r["kind"] for r in self.rows("SELECT kind FROM session_artifacts")}
        self.assertIn("shell-snapshot", kinds)

    # ── restore ──────────────────────────────────────────────────────────────

    def test_unredacted_restore_is_byte_identical(self):
        self.ingest("--no-redact")
        target_home = os.path.join(self.tmp, "restored")
        code, out, _ = run("sessions", "restore", "--db", self.db,
                           "--home", target_home, self.sid)
        self.assertEqual(code, 0, out)
        self.assertIn("verify=sha256-exact", out)
        path = os.path.join(target_home, "projects", "-home-dev-payments",
                            self.sid + ".jsonl")
        with open(path, "rb") as fh:
            restored = fh.read()
        with open(os.path.join(self.home, "projects", "-home-dev-payments",
                               self.sid + ".jsonl"), "rb") as fh:
            original = fh.read()
        self.assertEqual(restored, original)

    def test_restore_relocates_cwd_and_recomputes_the_slug(self):
        import json as _json
        self.ingest()
        target_home = os.path.join(self.tmp, "restored")
        code, out, _ = run("sessions", "restore", "--db", self.db, "--home", target_home,
                           "--cwd", "/Users/dev/payments", "--allow-redacted", self.sid)
        self.assertEqual(code, 0, out)
        path = os.path.join(target_home, "projects", "-Users-dev-payments",
                            self.sid + ".jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            cwds = {_json.loads(l)["cwd"] for l in fh if l.strip()}
        self.assertEqual(cwds, {"/Users/dev/payments"})
        self.assertIn("claude --resume", out)

    def test_task_state_is_restored_alongside_the_transcript(self):
        self.ingest()
        target_home = os.path.join(self.tmp, "restored")
        run("sessions", "restore", "--db", self.db, "--home", target_home,
            "--allow-redacted", self.sid)
        self.assertTrue(os.path.exists(
            os.path.join(target_home, "tasks", self.sid, "1.json")))

    def test_redacted_restore_requires_an_explicit_flag(self):
        self.ingest()
        target_home = os.path.join(self.tmp, "restored")
        code, _, err = run("sessions", "restore", "--db", self.db,
                           "--home", target_home, self.sid)
        self.assertEqual(code, 1)
        self.assertIn("redaction", err)
        self.assertFalse(os.path.exists(os.path.join(target_home, "projects")))

    def test_restore_refuses_to_clobber_without_force(self):
        self.ingest("--no-redact")
        target_home = os.path.join(self.tmp, "restored")
        run("sessions", "restore", "--db", self.db, "--home", target_home, self.sid)
        code, _, err = run("sessions", "restore", "--db", self.db,
                           "--home", target_home, self.sid)
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        code, _, _ = run("sessions", "restore", "--db", self.db, "--home", target_home,
                         "--force", self.sid)
        self.assertEqual(code, 0)

    def test_restore_refuses_a_broken_parent_chain(self):
        self.ingest("--no-redact")
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM session_events WHERE seq = 0")
        conn.commit()
        conn.close()
        code, _, err = run("sessions", "restore", "--db", self.db,
                           "--home", os.path.join(self.tmp, "restored"), self.sid)
        self.assertEqual(code, 1)
        self.assertIn("parent chain", err)

    def test_restore_dry_run_writes_nothing(self):
        self.ingest("--no-redact")
        target_home = os.path.join(self.tmp, "restored")
        code, out, _ = run("sessions", "restore", "--db", self.db, "--home", target_home,
                           "--dry-run", self.sid)
        self.assertEqual(code, 0)
        self.assertIn("WOULD_WRITE:", out)
        self.assertFalse(os.path.exists(os.path.join(target_home, "projects")))

    def test_restoring_an_unknown_session_is_a_readable_error(self):
        self.ingest()
        code, _, err = run("sessions", "restore", "--db", self.db, "no-such-session")
        self.assertEqual(code, 1)
        self.assertIn("no captured session", err)

    def test_ingest_without_an_agent_home_is_a_readable_error(self):
        code, _, err = run("sessions", "ingest", "--db", self.db,
                           "--home", os.path.join(self.tmp, "absent"))
        self.assertEqual(code, 1)
        self.assertIn("agent home", err)

    def test_sessions_link_to_indexed_projects(self):
        os.makedirs(self.tmp + "/tree/payments", exist_ok=True)
        write(os.path.join(self.tree, "payments", "go.mod"), "module payments\n")
        run("scan", "--db", self.db, self.tree)
        write(os.path.join(self.home, "projects", "-x", "sid-beta.jsonl"),
              jsonl([rec(uuid="b1", sessionId="sid-beta",
                         cwd=os.path.join(self.tree, "payments"))]))
        self.ingest()
        row = [r for r in self.rows("SELECT * FROM agent_sessions")
               if r["session_id"] == "sid-beta"][0]
        self.assertIsNotNone(row["project_id"])

    def test_doctor_reports_session_capture_state(self):
        import json as _json
        self.ingest()
        code, out, _ = run("doctor", "--db", self.db, "--json")
        self.assertEqual(code, 0)
        report = _json.loads(out)
        self.assertEqual(report["sessions_captured"], 1)
        self.assertEqual(report["sessions_unredacted"], 0)


class CliTests(TempCase):
    def test_db_flag_works_before_and_after_the_subcommand(self):
        write(os.path.join(self.tree, "a", "go.mod"), "module a\n")
        code, _, _ = run("--db", self.db, "scan", self.tree)
        self.assertEqual(code, 0)
        other = os.path.join(self.tmp, "other.db")
        code, _, _ = run("scan", "--db", other, self.tree)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(other))

    def test_atlas_db_env_var_sets_the_default(self):
        os.environ["ATLAS_DB"] = self.db
        self.addCleanup(os.environ.pop, "ATLAS_DB", None)
        self.assertEqual(atlas.default_db_path(), os.path.abspath(self.db))


if __name__ == "__main__":
    unittest.main(verbosity=2)
