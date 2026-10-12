"""Factory hardening L3 and L4: `link` adds the dropped item's unseen keys to count, seen
keys carry their source, a queue from before L4 migrates on load without counting a
message twice, and a raw feedback id can never equal a hashed one.

Every fixture is synthetic: example.com addresses and m-<n> ids only.
"""
import hashlib, io, json, os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io

CFG = {"sources": {"fb": {"enabled": True, "formats": ["feedback-jsonl"]},
                   "fb2": {"enabled": True, "formats": ["feedback-jsonl"]},
                   "jira": {"enabled": True, "formats": ["intake-signals-jsonl"]},
                   "ops": {"enabled": True, "formats": ["intake-signals-jsonl"]}}}
NOW = "2026-10-10T00:00:00Z"


def msg(i, topic="checkout-crash", channel="email", mid=None):
    return {"id": mid if mid is not None else "m-%d" % i, "topic": topic, "channel": channel,
            "created_at": "2026-10-01T00:%02d:00Z" % (i % 60), "text": "message %d" % i}


def lines(*recs):
    return "".join(json.dumps(r) + "\n" for r in recs)


def imp(root, text, source="fb", fmt="feedback-jsonl"):
    with mock.patch.object(IN, "_now", return_value=NOW), no_io(), \
            mock.patch.object(sys, "stdin", io.StringIO(text)):
        return run(root, "import", "--format", fmt, "--source", source, "-")


def cmd(root, *argv):
    with mock.patch.object(IN, "_now", return_value=NOW), no_io():
        return run(root, *argv)


def items(root):
    return IN.Queue.load(root).items


def qpath(root):
    return os.path.join(root, ".skill-contract", "intake", "queue.json")


def edit_queue(root, fn):
    """Rewrite queue.json on disk through fn(data), as an older intake would have left it."""
    with open(qpath(root), encoding="utf-8") as f:
        data = json.load(f)
    fn(data)
    with open(qpath(root), "w", encoding="utf-8") as f:
        json.dump(data, f)


def to_legacy(data):
    """A queue from before L4: no key_format, and every seen key without its source."""
    del data["key_format"]
    for it in data["items"].values():
        it["seen_keys"] = sorted(k.split("|", 1)[1] for k in it.get("seen_keys", []))


def jsonl(source, sid, key, title="p99 up"):
    return json.dumps({"source": source, "source_id": sid, "kind": "issue", "title": title, "severity": 2,
                       "first_seen": NOW, "last_seen": NOW,
                       "evidence": [{"text": "seen", "key": key}]}) + "\n"


CRASH, PAY = IN.item_id("fb", "checkout-crash"), IN.item_id("fb", "pay-crash")


class LinkCountTests(unittest.TestCase):
    """L3: count = keep.count + |drop keys - keep keys| + max(0, drop.count - |drop keys|)."""

    def test_overlapping_items_link_to_the_distinct_message_count(self):
        root = repo(config=CFG)
        imp(root, lines(*(msg(i, topic="pay-crash") for i in (1, 2, 3))))
        imp(root, lines(*(msg(i) for i in (2, 3, 4, 5))))
        self.assertEqual((items(root)[CRASH]["count"], items(root)[PAY]["count"]), (4, 3))
        rc, out = cmd(root, "link", CRASH, PAY)
        self.assertEqual(rc, 0, out)
        it = items(root)[CRASH]
        self.assertEqual(it["count"], 5)  # m-1 .. m-5
        self.assertEqual(it["count"], len(it["seen_keys"]))

    def test_a_reimport_after_link_adds_nothing(self):
        root = repo(config=CFG)
        pay = lines(*(msg(i, topic="pay-crash") for i in (1, 2, 3)))
        crash = lines(*(msg(i) for i in (2, 3, 4, 5)))
        imp(root, pay)
        imp(root, crash)
        cmd(root, "link", CRASH, PAY)
        imp(root, pay)
        imp(root, crash)
        self.assertEqual(items(root)[CRASH]["count"], 5)

    def test_a_dropped_item_keeps_its_untracked_count(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1, topic="pay-crash"), msg(2, topic="pay-crash")))
        imp(root, lines(msg(2), msg(3)))
        # A legacy item: its count says 7, but only two keys stand behind it.
        edit_queue(root, lambda d: d["items"][PAY].update(count=7))
        cmd(root, "link", CRASH, PAY)
        # 2 (keep) + 1 unseen key (m-1) + (7 - 2) untracked = 8
        self.assertEqual(items(root)[CRASH]["count"], 8)

    def test_link_carries_the_dropped_items_own_links(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1, topic="a-one")))
        imp(root, lines(msg(2, topic="b-two")))
        imp(root, lines(msg(3)))
        a, b = IN.item_id("fb", "a-one"), IN.item_id("fb", "b-two")
        cmd(root, "link", a, b)
        cmd(root, "link", CRASH, a)
        self.assertEqual(sorted(ln["id"] for ln in items(root)[CRASH]["linked"]), sorted([a, b]))
        self.assertEqual(items(root)[CRASH]["count"], 3)


class NamespaceTests(unittest.TestCase):
    """L4: a seen key is <source>|<evidence key>."""

    def test_new_keys_carry_the_source(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1)))
        self.assertEqual(items(root)[CRASH]["seen_keys"], ["fb|email:m-1"])
        with open(qpath(root), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["key_format"], IN.KEY_FORMAT)

    def test_a_cross_source_collision_counts_twice(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1)), source="fb")
        imp(root, lines(msg(1)), source="fb2")
        other = IN.item_id("fb2", "checkout-crash")
        cmd(root, "link", CRASH, other)
        it = items(root)[CRASH]
        self.assertEqual((it["count"], it["seen_keys"]), (2, ["fb2|email:m-1", "fb|email:m-1"]))
        imp(root, lines(msg(1)), source="fb")
        imp(root, lines(msg(1)), source="fb2")
        self.assertEqual(items(root)[CRASH]["count"], 2)

    def test_a_cross_source_collision_counts_twice_for_signals_too(self):
        root = repo(config=CFG)
        imp(root, jsonl("jira", "s-1", "a"), source="jira", fmt="intake-signals-jsonl")
        imp(root, jsonl("ops", "s-2", "a"), source="ops", fmt="intake-signals-jsonl")
        keep, drop = IN.item_id("jira", "s-1"), IN.item_id("ops", "s-2")
        cmd(root, "link", keep, drop)
        self.assertEqual(items(root)[keep]["count"], 2)


class RawIdTests(unittest.TestCase):
    """L4: a raw id that starts with h: or r: gets one more r:, so raw and hashed ids
    never meet."""

    def test_a_raw_h_id_and_a_hashed_id_do_not_collide(self):
        email = "pat.form@example.com"
        hashed = "h:" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
        root = repo(config=CFG)
        imp(root, lines(msg(1, mid=email), msg(2, mid=hashed)))
        it = items(root)[CRASH]
        self.assertEqual(it["count"], 2)
        self.assertEqual(it["seen_keys"], sorted(["fb|email:" + hashed, "fb|email:r:" + hashed]))
        imp(root, lines(msg(1, mid=email), msg(2, mid=hashed)))
        self.assertEqual(items(root)[CRASH]["count"], 2)

    def test_the_escape_is_one_to_one(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1, mid="h:x"), msg(2, mid="r:h:x"), msg(3, mid="r:x"),
                        msg(4, mid="x")))
        it = items(root)[CRASH]
        self.assertEqual(it["count"], 4)
        self.assertEqual(it["seen_keys"], ["fb|email:r:h:x", "fb|email:r:r:h:x",
                                           "fb|email:r:r:x", "fb|email:x"])


class MigrationTests(unittest.TestCase):
    """L4: a queue from before L4 loads, and a re-import of the same messages adds nothing."""

    def test_a_legacy_store_migrates_and_a_reimport_adds_nothing(self):
        root = repo(config=CFG)
        export = lines(*(msg(i) for i in (1, 2, 3)))
        imp(root, export)
        edit_queue(root, to_legacy)
        with open(qpath(root), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["items"][CRASH]["seen_keys"],
                             ["email:m-1", "email:m-2", "email:m-3"])
        it = items(root)[CRASH]
        self.assertEqual(it["seen_keys"], ["fb|email:m-1", "fb|email:m-2", "fb|email:m-3"])
        imp(root, export)
        self.assertEqual(items(root)[CRASH]["count"], 3)
        imp(root, lines(msg(4)))
        self.assertEqual(items(root)[CRASH]["count"], 4)

    def test_legacy_raw_h_and_r_ids_migrate_to_the_escaped_form(self):
        root = repo(config=CFG)
        email = "pat.form@example.com"
        export = lines(msg(1, mid="h:not-a-hash"), msg(2, mid="r:7"), msg(3, mid=email))
        imp(root, export)

        def old(data):
            to_legacy(data)
            # The pre-L4 adapter stored raw h: and r: ids as they came.
            data["items"][CRASH]["seen_keys"] = sorted(
                k.replace("email:r:", "email:", 1) for k in data["items"][CRASH]["seen_keys"])
        edit_queue(root, old)
        imp(root, export)
        it = items(root)[CRASH]
        self.assertEqual(it["count"], 3)
        self.assertIn("fb|email:r:h:not-a-hash", it["seen_keys"])
        self.assertIn("fb|email:r:r:7", it["seen_keys"])

    def legacy_two_sources(self, a, b, count):
        root = repo(config=CFG)
        imp(root, a, source="fb")
        imp(root, b, source="fb2")
        cmd(root, "link", CRASH, IN.item_id("fb2", "checkout-crash"))

        def old(data):
            to_legacy(data)
            data["items"][CRASH]["count"] = count  # what the pre-L4 link left
        edit_queue(root, old)
        return root

    def test_a_legacy_item_linked_from_two_sources_keeps_its_keys(self):
        a, b = lines(msg(1), msg(2)), lines(msg(3), msg(4))
        root = self.legacy_two_sources(a, b, 4)
        it = items(root)[CRASH]
        self.assertEqual((it["seen_keys"], it["legacy_keys"]),
                         ([], ["email:m-1", "email:m-2", "email:m-3", "email:m-4"]))
        imp(root, a, source="fb")
        imp(root, b, source="fb2")
        it = items(root)[CRASH]
        self.assertEqual((it["count"], it.get("legacy_keys")), (4, None))
        imp(root, a, source="fb")
        imp(root, b, source="fb2")
        self.assertEqual(items(root)[CRASH]["count"], 4)
        imp(root, lines(msg(9)), source="fb2")
        self.assertEqual(items(root)[CRASH]["count"], 5)

    def test_a_legacy_collision_is_counted_once_more_and_then_never(self):
        """The pre-L4 queue counted fb's m-2 and fb2's m-2 once. Each legacy key absorbs
        one re-imported key, so the count rises once to the true distinct count (4)."""
        a, b = lines(msg(1), msg(2)), lines(msg(2), msg(3))
        root = self.legacy_two_sources(a, b, 3)
        imp(root, a, source="fb")
        imp(root, b, source="fb2")
        self.assertEqual(items(root)[CRASH]["count"], 4)
        imp(root, a, source="fb")
        imp(root, b, source="fb2")
        self.assertEqual(items(root)[CRASH]["count"], 4)

    def test_a_legacy_chain_of_links_keeps_its_keys(self):
        """Before L4 a link did not carry the dropped item's own links, so the sources of
        a chain cannot be read back: the alias count is more than the link records."""
        root = repo(config=CFG)
        imp(root, lines(msg(1, topic="a-one")))
        imp(root, lines(msg(2, topic="b-two")))
        imp(root, lines(msg(3)))
        a, b = IN.item_id("fb", "a-one"), IN.item_id("fb", "b-two")
        cmd(root, "link", a, b)
        cmd(root, "link", CRASH, a)

        def old(data):
            to_legacy(data)
            data["items"][CRASH]["linked"] = [ln for ln in data["items"][CRASH]["linked"]
                                              if ln["id"] == a]
        edit_queue(root, old)
        it = items(root)[CRASH]
        self.assertEqual(it.get("legacy_keys"), ["email:m-1", "email:m-2", "email:m-3"])
        imp(root, lines(msg(1, topic="a-one"), msg(2, topic="b-two"), msg(3)))
        self.assertEqual(items(root)[CRASH]["count"], 3)

    def test_a_single_source_link_migrates(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1, topic="pay-crash")))
        imp(root, lines(msg(2)))
        cmd(root, "link", CRASH, PAY)
        edit_queue(root, to_legacy)
        it = items(root)[CRASH]
        self.assertEqual((it["seen_keys"], it.get("legacy_keys")),
                         (["fb|email:m-1", "fb|email:m-2"], None))

    def test_a_migrated_queue_saves_the_key_format(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1)))
        edit_queue(root, to_legacy)
        imp(root, lines(msg(1)))
        with open(qpath(root), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual((data["key_format"], data["items"][CRASH]["seen_keys"]),
                         (IN.KEY_FORMAT, ["fb|email:m-1"]))

    def test_an_unknown_key_format_is_queue_unreadable(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1)))
        edit_queue(root, lambda d: d.update(key_format=3))
        rc, out = cmd(root, "list")
        self.assertEqual(rc, 2)
        self.assertIn("STOP: queue-unreadable", out)

    def test_bad_legacy_keys_is_queue_unreadable(self):
        root = repo(config=CFG)
        imp(root, lines(msg(1)))
        edit_queue(root, lambda d: d["items"][CRASH].update(legacy_keys=[1]))
        rc, out = cmd(root, "list")
        self.assertEqual(rc, 2)
        self.assertIn("STOP: queue-unreadable", out)

    def test_legacy_keys_stay_out_of_the_payload(self):
        it = {"id": CRASH, "title": "t", "source": "fb", "source_id": "x", "first_seen": NOW,
              "legacy_keys": ["email:m-1"], "seen_keys": []}
        self.assertNotIn("legacy_keys", IN.item_payload(it))


class VersionPin(unittest.TestCase):
    def test_version_matches_skill_md(self):
        # every envelope names this constant as its producer version: it must be the
        # version SKILL.md ships
        skill_md = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")
        with open(skill_md, encoding="utf-8") as f:
            self.assertIn('  version: "%s"\n' % IN.VERSION, f.read())


if __name__ == "__main__":
    unittest.main()
