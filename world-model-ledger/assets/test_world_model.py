#!/usr/bin/env python3
"""Install gate for world-model-ledger. Stdlib unittest; no pip, no network.

If any of these fail, the skill's guarantees do not hold — fix before relying on it.
Run:  python3 test_world_model.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import world_model as W


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wm = W.WorldModel(os.path.join(self.tmp, "m.db"))

    def tearDown(self):
        self.wm.close()

    def _iv(self, iid):
        return self.wm.conn.execute("SELECT * FROM interaction WHERE id=?", (iid,)).fetchone()


class TestSchema(Base):
    def test_tables_exist(self):
        names = {r["name"] for r in self.wm.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("entity", "interaction", "constraint_", "evidence",
                  "contradiction", "contradiction_member"):
            self.assertIn(t, names)

    def test_fts_available_here(self):
        # The CI/dev environment ships FTS5; the store still works without it (LIKE fallback).
        self.assertTrue(self.wm.has_fts)

    def test_scip_shaped_symbol_ids(self):
        # ids follow SCIP's `<scheme> <package> <descriptor>+` shape with descriptor suffixes
        fid = self.wm.upsert_entity("file", "auth/hash.py", path="auth/hash.py")
        f = self.wm.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (fid,)).fetchone()
        self.assertTrue(f["symbol_id"].startswith("wml . ") and f["symbol_id"].endswith("/"))
        mid = self.wm.upsert_entity("symbol", "hash_pw", path="auth/hash.py", entity_type="function")
        m = self.wm.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (mid,)).fetchone()
        self.assertTrue(m["symbol_id"].endswith("hash_pw()."))   # SCIP method suffix
        rid = self.wm.upsert_entity("referent", "stripe/refunds-api")
        r = self.wm.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (rid,)).fetchone()
        self.assertTrue(r["symbol_id"].startswith("wml-referent . "))


class TestTwoAxisInvariant(Base):
    """The load-bearing guarantee: code observation never raises normative_conf."""

    def test_observation_raises_observed_not_normative(self):
        # two INDEPENDENT sources (different files) accumulate above a single sighting
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "file_loc", "a.py:1", weight=0.9)
        self.wm.add_evidence("interaction", iid, "static", "b.py:2", weight=0.9)
        r = self._iv(iid)
        self.assertGreater(r["observed_conf"], 0.9)
        self.assertEqual(r["normative_conf"], 0.0)
        self.assertEqual(r["validation"], "unverified")

    def test_only_oracle_evidence_validates(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "file_loc", "a.py:1", weight=0.9)
        self.wm.add_evidence("interaction", iid, "test", "t::x", weight=0.8)
        r = self._iv(iid)
        self.assertGreaterEqual(r["normative_conf"], W.TAU_VALIDATE)
        self.assertEqual(r["validation"], "validated")

    def test_agent_assert_is_observation_only(self):
        # An agent asserting an edge is an observation claim — it must NOT validate it.
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "agent_assert", "x", weight=1.0)
        r = self._iv(iid)
        self.assertGreater(r["observed_conf"], 0.0)
        self.assertEqual(r["normative_conf"], 0.0)
        self.assertEqual(r["validation"], "unverified")


class TestDerivation(Base):
    def test_noisy_or_saturates(self):
        self.assertAlmostEqual(W.noisy_or([]), 0.0)
        self.assertAlmostEqual(W.noisy_or([0.5]), 0.5)
        self.assertAlmostEqual(W.noisy_or([0.5, 0.5]), 0.75)
        self.assertLess(W.noisy_or([0.9, 0.9, 0.9]), 1.0)

    def test_correlated_evidence_is_dampened(self):
        # two sightings of the SAME source (same file) must NOT inflate observed_conf...
        same = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", same, "file_loc", "mod.py:1", weight=0.9)
        self.wm.add_evidence("interaction", same, "static", "mod.py:40", weight=0.9)
        self.assertAlmostEqual(self._iv(same)["observed_conf"], 0.9)  # max within source, not 0.99
        # ...whereas two DIFFERENT sources fuse independently (noisy-OR)
        diff = self.wm.add_interaction("c", "calls", "d")
        self.wm.add_evidence("interaction", diff, "file_loc", "one.py:1", weight=0.9)
        self.wm.add_evidence("interaction", diff, "static", "two.py:1", weight=0.9)
        self.assertGreater(self._iv(diff)["observed_conf"], 0.9)

    def test_grouped_noisy_or_helper(self):
        self.assertAlmostEqual(W.grouped_noisy_or([("s", 0.9), ("s", 0.9)]), 0.9)
        self.assertGreater(W.grouped_noisy_or([("a", 0.9), ("b", 0.9)]), 0.9)

    def test_refute_lowers_normative_and_contradicts(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "test", "t::x", weight=0.8)  # validated
        self.assertEqual(self._iv(iid)["validation"], "validated")
        self.wm.add_evidence("interaction", iid, "test", "t::y", polarity="refutes", weight=0.9)
        r = self._iv(iid)
        self.assertEqual(r["validation"], "contradicted")
        self.assertLess(r["normative_conf"], W.TAU_VALIDATE)

    def test_entrenchment_tracks_strongest_evidence(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "static", "a.py:1")
        self.assertEqual(self._iv(iid)["entrenchment"], 1)
        self.wm.add_evidence("interaction", iid, "human", "confirmed", weight=0.9)
        self.assertEqual(self._iv(iid)["entrenchment"], 4)


class TestIdempotency(Base):
    def test_duplicate_interaction_not_double_counted(self):
        i1 = self.wm.add_interaction("a", "calls", "b")
        i2 = self.wm.add_interaction("a", "calls", "b")
        self.assertEqual(i1, i2)

    def test_duplicate_evidence_idempotent(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "file_loc", "a.py:1", weight=0.9)
        before = self._iv(iid)["observed_conf"]
        self.wm.add_evidence("interaction", iid, "file_loc", "a.py:1", weight=0.9)
        self.assertEqual(self._iv(iid)["observed_conf"], before)
        n = self.wm.conn.execute("SELECT COUNT(*) c FROM evidence").fetchone()["c"]
        self.assertEqual(n, 1)


class TestSoftInvalidation(Base):
    def test_stale_marks_not_deletes(self):
        iid = self.wm.add_interaction("f", "calls", "g", subj_kind="symbol")
        # anchor the subject to a path so the stale sweep can find it
        sid = self._iv(iid)["subject_id"]
        self.wm.conn.execute("UPDATE entity SET path=? WHERE id=?", ("mod.py", sid))
        n = self.wm.mark_stale_for_path("mod.py")
        self.assertEqual(n, 1)
        r = self._iv(iid)
        self.assertEqual(r["validation"], "stale")
        self.assertIsNotNone(r["invalidated_at"])
        # row still present (soft delete)
        self.assertIsNotNone(self._iv(iid))

    def test_derive_skips_invalidated(self):
        iid = self.wm.add_interaction("f", "calls", "g")
        sid = self._iv(iid)["subject_id"]
        self.wm.conn.execute("UPDATE entity SET path=? WHERE id=?", ("mod.py", sid))
        self.wm.mark_stale_for_path("mod.py")
        # new evidence must not un-stale it
        self.wm.add_evidence("interaction", iid, "test", "t::x", weight=0.9)
        self.assertEqual(self._iv(iid)["validation"], "stale")


class TestContradiction(Base):
    def test_forbids_detects_and_proposes(self):
        self.wm.add_constraint(
            "no-weak-hash", "forbids", "{subject} {predicate} {object} — forbidden ({matched})",
            scope_predicate="uses", params={"patterns": ["md5", "sha1"]})
        iid = self.wm.add_interaction("reset_pw", "uses", "sha1")
        self.wm.add_evidence("interaction", iid, "file_loc", "auth.py:22", weight=0.9)
        opened = self.wm.evaluate_constraints()
        self.assertEqual(len(opened), 1)
        c = self.wm.conn.execute("SELECT * FROM contradiction WHERE id=?", (opened[0],)).fetchone()
        self.assertIn("sha1", c["message"])
        self.assertTrue(c["proposed_fix"])
        # the member interaction is now flagged contradicted
        self.assertEqual(self._iv(iid)["validation"], "contradicted")

    def test_contradiction_dedups(self):
        self.wm.add_constraint("nw", "forbids", "{object}", scope_predicate="uses",
                               params={"patterns": ["md5"]})
        self.wm.add_interaction("x", "uses", "md5")
        self.assertEqual(len(self.wm.evaluate_constraints()), 1)
        self.assertEqual(len(self.wm.evaluate_constraints()), 0)  # no duplicate open row

    def test_cardinality_functional(self):
        self.wm.add_constraint("one-owner", "functional", "{subject} has multiple: {values}",
                               scope_predicate="owned_by", params={"maxCount": 1})
        self.wm.add_interaction("table_users", "owned_by", "team_a")
        self.wm.add_interaction("table_users", "owned_by", "team_b")
        self.assertEqual(len(self.wm.evaluate_constraints()), 1)

    def test_resolution_clears_contradicted(self):
        self.wm.add_constraint("nw", "forbids", "{object}", scope_predicate="uses",
                               params={"patterns": ["md5"]})
        iid = self.wm.add_interaction("x", "uses", "md5")
        self.wm.add_evidence("interaction", iid, "test", "t::x", weight=0.9)
        cid = self.wm.evaluate_constraints()[0]
        self.assertEqual(self._iv(iid)["validation"], "contradicted")
        self.wm.resolve_contradiction(cid, "fixed_code")
        # after resolution the oracle evidence stands again
        self.assertEqual(self._iv(iid)["validation"], "validated")


class TestRetrieval(Base):
    def test_query_partitions(self):
        v = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", v, "test", "t::x", weight=0.9)
        u = self.wm.add_interaction("a", "imports", "c")
        self.wm.add_evidence("interaction", u, "file_loc", "a.py:1", weight=0.9)
        res = self.wm.query_touching("a")
        facts_validated = [x["fact"] for x in res["validated"]]
        facts_unverified = [x["fact"] for x in res["unverified"]]
        self.assertIn("a calls b", facts_validated)
        self.assertIn("a imports c", facts_unverified)


class TestGraphTraversal(Base):
    def test_recursive_cte_is_cycle_safe(self):
        # a -> b -> c -> a  (a cycle): the depth-capped UNION walk must terminate.
        self.wm.add_interaction("a", "calls", "b")
        self.wm.add_interaction("b", "calls", "c")
        self.wm.add_interaction("c", "calls", "a")
        seed = self.wm._entities_for_token("a")
        reached = self.wm._reachable_nodes(seed, max_depth=5)  # > cycle length
        self.assertGreaterEqual(len(reached), 3)  # terminates, returns the ring

    def test_two_hop_neighborhood(self):
        # a -> b -> c : a 1-hop query sees a-b; a 2-hop query also sees b-c.
        self.wm.add_interaction("a", "calls", "b")
        self.wm.add_interaction("b", "calls", "c")
        one = self.wm.query_touching("a", hops=1)
        two = self.wm.query_touching("a", hops=2)
        one_facts = [x["fact"] for v in ("validated", "unverified", "contradicted") for x in one[v]]
        two_facts = [x["fact"] for v in ("validated", "unverified", "contradicted") for x in two[v]]
        self.assertIn("a calls b", one_facts)
        self.assertNotIn("b calls c", one_facts)
        self.assertIn("b calls c", two_facts)


class TestBuild(Base):
    def _mini_repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "pkg"))
        open(os.path.join(d, "pkg", "__init__.py"), "w").close()
        with open(os.path.join(d, "pkg", "core.py"), "w") as f:
            f.write("VALUE = 1\n")
        with open(os.path.join(d, "pkg", "app.py"), "w") as f:
            f.write("from pkg.core import VALUE\nimport pkg.core\n")
        with open(os.path.join(d, "run.sh"), "w") as f:
            f.write('#!/bin/sh\npython3 pkg/app.py\n')
        return d

    def test_build_registers_files_and_imports(self):
        d = self._mini_repo()
        stats = self.wm.build_from_repo(d)
        self.assertGreaterEqual(stats["build"]["files_registered"], 4)
        # the python import edge exists
        edge = self.wm.conn.execute(
            "SELECT i.* FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path='pkg/app.py' AND i.predicate='imports' AND oe.path='pkg/core.py'"
        ).fetchone()
        self.assertIsNotNone(edge)
        # the shell reference edge exists (run.sh -> pkg/app.py)
        ref = self.wm.conn.execute(
            "SELECT i.* FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path='run.sh' AND i.predicate='references' AND oe.path='pkg/app.py'"
        ).fetchone()
        self.assertIsNotNone(ref)

    def test_build_is_observation_only(self):
        d = self._mini_repo()
        self.wm.build_from_repo(d)
        # every seeded interaction is observed-but-unverified — a bulk scan never validates
        rows = self.wm.conn.execute(
            "SELECT observed_conf, normative_conf, validation FROM interaction").fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["normative_conf"], 0.0)
            self.assertEqual(r["validation"], "unverified")
            self.assertGreater(r["observed_conf"], 0.0)

    def _counts(self):
        c = self.wm.conn.execute
        return (c("SELECT COUNT(*) n FROM entity").fetchone()["n"],
                c("SELECT COUNT(*) n FROM interaction").fetchone()["n"],
                c("SELECT COUNT(*) n FROM evidence").fetchone()["n"])

    def test_build_is_idempotent_across_multiple_runs(self):
        d = self._mini_repo()
        self.wm.build_from_repo(d)
        base = self._counts()
        # run it two MORE times — counts must not budge (pure upsert, no dupes, no deletes)
        for _ in range(2):
            stats = self.wm.build_from_repo(d)
            self.assertEqual(self._counts(), base)
            self.assertEqual(stats["build"]["entities_added"], 0)
            self.assertEqual(stats["build"]["interactions_added"], 0)
            self.assertEqual(stats["build"]["evidence_added"], 0)

    def test_build_preserves_first_seen_and_bumps_last_seen(self):
        d = self._mini_repo()
        self.wm.build_from_repo(d)
        row1 = self.wm.conn.execute(
            "SELECT first_seen, last_seen FROM entity WHERE path='pkg/core.py'").fetchone()
        self.wm.build_from_repo(d)  # re-run
        row2 = self.wm.conn.execute(
            "SELECT first_seen, last_seen FROM entity WHERE path='pkg/core.py'").fetchone()
        self.assertEqual(row1["first_seen"], row2["first_seen"])   # never reset
        self.assertGreaterEqual(row2["last_seen"], row1["last_seen"])

    def test_build_adds_only_the_new_file_on_second_run(self):
        d = self._mini_repo()
        self.wm.build_from_repo(d)
        before = self._counts()
        with open(os.path.join(d, "pkg", "extra.py"), "w") as f:
            f.write("from pkg.core import VALUE\n")   # one new file with one import edge
        stats = self.wm.build_from_repo(d)
        self.assertEqual(stats["build"]["entities_added"], 1)      # exactly the new file
        self.assertGreaterEqual(stats["build"]["interactions_added"], 1)
        e_after, i_after, _ = self._counts()
        self.assertEqual(e_after, before[0] + 1)                   # nothing else duplicated

    def test_build_does_not_clobber_agent_validated_facts(self):
        d = self._mini_repo()
        self.wm.build_from_repo(d)
        # agent validates the import edge with oracle (test) evidence → normative up, validated
        iid = self.wm.conn.execute(
            "SELECT i.id FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path='pkg/app.py' AND i.predicate='imports' AND oe.path='pkg/core.py'"
        ).fetchone()["id"]
        self.wm.add_evidence("interaction", iid, "test", "tests/test_app.py::t", weight=0.9)
        self.assertEqual(self._iv(iid)["validation"], "validated")
        # a subsequent build (which re-adds the same edge as an OBSERVATION) must not
        # downgrade it — oracle evidence stands, so it stays validated.
        self.wm.build_from_repo(d)
        self.assertEqual(self._iv(iid)["validation"], "validated")
        self.assertGreaterEqual(self._iv(iid)["normative_conf"], W.TAU_VALIDATE)

    def test_build_reports_truncation(self):
        d = self._mini_repo()
        stats = self.wm.build_from_repo(d, max_files=2)
        self.assertEqual(stats["build"]["files_registered"], 2)
        self.assertGreaterEqual(stats["build"]["dropped_files"], 1)  # no silent truncation


class TestBuildLanguages(Base):
    def _poly(self):
        import tempfile
        d = tempfile.mkdtemp()
        def w(p, s):
            fp = os.path.join(d, p)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w").write(s)
        w("app/index.js", "import React from 'react'\nimport {x} from './util'\nconst y=require('lodash')\n")
        w("app/util.js", "export const z=1\n")
        w("app/main.go", 'package main\nimport (\n "fmt"\n "github.com/gin-gonic/gin"\n)\n')
        w("app/lib.rs", "mod helper;\nuse serde::Serialize;\nuse std::fmt;\n")
        w("app/helper.rs", "pub fn h(){}\n")
        w("app/server.rb", "require 'sinatra'\nrequire_relative 'helper'\n")
        w("app/helper.rb", "def h; end\n")
        w("Dockerfile", "FROM node:18\nCOPY app/index.js /app/\n")
        w("docker-compose.yml", "services:\n  web:\n    image: postgres:16\n")
        w(".github/workflows/ci.yml", "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n")
        w("infra/main.tf", 'module "vpc" {\n  source = "terraform-aws-modules/vpc/aws"\n}\n')
        return d

    def _has(self, s, p, o_name):
        return self.wm.conn.execute(
            "SELECT 1 FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path=? AND i.predicate=? AND (oe.path=? OR oe.name=?) AND i.invalidated_at IS NULL",
            (s, p, o_name, o_name)).fetchone() is not None

    def test_local_import_edges_resolve_to_files(self):
        d = self._poly(); self.wm.build_from_repo(d)
        self.assertTrue(self._has("app/index.js", "imports", "app/util.js"))   # JS relative
        self.assertTrue(self._has("app/server.rb", "imports", "app/helper.rb"))  # Ruby require_relative
        self.assertTrue(self._has("app/lib.rs", "includes", "app/helper.rs"))   # Rust mod
        self.assertTrue(self._has("Dockerfile", "references", "app/index.js"))  # Docker COPY

    def test_external_dependencies_become_referents(self):
        d = self._poly(); self.wm.build_from_repo(d)
        for subj, dep in [("app/index.js", "react"), ("app/index.js", "lodash"),
                          ("app/main.go", "github.com/gin-gonic/gin"), ("app/lib.rs", "serde"),
                          ("app/server.rb", "sinatra"), ("Dockerfile", "node:18"),
                          ("docker-compose.yml", "postgres:16"),
                          (".github/workflows/ci.yml", "actions/checkout@v4"),
                          ("infra/main.tf", "terraform-aws-modules/vpc/aws")]:
            self.assertTrue(self._has(subj, "depends_on", dep), f"{subj} -> {dep}")
        # dependency targets are referents, not files
        r = self.wm.conn.execute("SELECT kind FROM entity WHERE name='react'").fetchone()
        self.assertEqual(r["kind"], "referent")

    def test_stdlib_is_not_recorded_as_dependency(self):
        d = self._poly(); self.wm.build_from_repo(d)
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name='fmt'").fetchone())  # Go stdlib
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name='std'").fetchone())  # Rust std

    def test_dependency_edges_are_observation_only(self):
        d = self._poly(); self.wm.build_from_repo(d)
        for r in self.wm.conn.execute(
                "SELECT normative_conf, validation FROM interaction WHERE predicate='depends_on'").fetchall():
            self.assertEqual(r["normative_conf"], 0.0)      # a declared dep is a sighting, not a proof
            self.assertEqual(r["validation"], "unverified")

    def test_language_extraction_is_idempotent(self):
        d = self._poly(); self.wm.build_from_repo(d)
        counts = (self.wm.conn.execute("SELECT COUNT(*) n FROM entity").fetchone()["n"],
                  self.wm.conn.execute("SELECT COUNT(*) n FROM interaction").fetchone()["n"])
        stats = self.wm.build_from_repo(d)
        self.assertEqual(stats["build"]["entities_added"], 0)
        self.assertEqual(stats["build"]["interactions_added"], 0)


class TestBuildTier1(Base):
    def _repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        def w(p, s):
            fp = os.path.join(d, p)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w").write(s)
        w("com/foo/App.java", "package com.foo;\nimport com.foo.Util;\n"
                              "import org.springframework.boot.SpringApplication;\nimport java.util.List;\n")
        w("com/foo/Util.java", "package com.foo;\npublic class Util {}\n")
        w("main.c", '#include "inc/helper.h"\n#include <boost/asio.hpp>\n#include <stdio.h>\n')
        w("inc/helper.h", "int helper();\n")
        w("cs/Program.cs", "using System.Linq;\nusing Newtonsoft.Json;\n")
        w("php/index.php", "<?php\nrequire_once 'php/lib.php';\n"
                           "use Symfony\\Component\\HttpFoundation\\Request;\nuse App\\Model\\User;\n")
        w("php/lib.php", "<?php\nfunction lib(){}\n")
        return d

    def _has(self, s, p, o):
        return self.wm.conn.execute(
            "SELECT 1 FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path=? AND i.predicate=? AND (oe.path=? OR oe.name=?) AND i.invalidated_at IS NULL",
            (s, p, o, o)).fetchone() is not None

    def test_java_local_fqn_and_external(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._has("com/foo/App.java", "imports", "com/foo/Util.java"))   # FQN → file
        self.assertTrue(self._has("com/foo/App.java", "depends_on", "org.springframework"))
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name LIKE 'java.util%'").fetchone())

    def test_c_local_include_and_library(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._has("main.c", "includes", "inc/helper.h"))          # #include "..."
        self.assertTrue(self._has("main.c", "depends_on", "boost"))               # <boost/asio.hpp>
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name='stdio.h'").fetchone())  # <stdio.h> skipped

    def test_csharp_external_and_system_skip(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._has("cs/Program.cs", "depends_on", "Newtonsoft.Json"))
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name LIKE 'System%'").fetchone())

    def test_php_local_include_and_vendor_use(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._has("php/index.php", "includes", "php/lib.php"))     # require_once
        self.assertTrue(self._has("php/index.php", "depends_on", "Symfony"))       # use vendor ns
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name='App'").fetchone())  # app ns skipped

    def test_tier1_edges_observation_only(self):
        d = self._repo(); self.wm.build_from_repo(d)
        for r in self.wm.conn.execute("SELECT normative_conf, validation FROM interaction").fetchall():
            self.assertEqual(r["normative_conf"], 0.0)
            self.assertEqual(r["validation"], "unverified")


class TestBuildTier2(Base):
    def _repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        def w(p, s):
            fp = os.path.join(d, p)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w").write(s)
        w("App.kt", "package a\nimport retrofit2.Retrofit\nimport kotlin.collections.List\n")
        w("View.swift", "import SwiftUI\nimport Alamofire\n")
        w("main.dart", "import 'package:http/http.dart';\nimport 'util.dart';\nimport 'dart:async';\n")
        w("util.dart", "void u(){}\n")
        w("Main.scala", "import cats.effect.IO\nimport scala.collection.mutable\n")
        w("m.ex", "defmodule M do\n  use Phoenix.Controller\n  alias Enum\nend\n")
        return d

    def _dep(self, s, o):
        return self.wm.conn.execute(
            "SELECT 1 FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id WHERE se.path=? AND i.predicate='depends_on' AND oe.name=?",
            (s, o)).fetchone() is not None

    def _absent(self, name):
        return self.wm.conn.execute("SELECT 1 FROM entity WHERE name=?", (name,)).fetchone() is None

    def test_tier2_external_deps(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._dep("App.kt", "retrofit2.Retrofit"))     # Kotlin
        self.assertTrue(self._dep("View.swift", "Alamofire"))          # Swift
        self.assertTrue(self._dep("main.dart", "http"))                # Dart package:
        self.assertTrue(self._dep("Main.scala", "cats.effect"))        # Scala
        self.assertTrue(self._dep("m.ex", "Phoenix"))                  # Elixir

    def test_dart_local_import_resolves(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self.wm.conn.execute(
            "SELECT 1 FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path='main.dart' AND i.predicate='imports' AND oe.path='util.dart'").fetchone())

    def test_tier2_stdlib_skipped(self):
        d = self._repo(); self.wm.build_from_repo(d)
        for n in ("kotlin.collections", "scala.collection", "Enum", "SwiftUI"):
            self.assertTrue(self._absent(n), n)


class TestBuildManifests(Base):
    def _repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        def w(p, s):
            open(os.path.join(d, p), "w").write(s)
        w("package.json", '{"dependencies":{"react":"^18"},"devDependencies":{"jest":"^29"}}')
        w("requirements.txt", "requests==2.31\nflask>=2\n# c\n-r other.txt\n")
        w("Cargo.toml", '[dependencies]\nserde = "1"\ntokio = { version = "1" }\n')
        w("go.mod", "module example.com/app\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n")
        w("pom.xml", "<project><dependencies><dependency><groupId>org.springframework</groupId>"
                     "<artifactId>spring-core</artifactId></dependency></dependencies></project>")
        w("build.gradle", 'dependencies {\n  implementation("com.squareup.okhttp3:okhttp:4.9.0")\n}\n')
        w("composer.json", '{"require":{"symfony/console":"^6","php":"^8"}}')
        return d

    def _dep(self, s, o):
        return self.wm.conn.execute(
            "SELECT 1 FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id WHERE se.path=? AND i.predicate='depends_on' AND oe.name=?",
            (s, o)).fetchone() is not None

    def test_manifests_yield_precise_deps(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._dep("package.json", "react"))
        self.assertTrue(self._dep("package.json", "jest"))
        self.assertTrue(self._dep("requirements.txt", "requests"))
        self.assertTrue(self._dep("requirements.txt", "flask"))
        self.assertTrue(self._dep("Cargo.toml", "serde"))
        self.assertTrue(self._dep("go.mod", "github.com/gin-gonic/gin"))
        self.assertTrue(self._dep("pom.xml", "org.springframework:spring-core"))
        self.assertTrue(self._dep("build.gradle", "com.squareup.okhttp3:okhttp"))
        self.assertTrue(self._dep("composer.json", "symfony/console"))

    def test_manifest_skips_and_observation_only(self):
        d = self._repo(); self.wm.build_from_repo(d)
        # composer 'php' pseudo-package and requirements '-r' line are not deps
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name='php'").fetchone())
        for r in self.wm.conn.execute("SELECT normative_conf, validation FROM interaction").fetchall():
            self.assertEqual(r["normative_conf"], 0.0)
            self.assertEqual(r["validation"], "unverified")

    def test_manifests_idempotent(self):
        d = self._repo(); self.wm.build_from_repo(d)
        stats = self.wm.build_from_repo(d)
        self.assertEqual(stats["build"]["interactions_added"], 0)
        self.assertEqual(stats["build"]["entities_added"], 0)


class TestBuildGoModuleAndCsproj(Base):
    def _repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        def w(p, s):
            fp = os.path.join(d, p)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w").write(s)
        w("go.mod", "module example.com/app\ngo 1.22\nrequire github.com/x/y v1.0.0\n")
        w("main.go", 'package main\nimport (\n "fmt"\n "example.com/app/util"\n "github.com/x/y"\n)\n')
        w("util/a.go", "package util\nfunc A(){}\n")
        w("util/b.go", "package util\nfunc B(){}\n")
        w("App.csproj", '<Project><ItemGroup>'
                        '<PackageReference Include="Newtonsoft.Json" Version="13.0.1" />'
                        '<ProjectReference Include="..\\Lib\\Lib.csproj" />'
                        '</ItemGroup></Project>')
        w("Lib/Lib.csproj", "<Project></Project>")
        return d

    def _has(self, s, p, o):
        return self.wm.conn.execute(
            "SELECT 1 FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id "
            "WHERE se.path=? AND i.predicate=? AND (oe.path=? OR oe.name=?) AND i.invalidated_at IS NULL",
            (s, p, o, o)).fetchone() is not None

    def test_go_local_package_resolves_to_files(self):
        d = self._repo(); self.wm.build_from_repo(d)
        # a local package import links to EVERY .go file in that package dir
        self.assertTrue(self._has("main.go", "imports", "util/a.go"))
        self.assertTrue(self._has("main.go", "imports", "util/b.go"))
        # external module → depends_on referent; stdlib fmt skipped
        self.assertTrue(self._has("main.go", "depends_on", "github.com/x/y"))
        self.assertIsNone(self.wm.conn.execute("SELECT 1 FROM entity WHERE name='fmt'").fetchone())
        # the local package is NOT recorded as an external referent
        self.assertIsNone(self.wm.conn.execute(
            "SELECT 1 FROM entity WHERE kind='referent' AND name LIKE 'example.com/app%'").fetchone())

    def test_csproj_package_and_project_references(self):
        d = self._repo(); self.wm.build_from_repo(d)
        self.assertTrue(self._has("App.csproj", "depends_on", "Newtonsoft.Json"))   # NuGet artifact
        self.assertTrue(self._has("App.csproj", "references", "Lib/Lib.csproj"))    # local project ref

    def test_followup2_observation_only(self):
        d = self._repo(); self.wm.build_from_repo(d)
        for r in self.wm.conn.execute("SELECT normative_conf, validation FROM interaction").fetchall():
            self.assertEqual(r["normative_conf"], 0.0)
            self.assertEqual(r["validation"], "unverified")


class TestBuildPrune(Base):
    def _repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "pkg"))
        open(os.path.join(d, "pkg", "__init__.py"), "w").close()
        with open(os.path.join(d, "pkg", "core.py"), "w") as f:
            f.write("VALUE = 1\n")
        with open(os.path.join(d, "pkg", "app.py"), "w") as f:
            f.write("from pkg.core import VALUE\n")   # app imports core
        return d

    def _edge(self, s, p, o):
        return self.wm.conn.execute(
            "SELECT i.* FROM interaction i JOIN entity se ON se.id=i.subject_id "
            "JOIN entity oe ON oe.id=i.object_id WHERE se.path=? AND i.predicate=? AND oe.path=?",
            (s, p, o)).fetchone()

    def test_prune_is_opt_in(self):
        d = self._repo()
        self.wm.build_from_repo(d)
        os.remove(os.path.join(d, "pkg", "core.py"))    # delete the imported file
        stats = self.wm.build_from_repo(d)              # default: NO prune
        self.assertEqual(stats["build"]["pruned_stale_edges"], 0)
        self.assertIsNone(self._edge("pkg/app.py", "imports", "pkg/core.py")["invalidated_at"])

    def test_prune_soft_invalidates_vanished_edges(self):
        d = self._repo()
        self.wm.build_from_repo(d)
        edge_id = self._edge("pkg/app.py", "imports", "pkg/core.py")["id"]
        os.remove(os.path.join(d, "pkg", "core.py"))
        stats = self.wm.build_from_repo(d, prune=True)
        self.assertGreaterEqual(stats["build"]["pruned_stale_edges"], 1)
        row = self.wm.conn.execute("SELECT * FROM interaction WHERE id=?", (edge_id,)).fetchone()
        self.assertEqual(row["validation"], "stale")
        self.assertIsNotNone(row["invalidated_at"])
        self.assertIsNotNone(row)                        # soft delete — row still present

    def test_prune_never_touches_agent_validated_edges(self):
        d = self._repo()
        self.wm.build_from_repo(d)
        edge_id = self._edge("pkg/app.py", "imports", "pkg/core.py")["id"]
        # agent validates it (non-build oracle evidence)
        self.wm.add_evidence("interaction", edge_id, "test", "tests/t.py::x", weight=0.9)
        self.assertEqual(
            self.wm.conn.execute("SELECT validation FROM interaction WHERE id=?", (edge_id,)).fetchone()["validation"],
            "validated")
        os.remove(os.path.join(d, "pkg", "core.py"))     # file vanishes anyway
        stats = self.wm.build_from_repo(d, prune=True)
        self.assertEqual(stats["build"]["pruned_stale_edges"], 0)   # protected
        row = self.wm.conn.execute("SELECT * FROM interaction WHERE id=?", (edge_id,)).fetchone()
        self.assertEqual(row["validation"], "validated")            # untouched
        self.assertIsNone(row["invalidated_at"])

    def test_prune_ignores_present_files(self):
        d = self._repo()
        self.wm.build_from_repo(d)
        stats = self.wm.build_from_repo(d, prune=True)   # nothing deleted
        self.assertEqual(stats["build"]["pruned_stale_edges"], 0)


class TestProjectRules(Base):
    def test_validated_constraint_surfaces_in_precall(self):
        cid = self.wm.add_constraint("no-weak-hash", "forbids", "weak hash {matched}",
                                     scope_predicate="uses", params={"patterns": ["md5", "sha1"]})
        # unvalidated constraint does NOT appear as a project rule
        self.assertEqual(self.wm.project_rules(), [])
        self.wm.add_evidence("constraint", cid, "human", "confirmed", weight=0.9)  # validate it
        rules = self.wm.project_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["name"], "no-weak-hash")
        # and it appears in the pre-call for a file with no recorded edges yet
        txt = self.wm.precall(["brand/new_file.py"])
        self.assertIn("no-weak-hash", txt)
        self.assertIn("md5", txt)


class TestReferents(Base):
    def test_map_creates_referent_edge_unverified(self):
        import argparse
        a = argparse.Namespace(symbol="billing/refund.py", to="stripe/refunds-api")
        W.cmd_map(self.wm, a)
        ref = self.wm.conn.execute("SELECT * FROM entity WHERE kind='referent'").fetchone()
        self.assertEqual(ref["name"], "stripe/refunds-api")
        edge = self.wm.conn.execute(
            "SELECT * FROM interaction WHERE predicate='realizes'").fetchone()
        # mapping is a claim → observed but NOT normatively validated
        self.assertEqual(edge["validation"], "unverified")
        self.assertEqual(edge["normative_conf"], 0.0)


class TestConsolidateAndStats(Base):
    def test_consolidate_and_stats_shape(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "test", "t::x", weight=0.9)
        s = self.wm.consolidate()
        self.assertIn("interactions", s)
        self.assertEqual(s["interactions"]["validated"], 1)
        self.assertIn("open_contradictions", s)

    def test_improvement_measurable(self):
        # unverified → validated raises the validated ratio (acceptance: correctness improves)
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "file_loc", "a.py:1", weight=0.9)
        self.assertEqual(self.wm.stats()["interactions"]["validated"], 0)
        self.wm.add_evidence("interaction", iid, "human", "confirmed", weight=0.9)
        self.assertEqual(self.wm.stats()["interactions"]["validated"], 1)


class TestTrustBoundary(Base):
    """Evidence kinds are constrained; unknown/forged kinds are rejected at the API."""

    def test_unknown_evidence_kind_rejected(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        with self.assertRaises(ValueError):
            self.wm.add_evidence("interaction", iid, "totally_made_up", "x")


class TestExecEdgeParsing(unittest.TestCase):
    """parse_exec_edges is structural, high-precision, and never inspects output."""

    def _repo(self, *files):
        s = set(files)
        return lambda t: t in s

    def test_runner_executes_repo_script(self):
        edges = W.parse_exec_edges("bash platform/reaper/reaper.sh --dry-run",
                                   self._repo("platform/reaper/reaper.sh"))
        self.assertEqual(edges, [("bash", "referent", "platform/reaper/reaper.sh")])

    def test_no_repo_file_yields_nothing(self):
        # `ls`, `kubectl get pods` name no repo file → no edge (precision over recall)
        self.assertEqual(W.parse_exec_edges("kubectl get pods -A", self._repo("a.sh")), [])
        self.assertEqual(W.parse_exec_edges("ls -la", self._repo("a.sh")), [])

    def test_external_tool_reads_config(self):
        edges = W.parse_exec_edges("kubectl apply -f platform/namespaces.yaml",
                                   self._repo("platform/namespaces.yaml"))
        self.assertEqual(edges, [("kubectl", "referent", "platform/namespaces.yaml")])

    def test_env_prefix_and_wrappers_stripped(self):
        edges = W.parse_exec_edges("FOO=1 sudo python3 scripts/x.py", self._repo("scripts/x.py"))
        self.assertEqual(edges, [("python3", "referent", "scripts/x.py")])

    def test_pipeline_segments_split(self):
        edges = W.parse_exec_edges("cat a | bash run.sh && python3 t.py",
                                   self._repo("run.sh", "t.py"))
        self.assertIn(("bash", "referent", "run.sh"), edges)
        self.assertIn(("python3", "referent", "t.py"), edges)

    def test_predicate_by_target_kind(self):
        self.assertEqual(W._predicate_for("reaper.sh"), "executes")
        self.assertEqual(W._predicate_for("bin/tool"), "executes")     # no ext → executes
        self.assertEqual(W._predicate_for("config.yaml"), "reads")

    def test_verifier_regex_is_build_tool_agnostic(self):
        vre = W.verifier_re_from_env()
        for pos in ("make test", "npm test", "pytest tests/", "bash x_test.sh", "shellcheck a.sh", "make lint"):
            self.assertTrue(vre.search(pos), pos)
        for neg in ("make build", "python3 deploy.py", "kubectl get pods", "ls contest/"):
            self.assertFalse(vre.search(neg), neg)


class TestExecutionChannel(Base):
    """The execution channel raises observed_conf; only a verifier's exit moves normative."""

    def _one(self, subj, pred, obj):
        r = self.wm.conn.execute(
            "SELECT i.* FROM interaction i JOIN entity s ON s.id=i.subject_id "
            "JOIN entity o ON o.id=i.object_id WHERE s.name=? AND i.predicate=? AND o.path=?",
            (subj, pred, obj)).fetchone()
        return r

    def test_plain_execution_is_observation_only(self):
        # exit 0 but NOT a verifier → runtime observation, normative stays 0
        c = self.wm.observe_execution("bash deploy.sh", exit_code=0,
                                      is_repo_file=lambda t: t == "deploy.sh")
        self.assertEqual(c["edges"], 1)
        self.assertEqual(c["oracle"], 0)
        r = self._one("bash", "executes", "deploy.sh")
        self.assertGreater(r["observed_conf"], 0.0)
        self.assertEqual(r["normative_conf"], 0.0)
        self.assertEqual(r["validation"], "unverified")

    def test_green_verifier_validates(self):
        c = self.wm.observe_execution("bash reaper_test.sh", exit_code=0,
                                      is_repo_file=lambda t: t == "reaper_test.sh")
        self.assertEqual(c["oracle"], 1)
        r = self._one("bash", "executes", "reaper_test.sh")
        self.assertGreaterEqual(r["normative_conf"], W.TAU_VALIDATE)
        self.assertEqual(r["validation"], "validated")

    def test_red_verifier_contradicts(self):
        r0 = self.wm.observe_execution("pytest tests/test_x.py", exit_code=1,
                                       is_repo_file=lambda t: t == "tests/test_x.py")
        self.assertEqual(r0["oracle"], 1)
        r = self._one("pytest", "executes", "tests/test_x.py")
        self.assertEqual(r["validation"], "contradicted")

    def test_unknown_exit_skips_oracle(self):
        c = self.wm.observe_execution("bash a_test.sh", exit_code=None,
                                      is_repo_file=lambda t: t == "a_test.sh")
        self.assertEqual(c["oracle"], 0)
        self.assertEqual(self._one("bash", "executes", "a_test.sh")["normative_conf"], 0.0)

    def test_idempotent_reruns(self):
        f = lambda t: t == "run.sh"   # noqa: E731
        self.wm.observe_execution("bash run.sh", exit_code=0, is_repo_file=f)
        self.wm.observe_execution("bash run.sh", exit_code=0, is_repo_file=f)
        n = self.wm.conn.execute("SELECT COUNT(*) c FROM interaction WHERE predicate='executes'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_extract_from_hook_command_and_exit(self):
        raw = '{"tool_input":{"command":"bash x.sh"},"tool_response":{"exit_code":0}}'
        self.assertEqual(W.extract_exec_from_hook(raw), ("bash x.sh", 0))
        raw2 = '{"tool_input":{"command":"pytest"},"tool_response":{"is_error":true}}'
        self.assertEqual(W.extract_exec_from_hook(raw2), ("pytest", 1))
        self.assertEqual(W.extract_exec_from_hook("not json"), ("", None))


class TestUniversalCapture(Base):
    """observe_tool registers what ANY tool reveals — from input only, scoped to the repo."""

    def _mkfile(self, rel, body="x"):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(body)
        return p

    def test_read_registers_repo_file_entity(self):
        p = self._mkfile("src/a.py")
        c = self.wm.observe_tool("Read", {"file_path": p}, root=self.tmp)
        self.assertEqual(c["files"], 1)
        r = self.wm.conn.execute("SELECT kind FROM entity WHERE path=?", ("src/a.py",)).fetchone()
        self.assertEqual(r["kind"], "file")

    def test_external_file_not_registered(self):
        ext = os.path.join(tempfile.mkdtemp(), "outside.py")
        with open(ext, "w") as fh:
            fh.write("x")
        c = self.wm.observe_tool("Read", {"file_path": ext}, root=self.tmp)
        self.assertEqual(c["files"], 0)   # outside the repo root → ignored, no littering

    def test_nonexistent_path_ignored(self):
        c = self.wm.observe_tool("Read", {"file_path": os.path.join(self.tmp, "ghost.py")}, root=self.tmp)
        self.assertEqual(c["files"], 0)

    def test_webfetch_registers_referent(self):
        c = self.wm.observe_tool("WebFetch", {"url": "https://api.stripe.com/v1/refunds"}, root=self.tmp)
        self.assertEqual(c["referents"], 1)
        r = self.wm.conn.execute("SELECT 1 FROM entity WHERE kind='referent' AND name=?",
                                 ("api.stripe.com/v1",)).fetchone()
        self.assertIsNotNone(r)

    def test_mcp_tool_with_file_path_captured(self):
        p = self._mkfile("lib/x.ts")
        c = self.wm.observe_tool("mcp__server__read_file", {"file_path": p}, root=self.tmp)
        self.assertEqual(c["files"], 1)   # future/unknown tools captured generically

    def test_bash_routes_to_execution(self):
        p = self._mkfile("run.sh")
        c = self.wm.observe_tool("Bash", {"command": "bash " + p}, root=self.tmp)
        self.assertGreaterEqual(c["edges"], 1)

    def test_extract_tool_from_hook(self):
        raw = '{"tool_name":"Read","tool_input":{"file_path":"a.py"},"tool_response":{}}'
        tn, ti, _ = W.extract_tool_from_hook(raw)
        self.assertEqual(tn, "Read")
        self.assertEqual(ti["file_path"], "a.py")
        self.assertEqual(W.extract_tool_from_hook("nonsense"), ("", {}, None))

    def test_url_referent_shapes(self):
        self.assertEqual(W._url_referent("https://x.io/a/b/c"), "x.io/a")
        self.assertEqual(W._url_referent("https://x.io"), "x.io")
        self.assertIsNone(W._url_referent("not-a-url"))


class TestBootstrap(Base):
    """bootstrap seeds an empty model from the repo, then no-ops (idempotent)."""

    def test_seeds_then_noops(self):
        self._f = os.path.join(self.tmp, "pkg", "a.py")
        os.makedirs(os.path.dirname(self._f))
        with open(self._f, "w") as fh:
            fh.write("import os\n")
        r1 = self.wm.bootstrap(self.tmp)
        self.assertTrue(r1["seeded"])
        self.assertGreater(self.wm.conn.execute("SELECT COUNT(*) c FROM entity").fetchone()["c"], 0)
        r2 = self.wm.bootstrap(self.tmp)
        self.assertFalse(r2["seeded"])   # already populated → no-op


if __name__ == "__main__":
    unittest.main(verbosity=2)
