import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_env as g

GROUPS = ("clock", "database", "environment", "filesystem", "network", "randomness", "subprocess")
CTRL = ("clock", "environment", "filesystem")
UNCTRL = ("database", "network", "randomness", "subprocess")
WHY = lambda grp: "no test can control it"

def env(**kv):
    return g.read_env(kv, CTRL, UNCTRL, "go", WHY)

class TestReadEnv(unittest.TestCase):
    def test_absent_tier_is_tier_1_with_a_note(self):
        tier, allow, notes = env()
        self.assertEqual((tier, allow), (1, None))
        self.assertIn("defaulting to tier 1", notes[0])
    def test_none_allows_nothing(self):
        self.assertEqual(env(TEST_SAFETY_NET_TIER="2", TEST_SAFETY_NET_ALLOW="none")[1], [])
    def test_an_uncontrollable_group_is_named_and_stays_blocked(self):
        tier, allow, notes = env(TEST_SAFETY_NET_TIER="2", TEST_SAFETY_NET_ALLOW="filesystem,network")
        self.assertEqual(allow, ["filesystem"])
        self.assertIn("names network, which is not controllable on go (no test can control it)", notes[0])
    def test_an_unknown_group_is_ignored(self):
        self.assertIn("unknown group 'bogus'", env(TEST_SAFETY_NET_TIER="2", TEST_SAFETY_NET_ALLOW="bogus")[2][0])
    def test_tier_1_ignores_the_allow_list(self):
        self.assertIn("tier 1 ignores", env(TEST_SAFETY_NET_TIER="1", TEST_SAFETY_NET_ALLOW="clock")[2][-1])

class TestBlockedGroups(unittest.TestCase):
    def test_tier_1_blocks_everything(self):
        self.assertEqual(g.blocked_groups(1, ["clock"], GROUPS, CTRL, UNCTRL), set(GROUPS))
    def test_tier_2_default_blocks_only_the_uncontrollable(self):
        self.assertEqual(g.blocked_groups(2, None, GROUPS, CTRL, UNCTRL), set(UNCTRL))
    def test_tier_2_permits_exactly_what_it_names(self):
        self.assertEqual(g.blocked_groups(2, ["filesystem"], GROUPS, CTRL, UNCTRL),
                         set(GROUPS) - {"filesystem"})

class TestExitTable(unittest.TestCase):
    def test_six_outcomes_with_words(self):
        self.assertEqual(sorted(g.OUTCOME), [0, 1, 2, 3, 4, 5])

if __name__ == "__main__":
    unittest.main()
