#!/usr/bin/env python3
"""Stdlib unit suite for okf-capability-catalog (offline, deterministic).

Covers the engine's four load-bearing parts — the YAML subset, the readiness
grid, the dependency state machine, and commit-boundary enforcement — plus the
scanner helpers and the idempotent writer. Run:  python3 test_okf_catalog.py
"""
import contextlib
import datetime as _dt
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalog_core as core  # noqa: E402
import okf_catalog as cli  # noqa: E402

DAY = _dt.date(2026, 8, 14)


def write(root, rel, meta, body=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(core.render_doc(meta, body))


def team(root, team_id, provenance="claimed"):
    write(root, f"teams/{team_id}/team.md",
          {"type": "Team", "title": team_id.title(), "team_id": team_id,
           "provenance": provenance})


def capability(root, team_id, slug, **kw):
    meta = {"type": "Capability", "title": slug, "capability_id": f"{team_id}/{slug}",
            "owning_team": f"/teams/{team_id}/team.md", "lifecycle": "active"}
    meta.update(kw)
    write(root, f"teams/{team_id}/capabilities/{slug}.md", meta)
    return f"teams/{team_id}/capabilities/{slug}.md"


def dependency(root, dep_id, consumer, provider, cap_rel, **kw):
    meta = {"type": "Dependency", "title": dep_id, "dependency_id": dep_id,
            "consumer_team": f"/teams/{consumer}/team.md",
            "provider_team": f"/teams/{provider}/team.md",
            "capability": f"/{cap_rel}", "target_environment": "production",
            "state": "acknowledged"}
    meta.update(kw)
    write(root, f"dependencies/{dep_id}.md", meta)
    return f"dependencies/{dep_id}.md"


def verification(root, consumer, cap_rel, env, result="verified", kind="deployment", **kw):
    meta = {"type": "Verification", "title": f"{consumer} {result}",
            "capability": f"/{cap_rel}", "verifying_team": f"/teams/{consumer}/team.md",
            "environment": env, "result": result, "kind": kind,
            "environment_resolved_from": "ci://x/run/1", "evidence": "https://e",
            "verified_by": consumer, "verified_at": "2026-08-10T11:20:00Z"}
    meta.update(kw)
    write(root, f"teams/{consumer}/verifications/{core.slugify(cap_rel)}-{env}.md", meta)


def bundle(config_extra=None):
    root = tempfile.mkdtemp(prefix="okfcc-")
    cfg = {"okf_version": "0.1", "organization": "acme",
           "branches": {"integration": "develop", "release": "main"},
           "environments": [{"name": "staging"},
                            {"name": "production", "requires_live_signal": True}],
           "default_fallback_execution_days": 2, "on_track_window_days": 7,
           "ponr_warning_days": 7, "stale_assertion_days": 90}
    cfg.update(config_extra or {})
    with open(os.path.join(root, "catalog.config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(core.dump_yaml(cfg))
    with open(os.path.join(root, "index.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nokf_version: "0.1"\n---\n\n# Catalog\n')
    with open(os.path.join(root, "log.md"), "w", encoding="utf-8") as fh:
        fh.write("# Log\n")
    return root


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class TempBundle(unittest.TestCase):
    def setUp(self):
        self.root = bundle()
        self.addCleanup(shutil.rmtree, self.root, True)


# ── YAML subset ──────────────────────────────────────────────────────────────

class TestYaml(unittest.TestCase):
    def test_round_trip_nested(self):
        value = {"type": "Dependency", "fallback": {"description": "flag off",
                                                    "execution_days": 3},
                 "acknowledgement": {"provider": {"by": "alice", "at": "2026-08-05T10:00:00Z"}},
                 "depends_on": ["dep-1", "dep-2"], "state": "acknowledged"}
        self.assertEqual(core.parse_yaml(core.dump_yaml(value)), value)

    def test_round_trip_list_of_maps(self):
        value = {"runtimes": [{"environment": "staging", "platform": "EKS"},
                              {"environment": "production", "platform": "ECS",
                               "note": "EKS migration in progress"}]}
        self.assertEqual(core.parse_yaml(core.dump_yaml(value)), value)

    def test_url_in_list_is_not_a_mapping(self):
        parsed = core.parse_yaml("repositories:\n- https://github.example/acme/svc\n")
        self.assertEqual(parsed["repositories"], ["https://github.example/acme/svc"])

    def test_long_text_round_trips_through_folded_block(self):
        text = ("Refund UI ships dark. Ops continues manual refunds via the admin console, "
                "roughly 40 tickets a week at about 15 minutes each.")
        parsed = core.parse_yaml(core.dump_yaml({"consequence_if_late": text}))
        self.assertEqual(parsed["consequence_if_late"], text)

    def test_multiline_text_keeps_its_newlines(self):
        text = "line one\nline two"
        self.assertEqual(core.parse_yaml(core.dump_yaml({"note": text}))["note"], text)

    def test_inline_list_and_map(self):
        parsed = core.parse_yaml("envs: [staging, production]\nack: {by: alice, at: x}\n")
        self.assertEqual(parsed["envs"], ["staging", "production"])
        self.assertEqual(parsed["ack"], {"by": "alice", "at": "x"})

    def test_booleans_numbers_and_iso_dates(self):
        parsed = core.parse_yaml("attested: true\ndays: 3\nwhen: 2026-08-25\n")
        self.assertIs(parsed["attested"], True)
        self.assertEqual(parsed["days"], 3)
        self.assertEqual(parsed["when"], "2026-08-25")

    def test_hash_value_is_quoted_and_survives(self):
        parsed = core.parse_yaml(core.dump_yaml({"channel": "#team-payments"}))
        self.assertEqual(parsed["channel"], "#team-payments")

    def test_reserved_filenames_never_become_concept_slugs(self):
        # A capability slugged "index" would land on OKF-reserved furniture and
        # silently not exist.
        self.assertEqual(core.slugify("Index"), "index-capability")
        self.assertEqual(core.slugify("log"), "log-capability")

    def test_tolerant_on_garbage(self):
        self.assertIsInstance(core.parse_yaml(":\n\t- [unclosed\n"), dict)

    def test_frontmatter_split(self):
        meta, body, had = core.parse_frontmatter("---\ntype: Team\n---\n\n# Hi\n")
        self.assertTrue(had)
        self.assertEqual(meta["type"], "Team")
        self.assertEqual(body.strip(), "# Hi")

    def test_frontmatter_absent(self):
        meta, body, had = core.parse_frontmatter("# Just markdown\n")
        self.assertFalse(had)
        self.assertEqual(meta, {})

    def test_render_doc_orders_keys_by_schema(self):
        text = core.render_doc({"timestamp": "t", "type": "Capability", "title": "T",
                                "capability_id": "a/b"})
        self.assertLess(text.index("title:"), text.index("capability_id:"))
        self.assertLess(text.index("capability_id:"), text.index("timestamp:"))


# ── readiness grid ───────────────────────────────────────────────────────────

class TestReadiness(TempBundle):
    def test_provider_assertion_is_clamped(self):
        # A provider-owned file may not say consumer_verified, whatever it types.
        team(self.root, "payments")
        capability(self.root, "payments", "refund",
                   readiness=[{"environment": "production", "state": "consumer_verified"}])
        cat = core.Catalog.load(self.root)
        state, notes = core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                          "production", DAY)
        self.assertEqual(state, "provider_tested")
        self.assertTrue(any("ignored provider-asserted" in n for n in notes))

    def test_consumer_verification_raises_only_its_environment(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund")
        verification(self.root, "checkout", rel, "staging")
        cat = core.Catalog.load(self.root)
        cap = cat.capability_by_id("payments/refund")
        self.assertEqual(core.own_readiness(cat, cap, "staging", DAY)[0], "consumer_verified")
        self.assertEqual(core.own_readiness(cat, cap, "production", DAY)[0], "unknown")

    def test_contract_kind_raises_nothing(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund")
        verification(self.root, "checkout", rel, "production", kind="contract")
        cat = core.Catalog.load(self.root)
        state, notes = core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                          "production", DAY)
        self.assertEqual(state, "unknown")
        self.assertTrue(any("evidence only" in n for n in notes))

    def test_failed_verification_lowers_readiness(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund",
                         readiness=[{"environment": "production", "state": "provider_tested"}])
        verification(self.root, "checkout", rel, "production", result="failed")
        cat = core.Catalog.load(self.root)
        state, notes = core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                          "production", DAY)
        self.assertEqual(state, "provider_tested")
        self.assertTrue(any("failed verification" in n for n in notes))

    def test_failed_verification_beats_a_green_one(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        team(self.root, "orders")
        rel = capability(self.root, "payments", "refund")
        verification(self.root, "checkout", rel, "production")
        verification(self.root, "orders", rel, "production", result="failed")
        cat = core.Catalog.load(self.root)
        self.assertEqual(core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                            "production", DAY)[0], "provider_tested")

    def test_expired_verification_drops_out(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund")
        verification(self.root, "checkout", rel, "production", expires_after_days=1)
        cat = core.Catalog.load(self.root)
        cap = cat.capability_by_id("payments/refund")
        self.assertEqual(core.own_readiness(cat, cap, "production",
                                            _dt.date(2026, 8, 11))[0], "consumer_verified")
        self.assertEqual(core.own_readiness(cat, cap, "production",
                                            _dt.date(2026, 9, 30))[0], "unknown")

    def test_traffic_signal_is_the_only_source_of_production_live(self):
        team(self.root, "payments")
        rel = capability(self.root, "payments", "refund")
        write(self.root, "signals/refund-production.md",
              {"type": "Signal", "capability": f"/{rel}", "environment": "production",
               "observation": "traffic", "emitted_by": "ci://payments/deploy/1182"})
        cat = core.Catalog.load(self.root)
        self.assertEqual(core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                            "production", DAY)[0], "production_live")

    def test_effective_readiness_is_the_minimum_over_hard_closure(self):
        # Acceptance test 14: verified capability, weaker hard upstream.
        team(self.root, "payments")
        team(self.root, "ledger")
        team(self.root, "checkout")
        up = capability(self.root, "ledger", "post-entry",
                        readiness=[{"environment": "production", "state": "provider_tested"}],
                        upstream={"attested": True})
        rel = capability(self.root, "payments", "refund",
                         requires=[{"capability": f"/{up}", "criticality": "hard"}],
                         upstream={"attested": True})
        verification(self.root, "checkout", rel, "production")
        cat = core.Catalog.load(self.root)
        ready = core.effective_readiness(cat, cat.capability_by_id("payments/refund"),
                                         "production", DAY)
        self.assertEqual(ready.state, "provider_tested")
        self.assertEqual(ready.depth, "complete")
        self.assertEqual(ready.verdict, "not_ready")

    def test_soft_requires_do_not_lower_the_state(self):
        team(self.root, "payments")
        team(self.root, "search")
        team(self.root, "checkout")
        up = capability(self.root, "search", "suggest", upstream={"attested": True})
        rel = capability(self.root, "payments", "refund",
                         requires=[{"capability": f"/{up}", "criticality": "soft"}],
                         upstream={"attested": True})
        verification(self.root, "checkout", rel, "production")
        cat = core.Catalog.load(self.root)
        ready = core.effective_readiness(cat, cat.capability_by_id("payments/refund"),
                                         "production", DAY)
        self.assertEqual(ready.state, "consumer_verified")
        self.assertTrue(any("degradation risk" in n for n in ready.notes))

    def test_unattested_upstreams_make_depth_unknown_not_green(self):
        # Acceptance test 15: verified, no declared requires, nobody has looked.
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund", upstream={"attested": False})
        verification(self.root, "checkout", rel, "production")
        cat = core.Catalog.load(self.root)
        ready = core.effective_readiness(cat, cat.capability_by_id("payments/refund"),
                                         "production", DAY)
        self.assertEqual(ready.state, "consumer_verified")
        self.assertEqual(ready.depth, "unknown")
        self.assertEqual(ready.verdict, "unknown")

    def test_stub_upstream_team_makes_depth_unknown(self):
        team(self.root, "payments")
        team(self.root, "ledger", provenance="stub")
        up = capability(self.root, "ledger", "post-entry", upstream={"attested": True})
        capability(self.root, "payments", "refund", upstream={"attested": True},
                   requires=[{"capability": f"/{up}", "criticality": "hard"}])
        cat = core.Catalog.load(self.root)
        ready = core.effective_readiness(cat, cat.capability_by_id("payments/refund"),
                                         "production", DAY)
        self.assertEqual(ready.depth, "unknown")
        self.assertTrue(any("stub team" in n for n in ready.notes))

    def test_missing_upstream_document_is_reported_not_ignored(self):
        team(self.root, "payments")
        capability(self.root, "payments", "refund", upstream={"attested": True},
                   requires=[{"capability": "/teams/ghost/capabilities/x.md",
                              "criticality": "hard"}])
        cat = core.Catalog.load(self.root)
        ready = core.effective_readiness(cat, cat.capability_by_id("payments/refund"),
                                         "production", DAY)
        self.assertEqual(ready.depth, "unknown")
        self.assertTrue(any("missing from the catalog" in n for n in ready.notes))

    def test_cycle_is_safe_and_reported(self):
        # Acceptance test 18: mutual hard requires must not hang the traversal.
        team(self.root, "a")
        team(self.root, "b")
        rel_a = "teams/a/capabilities/one.md"
        rel_b = "teams/b/capabilities/two.md"
        capability(self.root, "a", "one", upstream={"attested": True},
                   requires=[{"capability": f"/{rel_b}", "criticality": "hard"}])
        capability(self.root, "b", "two", upstream={"attested": True},
                   requires=[{"capability": f"/{rel_a}", "criticality": "hard"}])
        cat = core.Catalog.load(self.root)
        ready = core.effective_readiness(cat, cat.capability_by_id("a/one"), "production", DAY)
        self.assertEqual(ready.depth, "unknown")
        self.assertTrue(core.find_cycles(cat))
        self.assertEqual(len(core.closure(cat, cat.capability_by_id("a/one"))), 2)


# ── dependency state machine ─────────────────────────────────────────────────

class TestStateMachine(TempBundle):
    def _basic(self, **dep_kw):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund", upstream={"attested": True})
        kw = {"promised_date": "2026-09-01",
              "fallback": {"description": "flag", "execution_days": 3}}
        kw.update(dep_kw)
        dependency(self.root, "dep-1", "checkout", "payments", rel, **kw)
        return core.Catalog.load(self.root)

    def test_ponr_arithmetic(self):
        cat = self._basic()
        dep = cat.dependencies["dep-1"]
        self.assertEqual(core.point_of_no_return(dep, cat.config), _dt.date(2026, 8, 29))

    def test_ponr_uses_the_org_default_when_unestimated(self):
        cat = self._basic(fallback=None)
        dep = cat.dependencies["dep-1"]
        self.assertEqual(core.point_of_no_return(dep, cat.config), _dt.date(2026, 8, 30))
        self.assertTrue(core.uses_default_fallback_days(dep))

    def test_edge_trips_by_itself_after_the_ponr(self):
        cat = self._basic()
        dep = cat.dependencies["dep-1"]
        self.assertEqual(core.effective_state(cat, dep, _dt.date(2026, 8, 28)).state,
                         "acknowledged")
        self.assertEqual(core.effective_state(cat, dep, _dt.date(2026, 8, 30)).state, "tripped")

    def test_on_track_confirmation_inside_the_window_prevents_the_trip(self):
        cat = self._basic(on_track={"confirmed_by": "alice", "confirmed_at": "2026-08-27"})
        self.assertEqual(core.effective_state(cat, cat.dependencies["dep-1"],
                                              _dt.date(2026, 8, 30)).state, "acknowledged")

    def test_stale_confirmation_does_not_count(self):
        cat = self._basic(on_track={"confirmed_by": "alice", "confirmed_at": "2026-07-01"})
        self.assertEqual(core.effective_state(cat, cat.dependencies["dep-1"],
                                              _dt.date(2026, 8, 30)).state, "tripped")

    def test_terminal_states_do_not_trip(self):
        cat = self._basic(state="fallback_invoked")
        self.assertEqual(core.effective_state(cat, cat.dependencies["dep-1"],
                                              _dt.date(2026, 9, 30)).state, "fallback_invoked")

    def test_trip_propagates_two_hops_downstream(self):
        # Acceptance tests 5 and 16: no human relays the news.
        team(self.root, "a")
        team(self.root, "b")
        team(self.root, "c")
        cap_a = capability(self.root, "a", "one", upstream={"attested": True})
        cap_b = capability(self.root, "b", "two", upstream={"attested": True})
        dependency(self.root, "dep-up", "b", "a", cap_a, promised_date="2026-08-20",
                   fallback={"description": "x", "execution_days": 1})
        dependency(self.root, "dep-mid", "c", "b", cap_b, promised_date="2026-10-01",
                   fallback={"description": "x", "execution_days": 1},
                   depends_on=["dep-up"])
        dependency(self.root, "dep-down", "c", "b", cap_b, promised_date="2026-11-01",
                   fallback={"description": "x", "execution_days": 1},
                   depends_on=["dep-mid"])
        cat = core.Catalog.load(self.root)
        today = _dt.date(2026, 8, 25)
        self.assertEqual(core.effective_state(cat, cat.dependencies["dep-up"], today).state,
                         "tripped")
        for dep_id in ("dep-mid", "dep-down"):
            state = core.effective_state(cat, cat.dependencies[dep_id], today)
            self.assertEqual(state.state, "at_risk")
            self.assertEqual(state.originator, "dep-up")

    def test_propagation_follows_capability_hard_requires(self):
        team(self.root, "a")
        team(self.root, "b")
        team(self.root, "c")
        cap_a = capability(self.root, "a", "one", upstream={"attested": True})
        cap_b = capability(self.root, "b", "two", upstream={"attested": True},
                           requires=[{"capability": f"/{cap_a}", "criticality": "hard"}])
        dependency(self.root, "dep-up", "b", "a", cap_a, promised_date="2026-08-20",
                   fallback={"description": "x", "execution_days": 1})
        dependency(self.root, "dep-down", "c", "b", cap_b, promised_date="2026-11-01",
                   fallback={"description": "x", "execution_days": 1})
        cat = core.Catalog.load(self.root)
        state = core.effective_state(cat, cat.dependencies["dep-down"], _dt.date(2026, 8, 25))
        self.assertEqual((state.state, state.originator), ("at_risk", "dep-up"))

    def test_dependency_cycle_does_not_hang(self):
        team(self.root, "a")
        team(self.root, "b")
        cap_a = capability(self.root, "a", "one")
        cap_b = capability(self.root, "b", "two")
        dependency(self.root, "dep-1", "a", "b", cap_b, depends_on=["dep-2"],
                   promised_date="2026-09-01")
        dependency(self.root, "dep-2", "b", "a", cap_a, depends_on=["dep-1"],
                   promised_date="2026-09-01")
        cat = core.Catalog.load(self.root)
        self.assertIn(core.effective_state(cat, cat.dependencies["dep-1"],
                                           _dt.date(2026, 8, 14)).state,
                      core.DEPENDENCY_STATES)

    def test_satisfied_requires_effective_readiness_not_a_provider_claim(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund", upstream={"attested": True},
                         readiness=[{"environment": "production", "state": "provider_tested"}])
        dependency(self.root, "dep-1", "checkout", "payments", rel)
        cat = core.Catalog.load(self.root)
        ok, why = core.can_satisfy(cat, cat.dependencies["dep-1"], DAY)
        self.assertFalse(ok)
        self.assertIn("consumer_verified", why)
        verification(self.root, "checkout", rel, "production")
        cat = core.Catalog.load(self.root)
        ok, _ = core.can_satisfy(cat, cat.dependencies["dep-1"], DAY)
        self.assertTrue(ok)

    def test_impossible_promise_is_arithmetic(self):
        # Acceptance test 17.
        team(self.root, "a")
        team(self.root, "b")
        team(self.root, "c")
        cap_up = capability(self.root, "a", "one", upstream={"attested": True})
        cap = capability(self.root, "b", "two", upstream={"attested": True},
                         requires=[{"capability": f"/{cap_up}", "criticality": "hard"}])
        dependency(self.root, "dep-up", "b", "a", cap_up, promised_date="2026-09-05")
        dependency(self.root, "dep-down", "c", "b", cap, promised_date="2026-09-01")
        cat = core.Catalog.load(self.root)
        clashes = core.impossible_promise(cat, cat.dependencies["dep-down"])
        self.assertEqual([d.get("dependency_id") for d, _ in clashes], ["dep-up"])
        self.assertEqual(core.impossible_promise(cat, cat.dependencies["dep-up"]), [])


# ── audit + validate ─────────────────────────────────────────────────────────

class TestAudit(TempBundle):
    def _eks_case(self):
        """The canonical incident, reverse-engineered into a fixture."""
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund", upstream={"attested": True},
                         fulfilled_by=["/teams/payments/services/orchestrator.md"],
                         readiness=[{"environment": "production", "state": "provider_tested"}])
        write(self.root, "teams/payments/services/orchestrator.md",
              {"type": "Service", "title": "Orchestrator",
               "service_id": "payments/orchestrator",
               "owning_team": "/teams/payments/team.md", "fulfils": [f"/{rel}"],
               "runtimes": [{"environment": "staging", "platform": "EKS"},
                            {"environment": "production", "platform": "ECS"}]})
        dependency(self.root, "dep-1", "checkout", "payments", rel,
                   promised_date="2026-09-01", requested_date="2026-08-25",
                   consequence_if_late="Refund UI ships dark.",
                   fallback={"description": "flag", "execution_days": 3})
        return core.Catalog.load(self.root)

    def test_eks_case_surfaces_on_two_independent_rules_before_the_date(self):
        # Acceptance test 1.
        cat = self._eks_case()
        codes = {f.code for f in core.audit(cat, _dt.date(2026, 8, 25))}
        self.assertIn("CC-PROVIDER-ONLY", codes)
        self.assertIn("CC-PLATFORM-DRIFT", codes)
        self.assertGreaterEqual(len({"CC-PROVIDER-ONLY", "CC-PLATFORM-DRIFT",
                                     "CC-NOT-VERIFIED"} & codes), 2)

    def test_tripped_edge_is_reported_as_an_unresolved_decision(self):
        cat = self._eks_case()
        findings = core.audit(cat, _dt.date(2026, 8, 30))
        self.assertEqual(findings[0].code, "CC-TRIPPED")
        self.assertIn("decision", findings[0].message)

    def test_detected_edges_are_reported_as_unmanaged(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund")
        dependency(self.root, "dep-1", "checkout", "payments", rel, state="detected")
        cat = core.Catalog.load(self.root)
        self.assertIn("CC-DETECTED", {f.code for f in core.audit(cat, DAY)})

    def test_stub_provider_blocks_and_is_reported(self):
        team(self.root, "checkout")
        team(self.root, "ledger", provenance="stub")
        rel = capability(self.root, "ledger", "post-entry")
        dependency(self.root, "dep-1", "checkout", "ledger", rel, state="proposed")
        cat = core.Catalog.load(self.root)
        self.assertIn("CC-STUB-PROVIDER", {f.code for f in core.audit(cat, DAY)})

    def test_human_asserted_liveness_is_a_policy_violation(self):
        # Acceptance test 23.
        team(self.root, "payments")
        capability(self.root, "payments", "refund",
                   readiness=[{"environment": "production", "state": "production_live",
                               "method": "human"}])
        cat = core.Catalog.load(self.root)
        findings = [f for f in core.audit(cat, DAY) if f.code == "CC-HUMAN-LIVENESS"]
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, "high")

    def test_signal_from_a_human_source_is_rejected(self):
        team(self.root, "payments")
        rel = capability(self.root, "payments", "refund")
        write(self.root, "signals/x.md",
              {"type": "Signal", "capability": f"/{rel}", "environment": "production",
               "observation": "traffic", "emitted_by": "alice"})
        cat = core.Catalog.load(self.root)
        self.assertIn("CC-HUMAN-LIVENESS", {f.code for f in core.audit(cat, DAY)})

    def test_failed_verification_is_first_class(self):
        # Acceptance test 22.
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund")
        verification(self.root, "checkout", rel, "production", result="failed")
        cat = core.Catalog.load(self.root)
        self.assertIn("CC-FAILED-VERIFICATION", {f.code for f in core.audit(cat, DAY)})

    def test_fog_and_coverage_are_reported(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund", upstream={"attested": False})
        dependency(self.root, "dep-1", "checkout", "payments", rel,
                   consequence_if_late="x", fallback={"description": "y", "execution_days": 1})
        cat = core.Catalog.load(self.root)
        codes = {f.code for f in core.audit(cat, DAY)}
        self.assertIn("CC-FOG", codes)
        self.assertIn("CC-COVERAGE", codes)
        self.assertEqual(core.closure_coverage(cat, "checkout"), (0, 1))

    def test_capability_on_a_feature_branch_under_a_dependency(self):
        team(self.root, "payments")
        team(self.root, "checkout")
        rel = capability(self.root, "payments", "refund", upstream={"attested": True},
                         code_maturity={"state": "feature", "branch": "feat/x"})
        dependency(self.root, "dep-1", "checkout", "payments", rel)
        cat = core.Catalog.load(self.root)
        self.assertIn("CC-FEATURE-BRANCH", {f.code for f in core.audit(cat, DAY)})

    def test_validation_tolerates_broken_links_and_unknown_keys(self):
        team(self.root, "payments")
        capability(self.root, "payments", "refund", fulfilled_by=["/teams/x/services/y.md"],
                   some_producer_extension={"anything": True})
        cat = core.Catalog.load(self.root)
        hard, soft = core.validate(cat, DAY)
        self.assertEqual(hard, [])
        self.assertIn("CC-BROKEN-LINK", {f.code for f in core.audit(cat, DAY)})

    def test_validation_fails_hard_on_a_typeless_document(self):
        write(self.root, "teams/x/thing.md", {"title": "no type"})
        cat = core.Catalog.load(self.root)
        hard, _ = core.validate(cat, DAY)
        self.assertTrue(any("teams/x/thing.md" in h for h in hard))


# ── commit-boundary enforcement ──────────────────────────────────────────────

class TestEnforce(TempBundle):
    def setUp(self):
        super().setUp()
        team(self.root, "payments")
        team(self.root, "checkout")
        self.cap = capability(self.root, "payments", "refund")
        self.cat = core.Catalog.load(self.root)

    def _verification_text(self, verifying="checkout", **kw):
        meta = {"type": "Verification", "title": "v", "capability": f"/{self.cap}",
                "verifying_team": f"/teams/{verifying}/team.md", "environment": "production",
                "result": "verified", "kind": "deployment", "verified_by": "bob",
                "verified_at": "2026-08-10T11:20:00Z"}
        meta.update(kw)
        return core.render_doc(meta)

    def test_provider_authored_verification_fails(self):
        # Acceptance test 19: the highest-signal check.
        commits = [{"sha": "abc", "author_handle": "alice", "author_team": "payments",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "teams/checkout/verifications/v.md",
                               "content": self._verification_text()},
                              {"path": self.cap,
                               "content": core.render_doc({"type": "Capability",
                                                           "capability_id": "payments/refund",
                                                           "owning_team": "/teams/payments/team.md"})}]}]
        codes = {v.code for v in core.enforce(self.cat, commits)}
        self.assertIn("EN-FOREIGN-VERIFICATION", codes)
        messages = " ".join(v.message for v in core.enforce(self.cat, commits))
        self.assertIn("payments", messages)

    def test_self_verification_by_the_owning_team_fails(self):
        commits = [{"sha": "abc", "author_handle": "alice", "author_team": "payments",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "teams/payments/verifications/v.md",
                               "content": self._verification_text(verifying="payments",
                                                                  verified_by="alice")}]}]
        self.assertIn("EN-SELF-VERIFICATION", {v.code for v in core.enforce(self.cat, commits)})

    def test_honest_consumer_verification_passes(self):
        commits = [{"sha": "abc", "author_handle": "bob", "author_team": "checkout",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "teams/checkout/verifications/v.md",
                               "content": self._verification_text()}]}]
        self.assertEqual(core.enforce(self.cat, commits), [])

    def test_backdating_and_author_mismatch_are_reported(self):
        # Acceptance test 20.
        commits = [{"sha": "abc", "author_handle": "carol", "author_team": "checkout",
                    "committed_at": "2026-09-30T12:00:00Z",
                    "files": [{"path": "teams/checkout/verifications/v.md",
                               "content": self._verification_text()}]}]
        codes = {v.code for v in core.enforce(self.cat, commits)}
        self.assertIn("EN-BACKDATED", codes)
        self.assertIn("EN-AUTHOR-MISMATCH", codes)

    def test_production_live_outside_signals_is_rejected(self):
        text = core.render_doc({"type": "Capability", "capability_id": "payments/refund",
                                "readiness": [{"environment": "production",
                                               "state": "production_live"}]})
        commits = [{"sha": "abc", "author_handle": "alice", "author_team": "payments",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": self.cap, "content": text}]}]
        self.assertIn("EN-LIVENESS-OUTSIDE-SIGNALS",
                      {v.code for v in core.enforce(self.cat, commits)})

    def test_signals_written_by_a_non_platform_team_are_rejected(self):
        cat = core.Catalog.load(self.root)
        cat.config["platform_team"] = "platform"
        commits = [{"sha": "abc", "author_handle": "alice", "author_team": "payments",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "signals/x.md",
                               "content": core.render_doc({"type": "Signal"})}]}]
        self.assertIn("EN-SIGNAL-AUTHOR", {v.code for v in core.enforce(cat, commits)})

    def _dep_texts(self, **changes):
        base = {"type": "Dependency", "dependency_id": "dep-1",
                "consumer_team": "/teams/checkout/team.md",
                "provider_team": "/teams/payments/team.md",
                "capability": f"/{self.cap}", "requested_date": "2026-08-25",
                "promised_date": "2026-09-01", "consequence_if_late": "UI ships dark",
                "state": "acknowledged"}
        after = dict(base)
        after.update(changes)
        return core.render_doc(base), core.render_doc(after)

    def test_provider_may_not_rewrite_the_consumers_fields(self):
        before, after = self._dep_texts(consequence_if_late="not a big deal actually")
        commits = [{"sha": "abc", "author_handle": "alice", "author_team": "payments",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "dependencies/dep-1.md", "content": after,
                               "previous": before}]}]
        self.assertIn("EN-CROSS-SIDE-EDIT", {v.code for v in core.enforce(self.cat, commits)})

    def test_consumer_may_not_rewrite_the_promised_date(self):
        before, after = self._dep_texts(promised_date="2026-08-20")
        commits = [{"sha": "abc", "author_handle": "bob", "author_team": "checkout",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "dependencies/dep-1.md", "content": after,
                               "previous": before}]}]
        self.assertIn("EN-CROSS-SIDE-EDIT", {v.code for v in core.enforce(self.cat, commits)})

    def test_nobody_acknowledges_on_the_other_sides_behalf(self):
        before, after = self._dep_texts(acknowledgement={"provider": {"by": "bob"}})
        commits = [{"sha": "abc", "author_handle": "bob", "author_team": "checkout",
                    "committed_at": "2026-08-10T12:00:00Z",
                    "files": [{"path": "dependencies/dep-1.md", "content": after,
                               "previous": before}]}]
        self.assertIn("EN-PROXY-ACK", {v.code for v in core.enforce(self.cat, commits)})


# ── scanner helpers and the writer ───────────────────────────────────────────

class TestScannerHelpers(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="okfcc-repo-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    def _write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_capability_id_scheme(self):
        self.assertEqual(cli.full_cap_id("payments", {"id": "initiate-refund"}),
                         "payments/initiate-refund")
        self.assertEqual(cli.full_cap_id("payments", {"id": "ledger/post-entry"}),
                         "ledger/post-entry")
        self.assertEqual(cli.full_cap_id("payments", {"title": "Initiate Refund"}),
                         "payments/initiate-refund")

    def test_runtime_inference_is_per_environment(self):
        self._write("terraform/production/main.tf",
                    'resource "aws_ecs_service" "svc" {}\n')
        self._write("k8s/staging/deploy.yaml", "kind: Deployment\napiVersion: apps/v1\n")
        runtimes = cli.infer_runtimes(self.repo, ["staging", "production"])
        found = {r["environment"]: r["platform"] for r in runtimes}
        self.assertEqual(found, {"staging": "EKS", "production": "ECS"})

    def test_interface_inference_from_openapi_and_asyncapi(self):
        self._write("api/openapi.yaml",
                    "openapi: 3.0.0\npaths:\n  /internal/refunds:\n    post:\n"
                    "      summary: create\n")
        self._write("api/asyncapi.yaml",
                    "asyncapi: 2.0.0\nchannels:\n  payments.refund.v1:\n    publish:\n"
                    "      summary: emitted\n  payments.settlement.v1:\n    subscribe:\n"
                    "      summary: consumed\n")
        consumes, produces, sources = cli.infer_interfaces(self.repo)
        self.assertIn({"kind": "kafka_topic", "name": "payments.settlement.v1"}, consumes)
        self.assertIn({"kind": "kafka_topic", "name": "payments.refund.v1"}, produces)
        self.assertIn({"kind": "http", "name": "POST /internal/refunds"}, produces)
        self.assertEqual(len(sources), 2)

    def test_team_resolution_falls_back_to_codeowners(self):
        self._write("CODEOWNERS", "# comment\n* @acme/payments\n")
        cat = core.Catalog(self.repo)
        self.assertEqual(cli.resolve_team(cat, self.repo, None).get("team_id"), "payments")

    def test_contract_change_is_detected_not_overwritten(self):
        existing = core.Doc("x.md", {"inputs": [{"name": "payment_id"}]}, "")
        self.assertFalse(cli.contract_differs(existing, {"inputs": [{"name": "payment_id"}]}))
        self.assertTrue(cli.contract_differs(existing, {"inputs": [{"name": "other"}]}))


class TestWriter(TempBundle):
    def test_write_doc_is_idempotent_across_clocks(self):
        meta = {"type": "Team", "title": "Payments", "team_id": "payments",
                "timestamp": "2026-08-12T09:00:00Z"}
        with contextlib.redirect_stdout(io.StringIO()):
            first = cli.write_doc(self.root, "teams/payments/team.md", meta)
            meta2 = dict(meta, timestamp="2026-09-30T09:00:00Z")
            second = cli.write_doc(self.root, "teams/payments/team.md", meta2)
        self.assertTrue(first)
        self.assertFalse(second)
        with open(os.path.join(self.root, "teams/payments/team.md"), encoding="utf-8") as fh:
            self.assertIn("2026-08-12T09:00:00Z", fh.read())

    def test_write_doc_notices_a_real_change(self):
        meta = {"type": "Team", "title": "Payments", "team_id": "payments"}
        with contextlib.redirect_stdout(io.StringIO()):
            cli.write_doc(self.root, "teams/payments/team.md", meta)
            changed = cli.write_doc(self.root, "teams/payments/team.md",
                                    dict(meta, provenance="claimed"))
        self.assertTrue(changed)

    def test_log_appends_once_per_event_per_day(self):
        now = _dt.datetime(2026, 8, 12, 9, tzinfo=_dt.timezone.utc)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.append_log(self.root, ["Team payments claimed itself."], now)
            cli.append_log(self.root, ["Team payments claimed itself."], now)
            cli.append_log(self.root, ["Second event."], now)
        with open(os.path.join(self.root, "log.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("Team payments claimed itself."), 1)
        self.assertIn("## 2026-08-12", text)
        self.assertIn("Second event.", text)


# ── CLI authority guards ─────────────────────────────────────────────────────

class TestCliGuards(TempBundle):
    def setUp(self):
        super().setUp()
        team(self.root, "payments")
        team(self.root, "checkout")
        self.cap = capability(self.root, "payments", "refund", upstream={"attested": True})

    def test_declare_refuses_a_promised_date(self):
        # Acceptance test 12: refuse, never silently drop.
        code, text = run_cli("declare", self.root, "--consumer", "checkout",
                             "--capability", "payments/refund", "--requested-date", "2026-09-01",
                             "--consequence", "UI dark", "--fallback", "flag",
                             "--fallback-days", "2", "--promised-date", "2026-09-05")
        self.assertEqual(code, 2)
        self.assertIn("DECLARE_RESULT: REFUSED", text)
        self.assertIn("provider's commitment", text)

    def test_declare_refuses_a_blank_consequence(self):
        # Acceptance test 13.
        code, text = run_cli("declare", self.root, "--consumer", "checkout",
                             "--capability", "payments/refund", "--requested-date", "2026-09-01",
                             "--consequence", "TBD", "--fallback", "flag", "--fallback-days", "2")
        self.assertEqual(code, 2)
        self.assertIn("accepted risk", text)

    def test_declare_records_no_fallback_with_a_rationale(self):
        code, text = run_cli("declare", self.root, "--consumer", "checkout",
                             "--capability", "payments/refund", "--requested-date", "2026-09-01",
                             "--consequence", "Refunds stop entirely.",
                             "--no-fallback", "Nothing else can post to the ledger.",
                             "--now", "2026-08-12T09:00:00Z")
        self.assertEqual(code, 0)
        cat = core.Catalog.load(self.root)
        dep = [d for d in cat.by_type("Dependency")][0]
        self.assertIsNone(dep.meta.get("fallback"))
        self.assertIn("ledger", dep.get("no_fallback_rationale"))
        self.assertIn("red flag surfaced weeks early", text)

    def test_declare_refuses_a_fallback_with_no_execution_time(self):
        code, _ = run_cli("declare", self.root, "--consumer", "checkout",
                          "--capability", "payments/refund", "--requested-date", "2026-09-01",
                          "--consequence", "UI dark", "--fallback", "flag")
        self.assertEqual(code, 2)

    def test_ack_refuses_consumer_owned_fields(self):
        run_cli("declare", self.root, "--consumer", "checkout", "--capability",
                "payments/refund", "--requested-date", "2026-09-01", "--consequence",
                "UI dark", "--fallback", "flag", "--fallback-days", "2",
                "--now", "2026-08-12T09:00:00Z")
        cat = core.Catalog.load(self.root)
        dep_id = cat.by_type("Dependency")[0].get("dependency_id")
        code, text = run_cli("ack", self.root, "--dependency", dep_id, "--team", "payments",
                             "--promised-date", "2026-09-01", "--by", "alice",
                             "--fallback", "something else")
        self.assertEqual(code, 2)
        self.assertIn("belongs to the consumer", text)

    def test_verify_refuses_an_undeclared_consumer(self):
        code, text = run_cli("verify", self.root, "--team", "checkout", "--capability",
                             "payments/refund", "--environment", "production", "--result",
                             "verified", "--kind", "manual", "--evidence", "https://e",
                             "--by", "bob")
        self.assertEqual(code, 2)
        self.assertIn("not a declared consumer", text)

    def test_a_teams_verification_for_an_environment_is_one_document(self):
        # Re-verifying replaces that team's own earlier record for the
        # environment, so a later failure cannot sit beside a stale green tick.
        run_cli("declare", self.root, "--consumer", "checkout", "--capability",
                "payments/refund", "--requested-date", "2026-09-01", "--consequence",
                "UI dark", "--fallback", "flag", "--fallback-days", "2",
                "--now", "2026-08-12T09:00:00Z")
        args = ["verify", self.root, "--team", "checkout", "--capability", "payments/refund",
                "--environment", "production", "--kind", "manual", "--evidence", "https://e",
                "--by", "bob"]
        run_cli(*args, "--result", "verified", "--now", "2026-08-13T09:00:00Z")
        cat = core.Catalog.load(self.root)
        self.assertEqual(core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                            "production", DAY)[0], "consumer_verified")
        run_cli(*args, "--result", "failed", "--now", "2026-08-14T09:00:00Z")
        cat = core.Catalog.load(self.root)
        self.assertEqual(len(cat.verifications), 1)
        self.assertEqual(core.own_readiness(cat, cat.capability_by_id("payments/refund"),
                                            "production", DAY)[0], "unknown")

    def test_signal_refuses_a_human_source(self):
        code, text = run_cli("signal", self.root, "--capability", "payments/refund",
                             "--environment", "production", "--observation", "traffic",
                             "--emitted-by", "alice")
        self.assertEqual(code, 2)
        self.assertIn("machine source", text)

    def test_tested_refuses_a_team_that_does_not_own_the_capability(self):
        code, text = run_cli("tested", self.root, "--team", "checkout", "--capability",
                             "payments/refund", "--environment", "production")
        self.assertEqual(code, 2)
        self.assertIn("does not own", text)

    def test_init_creates_no_teams(self):
        # Acceptance test 9.
        target = os.path.join(self.root, "fresh")
        code, text = run_cli("init", target, "--org", "acme", "--now", "2026-08-12T09:00:00Z")
        self.assertEqual(code, 0)
        cat = core.Catalog.load(target)
        self.assertEqual(cat.by_type("Team"), [])
        self.assertIn("self-authoring", text)

    def test_config_portability_trunk_based(self):
        # Acceptance test 8.
        target = os.path.join(self.root, "trunk")
        run_cli("init", target, "--org", "acme", "--integration", "main", "--release", "main",
                "--environments", "test,prod", "--live-signal-env", "prod",
                "--now", "2026-08-12T09:00:00Z")
        cat = core.Catalog.load(target)
        self.assertEqual(cat.config["branches"]["integration"], "main")
        self.assertEqual(cat.env_names(), ["test", "prod"])
        self.assertTrue(cat.env_config("prod")["requires_live_signal"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
