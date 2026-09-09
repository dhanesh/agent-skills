"""Unit suite for rank_risk.py. Stdlib only, offline, deterministic."""
import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("rank_risk", os.path.join(HERE, "rank_risk.py"))
rank_risk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rank_risk)


def write(root, rel, text):
    p = pathlib.Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-")


class TestDiscoverUnits(TempRepo):
    def test_finds_module_level_functions_and_classes(self):
        write(self.root, "billing/refund.py",
              "def apply(amount):\n    return amount\n\n\nclass Ledger:\n    pass\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual([u["id"] for u in units],
                         ["billing/refund.py::Ledger", "billing/refund.py::apply"])
        self.assertEqual(units[1]["kind"], "function")
        self.assertEqual(units[0]["kind"], "class")
        self.assertEqual(units[1]["lineno"], 1)

    def test_skips_private_names_nested_defs_test_files_and_vendor(self):
        write(self.root, "app.py",
              "def _helper():\n    pass\n\n\ndef outer():\n    def inner():\n        pass\n    return inner\n")
        write(self.root, "tests/test_app.py", "def test_outer():\n    pass\n")
        write(self.root, "node_modules/pkg/mod.py", "def vendored():\n    pass\n")
        write(self.root, ".venv/lib/mod.py", "def alsovendored():\n    pass\n")
        ids = [u["id"] for u in rank_risk.discover_units(self.root)]
        self.assertEqual(ids, ["app.py::outer"])

    def test_unparseable_file_is_skipped_not_fatal(self):
        write(self.root, "broken.py", "def (((\n")
        write(self.root, "ok.py", "def fine():\n    pass\n")
        ids = [u["id"] for u in rank_risk.discover_units(self.root)]
        self.assertEqual(ids, ["ok.py::fine"])


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, capture_output=True, text=True)


class TestChurn(TempRepo):
    def test_counts_commits_per_file(self):
        if git(self.root, "init", "-q", ".").returncode != 0:
            self.skipTest("git unavailable")
        write(self.root, "hot.py", "def a():\n    pass\n")
        write(self.root, "cold.py", "def b():\n    pass\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-qm", "1")
        for i in range(3):
            write(self.root, "hot.py", f"def a():\n    return {i}\n")
            git(self.root, "add", "-A"); git(self.root, "commit", "-qm", f"c{i}")
        c = rank_risk.churn(self.root)
        self.assertEqual(c["hot.py"], 4)
        self.assertEqual(c["cold.py"], 1)

    def test_no_git_history_degrades_to_empty_not_a_crash(self):
        # A tarball checkout, or a brand-new directory, must not take the ranker
        # down — churn is one signal of two, and the other still works.
        write(self.root, "a.py", "def a():\n    pass\n")
        self.assertEqual(rank_risk.churn(self.root), {})

    def test_counts_a_filename_containing_a_quote(self):
        # git C-quotes filenames with a quote, backslash, or non-ASCII byte in
        # its default `--name-only` output (e.g. `"quo\"te.py"`); without `-z`
        # that quoted key never matches the plain path iter_py_files produces,
        # and the file's churn silently reads as 0.
        if git(self.root, "init", "-q", ".").returncode != 0:
            self.skipTest("git unavailable")
        rel = 'quo"te.py'
        try:
            write(self.root, rel, "def a():\n    pass\n")
        except OSError:
            self.skipTest("filesystem rejects quote in filename")
        git(self.root, "add", "-A")
        r = git(self.root, "commit", "-qm", "1")
        if r.returncode != 0:
            self.skipTest("git rejects quote in filename")
        c = rank_risk.churn(self.root)
        self.assertEqual(c.get(rel), 1)


class TestInboundRefs(TempRepo):
    def test_counts_references_outside_the_defining_file(self):
        write(self.root, "core.py", "def widely_used():\n    pass\n\n\ndef lonely():\n    pass\n")
        write(self.root, "a.py", "from core import widely_used\nwidely_used()\n")
        write(self.root, "b.py", "import core\ncore.widely_used()\n")
        units = rank_risk.discover_units(self.root)
        refs = rank_risk.inbound_refs(self.root, units)
        self.assertEqual(refs["core.py::widely_used"], 3)   # import + 2 call sites
        self.assertEqual(refs["core.py::lonely"], 0)

    def test_does_not_count_the_definition_itself(self):
        # Semantics changed by ruling: intra-module references now count, so the
        # self-reference inside `solo`'s own body ("return solo") is a real
        # intra-module use and counts as 1. Only the definition LINE itself
        # ("def solo():") is excluded, not the whole defining file.
        write(self.root, "core.py", "def solo():\n    return solo\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::solo"], 1)

    def test_intra_file_calls_are_counted(self):
        write(self.root, "core.py",
              "def helper():\n    pass\n\n\ndef a():\n    helper()\n\n\ndef b():\n    helper()\n\n\ndef c():\n    helper()\n")
        units = rank_risk.discover_units(self.root)
        refs = rank_risk.inbound_refs(self.root, units)
        self.assertEqual(refs["core.py::helper"], 3)

    def test_ranked_order_within_a_file_reflects_intra_file_call_counts(self):
        write(self.root, "core.py",
              "def busy():\n    pass\n\n\ndef quiet():\n    pass\n\n\n"
              "def a():\n    busy()\n    busy()\n    busy()\n\n\ndef b():\n    quiet()\n")
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        ids = [r["id"] for r in plan["ranked"]]
        self.assertLess(ids.index("core.py::busy"), ids.index("core.py::quiet"))

    def test_matches_whole_identifiers_only(self):
        # `apply` must not be found inside `apply_discount` or `reapply`.
        write(self.root, "core.py", "def apply():\n    pass\n")
        write(self.root, "other.py", "def apply_discount():\n    pass\n\n\ndef reapply():\n    pass\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::apply"], 0)

    def test_digit_glued_identifier_is_not_a_reference(self):
        # "2x" must not count as a reference to a unit named `x` — the old \bx\b
        # boundary semantics, which the single-pass tokeniser has to preserve.
        write(self.root, "core.py", "def x():\n    pass\n")
        write(self.root, "notes.py", "# scale by 2x and 3x for the 4k display\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::x"], 0)


class TestTriage(TempRepo):
    def _tier(self, rel, src, name):
        write(self.root, rel, src)
        unit = [u for u in rank_risk.discover_units(self.root) if u["name"] == name][0]
        return rank_risk.triage(self.root, unit)

    def test_pure_function_is_tier_1(self):
        tier, reason = self._tier("a.py", "def add(x, y):\n    return x + y\n", "add")
        self.assertEqual(tier, 1)
        self.assertIn("no I/O", reason)

    def test_filesystem_use_is_tier_2_with_a_named_boundary(self):
        tier, reason = self._tier(
            "b.py", "def load(path):\n    with open(path) as f:\n        return f.read()\n", "load")
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)

    def test_clock_use_is_tier_2(self):
        tier, reason = self._tier(
            "c.py", "import datetime\n\n\ndef stamp():\n    return datetime.datetime.now()\n", "stamp")
        self.assertEqual(tier, 2)
        self.assertIn("clock", reason)

    def test_network_call_is_tier_3(self):
        tier, reason = self._tier(
            "d.py", "import requests\n\n\ndef fetch(u):\n    return requests.get(u).json()\n", "fetch")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_network_in_a_constructor_is_tier_3(self):
        # THE headline negative: a class that dials out when you instantiate it
        # cannot be pinned without a seam, and must never get a test written.
        tier, reason = self._tier(
            "e.py",
            "import socket\n\n\nclass Client:\n    def __init__(self, host):\n"
            "        self.sock = socket.create_connection((host, 80))\n",
            "Client")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_module_level_io_makes_every_unit_in_the_file_tier_4(self):
        # Importing the module does I/O, so nothing in it can be reached at all.
        tier, reason = self._tier(
            "f.py",
            "import requests\n\nCONFIG = requests.get('http://x/cfg').json()\n\n\n"
            "def pure(x):\n    return x\n",
            "pure")
        self.assertEqual(tier, 4)
        self.assertIn("import time", reason)

    def test_module_level_data_naming_a_marker_is_not_import_time_io(self):
        # A module-level allowlist that MENTIONS a driver is data, not behaviour.
        # Reading it as I/O condemned every unit in the file to tier 4 and
        # silently dropped them from the net.
        tier, reason = self._tier(
            "g.py",
            'DRIVERS = {"database": ("psycopg2.", "sqlite3.connect")}\n\n\n'
            "def pure(x):\n    return x\n",
            "pure")
        self.assertEqual(tier, 1)

    def test_renamed_module_import_is_still_seen_as_network_io(self):
        # `import socket as s` must not hide behind an alias. Substring
        # matching on a derived marker like "s." would also match "os." —
        # this only works because resolution goes through the alias map.
        tier, reason = self._tier(
            "alias.py",
            "import socket as s\n\n\ndef dial(host):\n"
            "    return s.create_connection((host, 80))\n",
            "dial")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_renamed_from_import_is_still_seen_as_subprocess_io(self):
        tier, reason = self._tier(
            "fromalias.py",
            "from os import system as run_cmd\n\n\ndef go(cmd):\n"
            "    return run_cmd(cmd)\n",
            "go")
        self.assertEqual(tier, 3)
        self.assertIn("subprocess", reason)

    def test_wrapper_over_a_same_module_tier_3_helper_is_tier_3(self):
        # A thin wrapper that just forwards to a helper doing real I/O must
        # not read clean — the helper's tier is inherited, and the reason
        # must name the helper so a human can see why.
        tier, reason = self._tier(
            "indirect.py",
            "import requests\n\n\ndef _fetch_raw(u):\n    return requests.get(u)\n\n\n"
            "def get_data(u):\n    return _fetch_raw(u)\n",
            "get_data")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)
        self.assertIn("_fetch_raw", reason)

    def test_argument_default_calling_uncontrollable_io_taints_the_module(self):
        # Python evaluates argument defaults at DEFINITION time — import
        # time — even though the default lives inside a `def` header.
        tier, reason = self._tier(
            "default.py",
            "import requests\n\n\ndef risky(x=requests.get('http://x').json()):\n"
            "    return x\n\n\ndef pure_helper(y):\n    return y\n",
            "pure_helper")
        self.assertEqual(tier, 4)

    def test_class_body_call_at_definition_time_taints_the_module(self):
        # A class-body statement (not inside a method) runs when the class
        # is defined — at import time — even though it lives inside `class`.
        tier, reason = self._tier(
            "classbody.py",
            "import psycopg2\n\n\nclass Setup:\n    CONN = psycopg2.connect('dsn')\n\n\n"
            "def pure_helper2(z):\n    return z\n",
            "pure_helper2")
        self.assertEqual(tier, 4)

    def test_function_local_import_does_not_shadow_the_module_level_alias(self):
        # The alias map is scoped to MODULE-level imports only. An unrelated
        # function's local `import ... as requests` must not rewrite what
        # `requests` means for the rest of the file — the round-1 fix
        # introduced this regression by walking the whole tree.
        tier, reason = self._tier(
            "shadow.py",
            "import requests\n\n\n"
            "def unrelated():\n    import collections.abc as requests\n"
            "    return requests.Mapping\n\n\n"
            "def net_call(u):\n    return requests.get(u)\n",
            "net_call")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_class_base_expression_call_at_definition_time_taints_the_module(self):
        # `class C(make_base()):` evaluates `make_base()` at class-CREATION
        # time — import time — even though the call lives in the base-class
        # list, not the class body or a decorator.
        tier, reason = self._tier(
            "basecls.py",
            "import requests\n\n\ndef make_base():\n"
            "    return requests.get('http://x').json()\n\n\n"
            "class C(make_base()):\n    pass\n\n\ndef pure(x):\n    return x\n",
            "pure")
        self.assertEqual(tier, 4)

    def test_method_call_through_a_fresh_instance_is_tier_3_named_by_method(self):
        # `Client().fetch(u)` — the receiver is a fresh instantiation, not a
        # resolvable name, but the METHOD name alone must still be enough to
        # find a same-module `fetch` that does real network I/O.
        tier, reason = self._tier(
            "method.py",
            "import requests\n\n\nclass Client:\n    def fetch(self, u):\n"
            "        return requests.get(u)\n\n\ndef get_data(u):\n"
            "    return Client().fetch(u)\n",
            "get_data")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)
        self.assertIn("Client.fetch", reason)

    def test_module_level_call_to_a_same_module_helper_taints_the_module(self):
        # `_STARTED = _boot()` at module level must inherit whatever `_boot`
        # itself reaches — the module-taint check chases the same-module
        # call graph transitively, not just the direct call target.
        tier, reason = self._tier(
            "boot.py",
            "import subprocess\n\n\ndef _boot():\n    return subprocess.run(['true'])\n\n\n"
            "_STARTED = _boot()\n\n\ndef pure_helper3(x):\n    return x\n",
            "pure_helper3")
        self.assertEqual(tier, 4)

    def test_main_guard_does_not_taint_the_module(self):
        # `if __name__ == "__main__": main()` is THE standard idiom written
        # specifically so its body does not run at import time. Making the
        # module-taint check transitive (chasing `_boot()` above) must not
        # also start chasing into `main()` through this guard — that false
        # positive was observed to taint 128 of this repo's own 296 units
        # before `_is_main_guard` excluded it.
        tier, reason = self._tier(
            "script.py",
            "import subprocess\n\n\ndef main():\n    return subprocess.run(['true'])\n\n\n"
            "def pure_helper4(x):\n    return x\n\n\n"
            "if __name__ == '__main__':\n    main()\n",
            "pure_helper4")
        self.assertEqual(tier, 1)

    def test_function_local_import_is_still_visible_within_its_own_function(self):
        # Scoping the alias map to module level (previous test) must not
        # also blind a function to its OWN local import — this repo's own
        # scripts/ab-validate.py::check_audit_guardrails does exactly this
        # (`import subprocess as sp` then `sp.run(...)`, both inside the
        # same function) and was observed to silently drop to tier 2 before
        # `_local_alias_map` restored per-function visibility.
        tier, reason = self._tier(
            "localimport.py",
            "def run_it(cmd):\n    import subprocess as sp\n"
            "    return sp.run(cmd)\n",
            "run_it")
        self.assertEqual(tier, 3)
        self.assertIn("subprocess", reason)

    def test_nested_def_local_import_does_not_hide_the_outer_functions_io(self):
        # A NESTED def's own local import must not leak OUTWARD and
        # overwrite the ENCLOSING function's correct, module-level alias —
        # one boundary deeper than the shadow.py bug this fixes.
        tier, reason = self._tier(
            "nested.py",
            "import requests as X\n\n\n"
            "def outer(u):\n    def inner():\n        import os as X\n"
            "        return X.getcwd()\n    return X.get(u)\n",
            "outer")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_sibling_method_local_import_does_not_hide_another_methods_io(self):
        # One METHOD's local import must not leak SIDEWAYS into a sibling
        # method when the whole class is scanned as one unit.
        tier, reason = self._tier(
            "sibling.py",
            "import requests as X\n\n\nclass Client:\n"
            "    def helper(self):\n        import os as X\n"
            "        return X.getcwd()\n\n"
            "    def fetch(self, u):\n        return X.get(u)\n",
            "Client")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_bare_attribute_call_on_an_unresolvable_receiver_is_not_chased(self):
        # `cfg.get('key')` on a plain dict must NOT resolve to a
        # module-level `get()` just because the names coincide — chasing a
        # bare `.attr` regardless of receiver was found to silently shrink
        # the net project-wide (`.get`/`.run`/`.read`/... are everywhere).
        tier, reason = self._tier(
            "c2.py",
            "import subprocess\n\n\ndef get():\n    return subprocess.run(['true'])\n\n\n"
            "def lookup(cfg):\n    return cfg.get('key')\n",
            "lookup")
        self.assertEqual(tier, 1)

    def test_fresh_instance_method_chase_still_works_after_narrowing(self):
        # The control: narrowing the method chase (previous test) must not
        # have disabled it outright. `Client().fetch(u)` is still the one
        # receiver shape that IS trusted, and must still resolve.
        tier, reason = self._tier(
            "method2.py",
            "import requests\n\n\nclass Client:\n    def fetch(self, u):\n"
            "        return requests.get(u)\n\n\ndef get_data(u):\n"
            "    return Client().fetch(u)\n",
            "get_data")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)
        self.assertIn("Client.fetch", reason)

    def test_parameter_held_instance_method_is_chased_when_unambiguous(self):
        # `w` is a plain function PARAMETER — neither `self` nor an inline
        # `Client()` — so the two narrow receiver shapes miss it. But `fetch`
        # is defined by exactly one module-level class, so the resolution is
        # forced and the real `requests.get` behind it must stay visible.
        # This is the shape of this repo's own world_model.py CLI dispatchers
        # (`def cmd_x(wm, a): wm.build(...)`), twelve of which regressed to a
        # false Tier 1 when the chase was narrowed to those two shapes.
        tier, reason = self._tier(
            "param.py",
            "import requests\n\n\nclass Client:\n    def fetch(self, u):\n"
            "        return requests.get(u)\n\n\ndef use(w, u):\n"
            "    return w.fetch(u)\n",
            "use")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)
        self.assertIn("Client.fetch", reason)

    def test_module_level_singleton_method_call_is_chased(self):
        # `_c = Client()` at module level, then `_c.fetch(u)` from a function:
        # the receiver is an ordinary module global, not a constructor call in
        # the expression itself, so it needs the unambiguous-method rule too.
        tier, reason = self._tier(
            "singleton.py",
            "import requests\n\n\nclass Client:\n    def fetch(self, u):\n"
            "        return requests.get(u)\n\n\n_c = Client()\n\n\n"
            "def grab(u):\n    return _c.fetch(u)\n",
            "grab")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_class_name_dispatch_is_chased(self):
        # `Client.fetch(u)` — static/classmethod dispatch by bare class name
        # from an outside function. The receiver IS a module-level ClassDef,
        # so this resolves exactly, with no ambiguity to weigh.
        tier, reason = self._tier(
            "static.py",
            "import requests\n\n\nclass Client:\n    @staticmethod\n"
            "    def fetch(u):\n        return requests.get(u)\n\n\n"
            "def grab(u):\n    return Client.fetch(u)\n",
            "grab")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)
        self.assertIn("Client.fetch", reason)

    def test_bare_attribute_still_does_not_chase_a_module_level_function(self):
        # THE control that must not regress. Widening the chase to class
        # methods must not re-open it to module-level FUNCTIONS: `cfg.get`
        # on a plain dict still must not resolve to a module-level `get()`
        # that shells out. No class in this file defines `get`, so there is
        # nothing to chase and the unit stays directly callable.
        tier, reason = self._tier(
            "c3.py",
            "import subprocess\n\n\ndef get():\n    return subprocess.run(['true'])\n\n\n"
            "def lookup(cfg):\n    return cfg.get('key')\n",
            "lookup")
        self.assertEqual(tier, 1)

    def test_ambiguous_method_name_across_two_classes_is_not_chased(self):
        # Two module-level classes both define `fetch`, one of them doing
        # network I/O. Picking which one `w` holds needs the receiver's TYPE
        # — points-to analysis, deliberately out of scope — and fanning out
        # to both is the over-flagging that shrinks the net project-wide.
        # So the name is dropped: assert only that the caller was NOT chased
        # (no `Class.fetch` credited in the reason), not any particular tier,
        # since the tier follows only from whatever the unit does itself.
        tier, reason = self._tier(
            "ambig.py",
            "import requests\n\n\nclass A:\n    def fetch(self, u):\n"
            "        return requests.get(u)\n\n\nclass B:\n    def fetch(self, u):\n"
            "        return u\n\n\ndef use(w, u):\n    return w.fetch(u)\n",
            "use")
        self.assertNotIn("fetch", reason)
        self.assertNotIn("network", reason)

    def test_raw_file_descriptor_io_is_tier_2_filesystem(self):
        # `os.open` is NOT the builtin `open`: it sits underneath it, so a
        # runtime guard patching the builtin never sees this. Still
        # CONTROLLABLE though -- a test can point an fd at a temp dir.
        tier, reason = self._tier(
            "rawwrite.py",
            "import os\n\n\ndef rawwrite(p, data):\n"
            "    fd = os.open(p, os.O_WRONLY | os.O_CREAT)\n"
            "    os.write(fd, data)\n    os.close(fd)\n",
            "rawwrite")
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)

    def test_posix_spawn_is_tier_3_subprocess(self):
        # `os.posix_spawn` never routes through `subprocess.Popen`, so a
        # guard patching `subprocess` does not see it. No seam makes
        # spawning a real process safe to pin: decline it.
        tier, reason = self._tier(
            "spawn.py",
            "import os\n\n\ndef spawn(p, argv):\n"
            "    return os.posix_spawn(p, argv, {})\n",
            "spawn")
        self.assertEqual(tier, 3)
        self.assertIn("subprocess", reason)

    def test_execv_is_tier_3_because_no_runtime_guard_can_survive_it(self):
        # THE case that must be caught STATICALLY rather than left to the
        # runtime guard: `os.execv` replaces the entire process image, so
        # every in-process monkeypatch the guard installed is gone the
        # instant it runs. There is no "the guard will catch it" backstop
        # here -- the guard cannot survive the call it would be catching.
        tier, reason = self._tier(
            "replace.py",
            "import os\n\n\ndef replace(p, argv):\n"
            "    os.execv(p, argv)\n",
            "replace")
        self.assertEqual(tier, 3)
        self.assertIn("subprocess", reason)

    def test_os_path_join_is_not_swept_into_subprocess_by_the_new_os_markers(self):
        # The control: the new `os.*` spawn/exec entries are exact-or-prefix
        # matches on a resolved dotted name, not a blanket "starts with os.".
        # Ordinary `os.path` use must stay Tier 2 filesystem.
        tier, reason = self._tier(
            "joiner.py",
            "import os\n\n\ndef joiner(a, b):\n    return os.path.join(a, b)\n",
            "joiner")
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)
        self.assertNotIn("subprocess", reason)


class TestAlreadyCovered(TempRepo):
    def test_unit_named_in_a_test_file_is_reported_covered(self):
        write(self.root, "core.py", "def covered():\n    pass\n\n\ndef bare():\n    pass\n")
        write(self.root, "tests/test_core.py",
              "from core import covered\n\n\ndef test_covered():\n    covered()\n")
        units = rank_risk.discover_units(self.root)
        cov = rank_risk.already_covered(self.root, units)
        self.assertEqual(cov.get("core.py::covered"), "tests/test_core.py")
        self.assertNotIn("core.py::bare", cov)


class TestRank(TempRepo):
    def _repo(self):
        write(self.root, "hot.py", "def risky(a, b):\n    return a + b\n")
        write(self.root, "cold.py", "def quiet(a):\n    return a\n")
        write(self.root, "net.py",
              "import requests\n\n\ndef fetch(u):\n    return requests.get(u)\n")
        write(self.root, "caller.py",
              "from hot import risky\nrisky(1, 2)\nrisky(3, 4)\n")

    def test_tier_3_units_are_not_netted_never_ranked(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        ranked_ids = [r["id"] for r in plan["ranked"]]
        self.assertNotIn("net.py::fetch", ranked_ids)
        self.assertIn("net.py::fetch", [r["id"] for r in plan["not_netted"]])

    def test_more_referenced_unit_outranks_a_quiet_one(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        ids = [r["id"] for r in plan["ranked"]]
        self.assertLess(ids.index("hot.py::risky"), ids.index("cold.py::quiet"))

    def test_top_n_bounds_the_ranked_list(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=1)
        self.assertEqual(len(plan["ranked"]), 1)

    def test_output_is_byte_identical_across_runs(self):
        import json
        self._repo()
        a = json.dumps(rank_risk.rank(self.root, since="10 years ago", top_n=10), sort_keys=True)
        b = json.dumps(rank_risk.rank(self.root, since="10 years ago", top_n=10), sort_keys=True)
        self.assertEqual(a, b)

    def test_every_ranked_row_flags_the_reference_count_as_approximate(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        self.assertTrue(plan["ranked"])
        for row in plan["ranked"]:
            self.assertIs(row["inbound_approx"], True)


if __name__ == "__main__":
    unittest.main()
