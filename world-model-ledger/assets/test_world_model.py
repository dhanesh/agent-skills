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


class TestTwoAxisInvariant(Base):
    """The load-bearing guarantee: code observation never raises normative_conf."""

    def test_observation_raises_observed_not_normative(self):
        iid = self.wm.add_interaction("a", "calls", "b")
        self.wm.add_evidence("interaction", iid, "file_loc", "a.py:1", weight=0.9)
        self.wm.add_evidence("interaction", iid, "static", "a.py:1", weight=0.9)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
