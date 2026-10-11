"""The money ledger (roadmap step 6D, part 2): budget lint, register, the refuse-only check,
record, withdraw, status, the state files and the output rules. Stdlib only, offline."""
import contextlib
import io
import json
import os
import re
import unittest
from unittest import mock

from ledger_testkit import (APPROVAL_TOKEN_RE, BUDGET, L, TODAY, check, ldir, log_lines,
                            no_io, registered, repo, run, state, write_budget, write_state)

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_LOOKALIKES = ("agent:claude", "agent：claude", "ａｇｅｎｔ:claude",
                    "аgent:claude", "agеnt:claude", "agent :claude", "agent-claude",
                    "agent/claude", "agent_claude", "AGENT-Claude", " АGENT:x",
                    # the review's lookalikes (one shared check with growth-review and
                    # idea-validation): a bare agent, any separator, letters apart, and
                    # small-capital or IPA letters; and a human name starting with the word
                    "agent", "agent x", "agent.claude", "agent;x", "agent|x", "@agent:x",
                    "[agent]:x", "a gent:x", "agent\u2236x", "agent\ua789x", "agent\u02d0x",
                    "agent\u0589x", "agent\u2010x", "agent\u2014x", "agent\u00b7x",
                    "\u1d00gent:x", "\u0251gent:x", "Agent Smith")
APPROVAL_NAMES = ("OK", "Okay", "yes", "Dana_ok", "Dana (approved)", "Granted", "Grant",
                  "ｏｋ", "PASS", "Allowed Dana", "accept")


def snapshot(d):
    out = {}
    for n in os.listdir(d):
        with open(os.path.join(d, n), "rb") as f:
            out[n] = f.read()
    return out


def budget_with(**caps):
    """BUDGET with some caps replaced: budget_with(hosting="30")."""
    text = BUDGET
    for name, value in caps.items():
        text = re.sub(r"\| %s \| [^|]+ \|" % name, "| %s | %s |" % (name, value), text)
    return text


class Lint(unittest.TestCase):
    def lint(self, text):
        root = repo(text)
        return run(root, "lint")[:2]

    def test_good_file(self):
        rc, out = self.lint(BUDGET)
        self.assertEqual(rc, 0, out)
        self.assertIn("LINT: clean 3 categories, currency USD, total 120.00", out)

    def test_bad_currency(self):
        for cur in ("usd", "US", "USDX", "ＵＳＤ", "U$D", ""):
            rc, out = self.lint(BUDGET.replace("currency: USD", "currency: " + cur))
            self.assertEqual(rc, 2, cur)
            self.assertIn("PROBLEM:", out)
        rc, out = self.lint(BUDGET.replace("currency: USD\n", ""))
        self.assertEqual(rc, 2)
        self.assertIn("no `currency:` line", out)
        rc, out = self.lint(BUDGET.replace("currency: USD", "currency: USD\ncurrency: EUR"))
        self.assertEqual(rc, 2)
        self.assertIn("more than one currency line", out)

    def test_bad_amounts(self):
        for bad in ("1e3", "1.5e2", "12.345", "1234567890123", "１００",
                    "١٠", "-5", "1_000", ".5", "5.", "inf", "nan", "1,000", " "):
            rc, out = self.lint(budget_with(hosting=bad))
            self.assertEqual(rc, 2, bad)
            self.assertIn("PROBLEM:", out, bad)

    def test_twelve_digits_and_two_decimals_lint(self):
        rc, out = self.lint(budget_with(total="999999999999.99"))
        self.assertEqual(rc, 0, out)

    def test_duplicate_category(self):
        rc, out = self.lint(BUDGET.replace("| total |", "| hosting | 5 |\n| total |"))
        self.assertEqual(rc, 2)
        self.assertIn("category hosting appears twice", out)

    def test_no_total_row(self):
        rc, out = self.lint(BUDGET.replace("| total | 120 |\n", ""))
        self.assertEqual(rc, 2)
        self.assertIn("no `total` row", out)

    def test_two_total_rows(self):
        rc, out = self.lint(BUDGET.replace("| total | 120 |", "| total | 120 |\n| total | 9 |"))
        self.assertEqual(rc, 2)
        self.assertIn("the total row appears twice", out)

    def test_bad_category_names(self):
        for name in ("Hosting", "web hosting", "a" * 65, "pass", "ads-ok", "allowed",
                     "approved-spend"):
            rc, out = self.lint(BUDGET.replace("| hosting |", "| %s |" % name))
            self.assertEqual(rc, 2, name)

    def test_columns_and_tables(self):
        self.assertEqual(self.lint("currency: USD\n\nno table\n")[0], 2)
        rc, out = self.lint(BUDGET.replace("| category | monthly_cap |",
                                           "| category | cap |"))
        self.assertEqual(rc, 2)
        self.assertIn("exactly category and monthly_cap", out)

    def test_parse_amount(self):
        self.assertEqual(L.parse_amount("25"), 2500)
        self.assertEqual(L.parse_amount("19.99"), 1999)
        self.assertEqual(L.parse_amount("0.5"), 50)
        self.assertEqual(L.parse_amount("999999999999.99"), 99999999999999)
        for bad in ("1e3", "1.234", "1234567890123", "１", "1_0", "", None, "+1", "-1"):
            self.assertIsNone(L.parse_amount(bad), bad)
        self.assertEqual(L.money(1999), "19.99")
        self.assertEqual(L.money(-5), "-0.05")


class Register(unittest.TestCase):
    def test_agent_names_and_lookalikes_refused(self):
        for by in AGENT_LOOKALIKES:
            root = repo()
            rc, out, _ = run(root, "register", "--by", by)
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: an agent: name cannot register", out)
            self.assertFalse(os.path.exists(os.path.join(ldir(root), L.STATE_FILE)), by)

    def test_human_names_pass(self):
        # "Agent Smith" moved to AGENT_LOOKALIKES: a name that starts with the word "agent"
        # is refused (the check fails closed).
        for by in ("Dana", "Reagent:x", "Agentha Ray", "agentur GmbH", "Reagent Labs",
                   "Magenta", "Yesenia Okafor"):
            self.assertEqual(run(repo(), "register", "--by", by)[0], 0, by)

    def test_approval_word_names_refused(self):
        """A --by that holds an approval word as a word is a STOP, because register prints
        the name. "Grant" is a real first name: refused too (fail closed)."""
        for by in APPROVAL_NAMES:
            root = repo()
            rc, out, _ = run(root, "register", "--by", by)
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: --by holds a word that reads as approval", out)
            self.assertFalse(os.path.exists(os.path.join(ldir(root), L.STATE_FILE)), by)

    def test_empty_or_hidden_name_refused(self):
        for by in ("", "  ", "Da​na"):
            self.assertEqual(run(repo(), "register", "--by", by)[0], 2, repr(by))

    def test_register_then_no_change(self):
        root = registered()
        rc, out, _ = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 0)
        self.assertIn("no change since the last registration", out)
        self.assertEqual(len(state(root)["registrations"]), 1)

    def test_prose_edit_is_not_a_change(self):
        root = registered()
        write_budget(root, BUDGET.replace("Prose here", "Other prose"))
        self.assertIn("no change", run(root, "register", "--by", "Dana")[1])
        self.assertEqual(check(root, "p1", "hosting", "1")[0], 0)

    def test_reregister_needs_a_reason(self):
        root = registered()
        write_budget(root, budget_with(hosting="20"))
        rc, out, _ = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("registering again needs --reason", out)
        self.assertEqual(len(state(root)["registrations"]), 1)
        rc, out, _ = run(root, "register", "--by", "Dana", "--reason", "cut hosting")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("RAISED:", out)   # a lowered cap is not raised
        self.assertEqual(state(root)["registrations"][-1]["reason"], "cut hosting")

    def test_raised_caps_print_raised(self):
        root = registered()
        text = budget_with(hosting="40", total="150").replace(
            "| total |", "| email | 5 |\n| total |")
        write_budget(root, text)
        rc, out, _ = run(root, "register", "--by", "Dana", "--reason", "growth")
        self.assertEqual(rc, 0, out)
        self.assertIn("RAISED: hosting 25.00 -> 40.00", out)
        self.assertIn("RAISED: total 120.00 -> 150.00", out)
        self.assertIn("RAISED: email none -> 5.00 (a new category)", out)
        self.assertNotIn("RAISED: ads", out)

    def test_bad_budget_not_registered(self):
        root = repo(budget_with(hosting="1e3"))
        rc, out, _ = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("LINT: fail", out)
        self.assertFalse(os.path.exists(os.path.join(ldir(root), L.STATE_FILE)))

    def test_missing_budget(self):
        rc, out, _ = run(repo(None), "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("STOP: cannot read budget/budget.md", out)

    def test_currency_change_with_proposals_refused(self):
        root = registered()
        check(root, "p1", "hosting", "5")
        write_budget(root, BUDGET.replace("currency: USD", "currency: EUR"))
        rc, out, _ = run(root, "register", "--by", "Dana", "--reason", "moved")
        self.assertEqual(rc, 2)
        self.assertIn("there is no conversion", out)

    def test_currency_change_without_proposals_registers(self):
        root = registered()
        write_budget(root, BUDGET.replace("currency: USD", "currency: EUR"))
        self.assertEqual(run(root, "register", "--by", "Dana", "--reason", "moved")[0], 0)


class HandReset(unittest.TestCase):
    """The log remembers what a hand edit of ledger.json lost (growth-review's M1 fix)."""

    def test_deleted_state_needs_a_reason_and_refuses_checks(self):
        root = registered()
        self.assertEqual(check(root, "p1", "hosting", "20")[0], 0)
        os.unlink(os.path.join(ldir(root), L.STATE_FILE))
        rc, out, _ = check(root, "p2", "hosting", "20")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED: the ledger state was deleted or emptied by hand", out)
        rc, out, _ = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2, out)
        self.assertIn("NOTE: log.jsonl records 1 registration", out)
        self.assertIn("STOP: the ledger state was deleted or changed by hand", out)
        self.assertFalse(os.path.exists(os.path.join(ldir(root), L.STATE_FILE)))
        rc, out, _ = run(root, "register", "--by", "Dana", "--reason", "state was lost")
        self.assertEqual(rc, 0, out)
        self.assertEqual(check(root, "p3", "hosting", "1")[0], 0)

    def test_dropped_reservation_refuses_checks(self):
        root = registered()
        check(root, "p1", "hosting", "20")
        st = state(root)
        st["proposals"] = {}
        write_state(root, st)   # the reservation is gone, the seq is unchanged
        rc, out, _ = check(root, "p2", "hosting", "20")
        self.assertEqual(rc, 3)
        st["seq"] = 1           # a careful hand edit of the seq still disagrees with the log
        write_state(root, st)
        rc, out, _ = check(root, "p2", "hosting", "20")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED: the ledger state and its log disagree", out)
        for argv in (("record", "--id", "p1", "--amount", "1", "--currency", "USD",
                      "--month", "2026-10", "--by", "Dana"),
                     ("withdraw", "--id", "p1", "--by", "Dana", "--reason", "x")):
            rc, out, _ = run(root, *argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("STOP: the ledger state and its log disagree", out)
        rc, out, _ = run(root, "status")
        self.assertIn("NOTE: ledger.json is at event 1 but log.jsonl is at event 2", out)
        self.assertEqual(run(root, "register", "--by", "Dana")[0], 2)
        rc, out, _ = run(root, "register", "--by", "Dana", "--reason", "rebuilt by hand")
        self.assertEqual(rc, 0, out)
        self.assertEqual(check(root, "p2", "hosting", "20")[0], 0)

    def test_swapped_registration_refuses(self):
        root = registered()
        write_budget(root, budget_with(hosting="200", total="999"))
        st = state(root)
        reg = st["registrations"][-1]
        reg["caps"]["hosting"], reg["total"] = 20000, 99900
        reg["sha256"] = L.budget_sha(reg["currency"], reg["caps"], reg["total"])
        write_state(root, st)
        rc, out, _ = check(root, "p1", "hosting", "150")
        self.assertEqual(rc, 3, out)
        self.assertIn("REFUSED: the ledger state and its log disagree", out)


class Stale(unittest.TestCase):
    def test_edited_budget_refuses_every_check(self):
        root = registered()
        self.assertEqual(check(root, "p1", "hosting", "5")[0], 0)
        for text in (budget_with(hosting="1000", total="2000"), budget_with(hosting="1")):
            write_budget(root, text)
            for pid, amount in (("p2", "1"), ("p1", "5")):   # a new id and a replay
                rc, out, _ = check(root, pid, "hosting", amount)
                self.assertEqual(rc, 3, out)
                self.assertIn("REFUSED: budget edited since register", out)
                self.assertNotIn("NOT_REFUSED", out)
        rc, out, _ = run(root, "status")
        self.assertIn("stale=true", out)

    def test_missing_or_broken_budget_refuses(self):
        root = registered()
        write_budget(root, budget_with(hosting="1e3"))
        self.assertIn("budget edited since register", check(root, "p1", "hosting", "1")[1])
        os.unlink(os.path.join(root, "budget", "budget.md"))
        rc, out, _ = check(root, "p1", "hosting", "1")
        self.assertEqual(rc, 3)
        self.assertIn("budget/budget.md is missing", out)

    def test_register_again_clears_stale(self):
        root = registered()
        write_budget(root, budget_with(hosting="10"))
        self.assertEqual(check(root, "p1", "hosting", "1")[0], 3)
        run(root, "register", "--by", "Dana", "--reason", "cut")
        self.assertEqual(check(root, "p1", "hosting", "1")[0], 0)
        self.assertEqual(check(root, "p2", "hosting", "9.01")[0], 3)

    def test_unregistered_refuses(self):
        rc, out, _ = check(repo(), "p1", "hosting", "1")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED: the budget is not registered", out)


class Check(unittest.TestCase):
    def setUp(self):
        self.root = registered()

    def status(self, month=None):
        return run(self.root, "status", *(["--month", month] if month else []))[1]

    def test_under_cap_reserves(self):
        rc, out, _ = check(self.root, "vps", "hosting", "19.99")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines()[-1],
                         "NOT_REFUSED: vps — the human's approval is still required")
        p = state(self.root)["proposals"]["vps"]
        self.assertEqual((p["amount"], p["months"], p["status"]), (1999, ["2026-10"], "open"))
        self.assertIn("CATEGORY: hosting cap=25.00 recorded=0.00 reserved=19.99 "
                      "headroom=5.01", self.status())

    def test_exactly_at_cap_and_one_unit_over(self):
        self.assertEqual(check(self.root, "a", "hosting", "25")[0], 0)
        root = registered()
        rc, out, _ = check(root, "a", "hosting", "25.01")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED: 2026-10 hosting would reach 25.01, over its monthly cap 25.00",
                      out)
        self.assertNotIn("a", state(root)["proposals"])

    def test_total_cap_refuses_when_no_category_cap_does(self):
        self.assertEqual(check(self.root, "a", "ads", "100")[0], 0)
        rc, out, _ = check(self.root, "b", "hosting", "20.01")
        self.assertEqual(rc, 3)
        self.assertIn("the total would reach 120.01, over the monthly total cap 120.00", out)
        self.assertEqual(check(self.root, "c", "hosting", "20")[0], 0)   # exactly the total

    def test_recurring_refuses_when_only_month_7_goes_over(self):
        self.assertEqual(check(self.root, "launch", "ads", "95", "--month", "2027-04")[0], 0)
        rc, out, _ = check(self.root, "ppc", "ads", "10", "--recurring", "monthly",
                           "--start", "2026-10")
        self.assertEqual(rc, 3, out)
        self.assertIn("REFUSED: 2027-04 ads would reach 105.00", out)   # month 7 of 12
        rc, out, _ = check(self.root, "ppc2", "ads", "10", "--recurring", "monthly",
                           "--start", "2026-10", "--end", "2027-03")
        self.assertEqual(rc, 0, out)

    def test_open_ended_counts_12_months(self):
        rc, out, _ = check(self.root, "sub", "domains", "10", "--recurring", "monthly",
                           "--start", "2026-11")
        self.assertEqual(rc, 0, out)
        months = state(self.root)["proposals"]["sub"]["months"]
        self.assertEqual((len(months), months[0], months[-1]), (12, "2026-11", "2027-10"))
        self.assertEqual(check(self.root, "x", "domains", "20.01", "--month", "2027-10")[0], 3)
        self.assertEqual(check(self.root, "y", "domains", "30", "--month", "2027-11")[0], 0)
        self.assertEqual(check(self.root, "z", "domains", "30", "--month", "2026-10")[0], 0)

    def test_other_currency_refused(self):
        for cur in ("EUR", "usd", "US$"):
            rc, out, _ = check(self.root, "p", "hosting", "1", currency=cur)
            self.assertEqual(rc, 3, cur)
            self.assertIn("REFUSED: the currency is not the project currency USD", out)
        self.assertEqual(state(self.root)["proposals"], {})

    def test_rerun_reserves_once(self):
        self.assertEqual(check(self.root, "p", "hosting", "15")[0], 0)
        rc, out, _ = check(self.root, "p", "hosting", "15.00")   # the same amount
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing more is reserved", out)
        self.assertIn("reserved=15.00 ", self.status())
        self.assertEqual([e["event"] for e in log_lines(self.root)], ["register", "check"])

    def test_reused_id_with_different_fields_refused(self):
        check(self.root, "p", "hosting", "15", what="vps")
        for argv, what in ((("hosting", "16"), "vps"), (("ads", "15"), "vps"),
                           (("hosting", "15"), "another vps"),
                           (("hosting", "15", "--month", "2026-11"), "vps")):
            rc, out, _ = check(self.root, "p", *argv, what=what)
            self.assertEqual(rc, 3, argv)
            self.assertIn("REFUSED: proposal id reused with different fields", out)
        self.assertIn("reserved=15.00 ", self.status())

    def test_two_that_fit_alone_but_not_together(self):
        self.assertEqual(check(self.root, "a", "hosting", "15")[0], 0)
        rc, out, _ = check(self.root, "b", "hosting", "15")
        self.assertEqual(rc, 3)
        self.assertIn("would reach 30.00", out)

    def test_unknown_category_refused(self):
        for cat in ("gold", "total", "Hosting"):
            rc, out, _ = check(self.root, "p", cat, "1")
            self.assertEqual(rc, 3, cat)
            self.assertIn("REFUSED: unknown category", out)

    def test_invalid_amounts_refused(self):
        for bad in ("0", "0.00", "-1", "abc", "1.234", "１", "1_000", "1e2",
                    "1234567890123", ""):
            rc, out, _ = check(self.root, "p", "hosting", bad)
            self.assertEqual(rc, 3, bad)
            self.assertIn("REFUSED: the amount is not a valid positive amount", out)

    def test_huge_valid_amount_refused_by_the_cap(self):
        rc, out, _ = check(self.root, "p", "hosting", "999999999999.99")
        self.assertEqual(rc, 3)
        self.assertIn("over its monthly cap", out)

    def test_past_months_refused(self):
        rc, out, _ = check(self.root, "p", "hosting", "1", "--month", "2026-09")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED: month 2026-09 is before the current month 2026-10", out)
        rc, out, _ = check(self.root, "q", "hosting", "1", "--recurring", "monthly",
                           "--start", "2026-01")
        self.assertEqual(rc, 3)
        self.assertIn("before the current month", out)

    def test_default_month_is_the_current_utc_month(self):
        check(self.root, "p", "hosting", "1", today="2026-12-31")
        self.assertEqual(state(self.root)["proposals"]["p"]["months"], ["2026-12"])

    def test_bad_shapes_exit_2(self):
        for extra in (("--month", "2026-13"), ("--month", "26-10"),
                      ("--month", "２０２６-10"),
                      ("--recurring", "weekly", "--start", "2026-10"),
                      ("--recurring", "monthly"),
                      ("--recurring", "monthly", "--start", "2026-12", "--end", "2026-11"),
                      ("--recurring", "monthly", "--start", "2026-10", "--end", "2040-10"),
                      ("--recurring", "monthly", "--start", "9999-12"),
                      ("--recurring", "monthly", "--start", "2026-10", "--month", "2026-10"),
                      ("--start", "2026-10")):
            rc, out, err = check(self.root, "p", "hosting", "1", *extra)
            self.assertEqual(rc, 2, extra)
            self.assertIn("STOP:", out + err, extra)
        for pid in ("P1", "a b", "x" * 65, "ok", "pass-1", "allowed", "granted", "yes-go",
                    "okay", "vps-approve", "accepted"):
            self.assertEqual(check(self.root, pid, "hosting", "1")[0], 2, pid)
        self.assertEqual(check(self.root, "p", "hosting", "1", what=" ")[0], 2)

    def test_replay_after_a_cap_cut_is_refused(self):
        self.assertEqual(check(self.root, "vps", "hosting", "20")[0], 0)
        write_budget(self.root, budget_with(hosting="10"))
        self.assertEqual(run(self.root, "register", "--by", "Dana", "--reason", "cut")[0], 0)
        events = len(log_lines(self.root))
        rc, out, _ = check(self.root, "vps", "hosting", "20")
        self.assertEqual(rc, 3, out)
        self.assertIn("REFUSED: 2026-10 hosting would reach 20.00, over its monthly cap 10.00; "
                      "vps stays reserved until the human withdraws it", out)
        self.assertNotIn("NOT_REFUSED", out)
        self.assertEqual(state(self.root)["proposals"]["vps"]["status"], "open")
        self.assertEqual(len(log_lines(self.root)), events)   # a refused replay writes nothing
        self.assertIn("reserved=20.00 ", self.status())

    def test_replay_after_a_record_pushes_the_total_over_is_refused(self):
        self.assertEqual(check(self.root, "a", "ads", "100")[0], 0)
        self.assertEqual(check(self.root, "b", "hosting", "20")[0], 0)   # total 120, at cap
        self.assertEqual(check(self.root, "b", "hosting", "20")[0], 0)   # replay at the cap
        self.assertEqual(run(self.root, "record", "--id", "a", "--amount", "100.50",
                             "--currency", "USD", "--month", "2026-10", "--by", "Dana")[0], 0)
        rc, out, _ = check(self.root, "b", "hosting", "20")
        self.assertEqual(rc, 3, out)
        self.assertIn("REFUSED: 2026-10 the total would reach 120.50, over the monthly total "
                      "cap 120.00", out)
        self.assertEqual(state(self.root)["proposals"]["b"]["status"], "open")

    def test_open_ended_prints_its_horizon(self):
        rc, out, _ = check(self.root, "sub", "domains", "10", "--recurring", "monthly",
                           "--start", "2026-11")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE: open-ended; reserved only to 2027-10", out)
        rc, out, _ = check(self.root, "sub", "domains", "10", "--recurring", "monthly",
                           "--start", "2026-11")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE: open-ended; reserved only to 2027-10", out)
        rc, out, _ = check(self.root, "fixed", "domains", "1", "--recurring", "monthly",
                           "--start", "2026-11", "--end", "2027-01")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("open-ended", out)

    def test_withdrawn_id_cannot_be_checked_again(self):
        check(self.root, "p", "hosting", "15")
        run(self.root, "withdraw", "--id", "p", "--by", "Dana", "--reason", "no")
        rc, out, _ = check(self.root, "p", "hosting", "15")
        self.assertEqual(rc, 3)
        self.assertIn("was withdrawn by the human", out)


class RecordWithdraw(unittest.TestCase):
    def setUp(self):
        self.root = registered()

    def record(self, pid, amount, month="2026-10", by="Dana", currency="USD", today=TODAY):
        return run(self.root, "record", "--id", pid, "--amount", amount, "--currency",
                   currency, "--month", month, "--by", by, today=today)

    def test_agent_cannot_record_or_withdraw(self):
        check(self.root, "p", "hosting", "15")
        for by in AGENT_LOOKALIKES:
            rc, out, _ = self.record("p", "1", by=by)
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: an agent: name cannot record spend", out)
            rc, out, _ = run(self.root, "withdraw", "--id", "p", "--by", by, "--reason", "x")
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: an agent: name cannot withdraw", out)
        p = state(self.root)["proposals"]["p"]
        self.assertEqual((p["records"], p["status"]), ({}, "open"))

    def test_record_replaces_that_months_reservation(self):
        check(self.root, "sub", "ads", "10", "--recurring", "monthly", "--start", "2026-10")
        rc, out, _ = self.record("sub", "7", month="2026-10")
        self.assertEqual(rc, 0, out)
        self.assertIn("RECORDED: sub 2026-10 7.00 USD by `Dana`", out)
        st = run(self.root, "status")[1]
        self.assertIn("CATEGORY: ads cap=100.50 recorded=7.00 reserved=0.00 headroom=93.50", st)
        st = run(self.root, "status", "--month", "2026-11")[1]
        self.assertIn("CATEGORY: ads cap=100.50 recorded=0.00 reserved=10.00", st)

    def test_record_may_differ_and_counts_against_checks(self):
        check(self.root, "p", "hosting", "5")
        self.record("p", "24")
        self.assertEqual(check(self.root, "q", "hosting", "1.01")[0], 3)
        self.assertEqual(check(self.root, "r", "hosting", "1")[0], 0)

    def test_second_record_replaces_the_first(self):
        check(self.root, "p", "hosting", "5")
        self.record("p", "4")
        rc, out, _ = self.record("p", "6")
        self.assertIn("NOTE: replaced the earlier record of 4.00 for 2026-10", out)
        self.assertEqual(state(self.root)["proposals"]["p"]["records"]["2026-10"]["amount"],
                         600)

    def test_record_over_cap_is_noted(self):
        check(self.root, "p", "hosting", "5")
        rc, out, _ = self.record("p", "30")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE: recorded spend in 2026-10 for hosting is over its monthly cap",
                      out)
        self.assertIn("headroom=-5.00", run(self.root, "status")[1])

    def test_record_refusals(self):
        check(self.root, "p", "hosting", "5")
        self.assertEqual(self.record("nope", "1")[0], 2)
        self.assertEqual(self.record("p", "1", month="2026-11")[0], 2)   # not started
        self.assertEqual(self.record("p", "1.234")[0], 2)
        rc, out, _ = self.record("p", "1", currency="EUR")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED: the currency is not the project currency", out)
        self.assertEqual(state(self.root)["proposals"]["p"]["records"], {})
        self.assertEqual(self.record("p", "0")[0], 0)   # a cancelled spend records 0

    def test_withdraw_frees_and_keeps_records(self):
        check(self.root, "a", "hosting", "15")
        self.assertEqual(check(self.root, "b", "hosting", "15")[0], 3)
        rc, out, _ = run(self.root, "withdraw", "--id", "a", "--by", "Dana", "--reason", "no")
        self.assertEqual(rc, 0, out)
        self.assertIn("WITHDRAWN: a by `Dana`, freed 15.00", out)
        self.assertEqual(check(self.root, "b", "hosting", "15")[0], 0)
        check(self.root, "c", "ads", "10", "--recurring", "monthly", "--start", "2026-10")
        self.record("c", "8")
        run(self.root, "withdraw", "--id", "c", "--by", "Dana", "--reason", "stop")
        st = run(self.root, "status")[1]
        self.assertIn("CATEGORY: ads cap=100.50 recorded=8.00 reserved=0.00", st)
        st = run(self.root, "status", "--month", "2026-11")[1]
        self.assertIn("CATEGORY: ads cap=100.50 recorded=0.00 reserved=0.00", st)

    def test_withdrawn_proposals_recorded_spend_still_counts(self):
        check(self.root, "p", "hosting", "5")
        self.record("p", "24")
        rc, out, _ = run(self.root, "withdraw", "--id", "p", "--by", "Dana", "--reason", "x")
        self.assertEqual(rc, 0, out)
        rc, out, _ = check(self.root, "q", "hosting", "1.01")
        self.assertEqual(rc, 3, out)
        self.assertIn("hosting would reach 25.01, over its monthly cap 25.00", out)
        self.assertEqual(check(self.root, "r", "hosting", "1")[0], 0)

    def test_record_for_a_month_the_proposal_never_covered_is_noted(self):
        check(self.root, "p", "hosting", "5", "--month", "2026-11")
        rc, out, _ = self.record("p", "3", month="2026-10")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE: 2026-10 is not a month that proposal p reserved; recorded all "
                      "the same", out)
        self.assertEqual(state(self.root)["proposals"]["p"]["records"]["2026-10"]["amount"],
                         300)
        rc, out, _ = check(self.root, "q", "hosting", "5")
        self.assertNotIn("is not a month", self.record("q", "5")[1])

    def test_approval_word_names_cannot_record_or_withdraw(self):
        check(self.root, "p", "hosting", "15")
        for by in APPROVAL_NAMES:
            rc, out, _ = self.record("p", "1", by=by)
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: --by holds a word that reads as approval", out)
            rc, out, _ = run(self.root, "withdraw", "--id", "p", "--by", by, "--reason", "x")
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: --by holds a word that reads as approval", out)
        p = state(self.root)["proposals"]["p"]
        self.assertEqual((p["records"], p["status"]), ({}, "open"))

    def test_withdraw_needs_a_reason_and_a_known_id(self):
        check(self.root, "a", "hosting", "15")
        self.assertEqual(run(self.root, "withdraw", "--id", "a", "--by", "Dana")[0], 2)
        self.assertEqual(run(self.root, "withdraw", "--id", "a", "--by", "Dana",
                             "--reason", " ")[0], 2)
        self.assertEqual(run(self.root, "withdraw", "--id", "zz", "--by", "Dana",
                             "--reason", "x")[0], 2)


class Status(unittest.TestCase):
    def test_status_is_read_only(self):
        root = repo()
        rc, out, _ = run(root, "status")
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(root, ".skill-contract")))
        root = registered()
        check(root, "p", "hosting", "5")
        before = snapshot(ldir(root))
        rc, out, _ = run(root, "status")
        self.assertEqual(rc, 0)
        after = snapshot(ldir(root))
        self.assertEqual(before, after)
        self.assertIn("STATUS: 2026-10 currency=USD stale=false", out)
        self.assertIn("TOTAL: cap=120.00 recorded=0.00 reserved=5.00 headroom=115.00", out)

    def test_bad_month(self):
        self.assertEqual(run(registered(), "status", "--month", "2026-1")[0], 2)

    def test_status_refuses_symlinks_like_the_writers(self):
        root = registered()
        os.rename(os.path.join(root, ".skill-contract"), os.path.join(root, "real"))
        os.symlink(os.path.join(root, "real"), os.path.join(root, ".skill-contract"))
        rc, out, _ = run(root, "status")
        self.assertEqual(rc, 2, out)
        self.assertIn("is a symlink", out)
        for name in (L.STATE_FILE, L.LOG_FILE, L.LOCK_FILE):
            root = registered()
            path = os.path.join(ldir(root), name)
            target = os.path.join(root, "outside")
            os.rename(path, target)
            os.symlink(target, path)
            rc, out, _ = run(root, "status")
            self.assertEqual(rc, 2, name)
            self.assertIn("is a symlink", out, name)


class Output(unittest.TestCase):
    """A check never prints an approval word, and a pass writes no envelope."""

    def outputs(self):
        root = registered()
        outs = []

        def c(*argv, **kw):
            rc, out, err = check(root, *argv, **kw)
            outs.append(out + err)
            return rc
        self.assertEqual(c("vps", "hosting", "19.99"), 0)
        c("vps", "hosting", "19.99")
        c("vps", "hosting", "20")
        c("big", "hosting", "25")
        c("p", "ads", "1", currency="EUR")
        c("p", "gold", "1")
        c("p", "ads", "1.234")
        c("p", "ads", "1", "--month", "2026-01")
        c("p", "ads", "1", "--month", "x")
        c("p", "ads", "1", "--recurring", "pass", "--start", "2026-10")
        c("ok", "ads", "1")
        c("p", "ads", "200", "--recurring", "monthly", "--start", "2026-10")
        for argv in (["check"], ["check", "--id", "ok", "allowed"], ["check", "-h"]):
            rc, out, err = run(root, *argv)
            outs.append(out + err)
        write_budget(root, budget_with(hosting="99"))
        c("vps", "hosting", "19.99")
        c("q", "hosting", "1")
        outs.append("".join(check(repo(), "p", "hosting", "1")[1:]))
        return outs

    def test_no_approval_token_in_any_check_output(self):
        outs = self.outputs()
        self.assertGreater(len(outs), 15)
        for text in outs:
            self.assertIsNone(re.search(APPROVAL_TOKEN_RE, text), text)

    def test_no_approval_token_in_any_command_output(self):
        """Every command, on the legal inputs of these fixtures, prints no approval word."""
        root = repo()
        outs = []

        def r(*argv, **kw):
            rc, out, err = run(root, *argv, **kw)
            outs.append(out + err)
            return rc
        self.assertEqual(r("lint"), 0)
        self.assertEqual(r("status"), 2)
        self.assertEqual(r("register", "--by", "Dana"), 0)
        self.assertEqual(r("register", "--by", "Dana"), 0)
        self.assertEqual(r("check", "--id", "vps", "--category", "hosting", "--amount", "5",
                           "--currency", "USD", "--what", "a vps"), 0)
        r("check", "--id", "vps", "--category", "hosting", "--amount", "5", "--currency",
          "USD", "--what", "a vps")
        r("check", "--id", "sub", "--category", "ads", "--amount", "5", "--currency", "USD",
          "--what", "ads", "--recurring", "monthly", "--start", "2026-10")
        r("check", "--id", "big", "--category", "ads", "--amount", "99", "--currency", "USD",
          "--what", "ads")
        self.assertEqual(r("record", "--id", "vps", "--amount", "30", "--currency", "USD",
                           "--month", "2026-10", "--by", "Dana"), 0)
        r("record", "--id", "vps", "--amount", "6", "--currency", "USD", "--month", "2026-09",
          "--by", "Dana")
        r("record", "--id", "vps", "--amount", "4", "--currency", "USD", "--month", "2026-10",
          "--by", "Dana")
        self.assertEqual(r("withdraw", "--id", "sub", "--by", "Dana", "--reason", "no"), 0)
        r("withdraw", "--id", "sub", "--by", "Dana", "--reason", "no")
        r("status")
        r("status", "--month", "2026-11")
        write_budget(root, budget_with(hosting="40", total="150"))
        r("status")
        r("register", "--by", "Dana")
        self.assertEqual(r("register", "--by", "Dana", "--reason", "more hosting"), 0)
        r("lint")
        self.assertGreater(len(outs), 18)
        for text in outs:
            self.assertIsNone(re.search(APPROVAL_TOKEN_RE, text), text)

    def test_the_pass_line_is_exact(self):
        root = registered()
        rc, out, err = check(root, "vps", "hosting", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.splitlines(),
                         ["NOTE: reserved 1.00 USD a month in 2026-10 (1 month)",
                          "NOT_REFUSED: vps — the human's approval is still required"])

    def test_no_envelope_is_written(self):
        root = registered()
        before = sorted(os.listdir(os.path.join(root, ".skill-contract")))
        self.assertEqual(check(root, "vps", "hosting", "1")[0], 0)
        self.assertEqual(sorted(os.listdir(os.path.join(root, ".skill-contract"))), before)
        self.assertEqual(before, ["ledger"])
        self.assertEqual(sorted(os.listdir(ldir(root))),
                         [".gitignore", ".lock", "ledger.json", "log.jsonl"])


class StateFiles(unittest.TestCase):
    def test_gitignore_written_first(self):
        root = registered()
        with open(os.path.join(ldir(root), ".gitignore"), encoding="utf-8") as f:
            self.assertIn("*", f.read().splitlines())

    def test_gitignore_written_before_any_other_write(self):
        root = repo()
        seen = []
        real = L._write_state

        def spy(r, st):
            seen.append(os.path.exists(os.path.join(ldir(r), ".gitignore")))
            return real(r, st)
        with mock.patch.object(L, "_write_state", spy):
            run(root, "register", "--by", "Dana")
        self.assertEqual(seen, [True])

    def test_negated_gitignore_refused(self):
        root = repo()
        os.makedirs(ldir(root))
        with open(os.path.join(ldir(root), ".gitignore"), "w") as f:
            f.write("*\n!ledger.json\n")
        rc, out, _ = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("cannot be shown to be git-ignored", out)
        self.assertFalse(os.path.exists(os.path.join(ldir(root), L.STATE_FILE)))

    def test_symlinked_dir_refused(self):
        root = repo()
        os.makedirs(os.path.join(root, ".skill-contract"))
        os.makedirs(os.path.join(root, "elsewhere"))
        os.symlink(os.path.join(root, "elsewhere"), ldir(root))
        self.assertEqual(run(root, "register", "--by", "Dana")[0], 2)
        self.assertEqual(os.listdir(os.path.join(root, "elsewhere")), [])
        self.assertEqual(run(root, "status")[0], 2)

    def test_symlinked_state_log_and_lock_refused(self):
        for name in (L.STATE_FILE, L.LOG_FILE, L.LOCK_FILE):
            root = registered()
            target = os.path.join(root, "outside")
            path = os.path.join(ldir(root), name)
            with open(path, "rb") as f:
                data = f.read()
            with open(target, "wb") as f:
                f.write(data)
            os.unlink(path)
            os.symlink(target, path)
            rc, out, _ = check(root, "p", "hosting", "1")
            self.assertEqual(rc, 2, name)
            self.assertIn("is a symlink", out, name)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), data, name)

    def test_corrupt_state_is_stop_not_a_traceback(self):
        good = registered()
        check(good, "p", "hosting", "5")
        base = state(good)
        bads = ["{", "[]", '{"version": 2}', "1" * 5000,
                json.dumps(dict(base, seq=True)), json.dumps(dict(base, seq=-1))]
        for field, value in (("amount", True), ("amount", -5), ("amount", 10 ** 30),
                             ("amount", 1.5), ("amount", "5"), ("months", []),
                             ("months", ["2026-13"]), ("status", "approved"),
                             ("records", {"2026-10": {"amount": 10 ** 40, "by": "D",
                                                      "at": "x"}}),
                             ("category", "Bad Cat")):
            st = json.loads(json.dumps(base))
            st["proposals"]["p"][field] = value
            bads.append(json.dumps(st))
        st = json.loads(json.dumps(base))
        st["registrations"][0]["total"] = 10 ** 20
        bads.append(json.dumps(st))
        bads.append('{"version": 1, "seq": 1, "registrations": [{}], "proposals": {}}')
        bads.append('{"version": 1, "seq": 1, "registrations": [], "proposals": {"p": 1}}')
        for text in bads:
            root = registered()
            with open(os.path.join(ldir(root), L.STATE_FILE), "w") as f:
                f.write(text)
            for argv in (("check", "--id", "q", "--category", "hosting", "--amount", "1",
                          "--currency", "USD", "--what", "x"),
                         ("record", "--id", "p", "--amount", "1", "--currency", "USD",
                          "--month", "2026-10", "--by", "Dana"),
                         ("withdraw", "--id", "p", "--by", "Dana", "--reason", "x"),
                         ("register", "--by", "Dana", "--reason", "x"), ("status",)):
                rc, out, _ = run(root, *argv)
                self.assertEqual(rc, 2, (text[:60], argv))
                self.assertIn("STOP: ", out, (text[:60], argv))

    def test_corrupt_log_is_stop(self):
        for text in ("not json\n", '{"seq": 0}\n', '[1]\n', '{"seq": 2}\n{"seq": 1}\n',
                     '{"seq": 1, "event": "register"}\n', '{"seq": %s}\n' % ("9" * 5000)):
            root = registered()
            with open(os.path.join(ldir(root), L.LOG_FILE), "w") as f:
                f.write(text)
            rc, out, _ = check(root, "p", "hosting", "1")
            self.assertEqual(rc, 2, text[:40])
            self.assertIn("STOP: ", out)


class Clock(unittest.TestCase):
    def test_today_refused_outside_the_test_switch(self):
        root = repo()
        env = {k: v for k, v in os.environ.items() if k != L.TEST_CLOCK_ENV}
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(err):
            rc = L.main(["--root", root, "--today", TODAY, "register", "--by", "Dana"])
        self.assertEqual(rc, 2)
        self.assertIn("STOP: --today is for tests only", buf.getvalue())
        self.assertNotIn(L.TEST_CLOCK_ENV, buf.getvalue() + err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(root, ".skill-contract")))

    def test_switch_name_is_not_in_the_docs_or_the_usage(self):
        for rel in ("SKILL.md", os.path.join("references", "ledger.md")):
            with open(os.path.join(SKILL, rel), encoding="utf-8") as f:
                self.assertNotIn(L.TEST_CLOCK_ENV, f.read(), rel)
        self.assertNotIn(L.TEST_CLOCK_ENV, L.__doc__)
        for argv in (["-h"], ["check", "-h"], ["status", "-h"], ["bogus"]):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                L.main(argv)
            self.assertNotIn(L.TEST_CLOCK_ENV, out.getvalue() + err.getvalue(), argv)

    def test_bad_today(self):
        self.assertEqual(run(repo(), "status", today="2026-02-30")[0], 2)

    def test_month_math_has_no_year_limit_traceback(self):
        self.assertEqual(L.month_index("9999-12"), 9999 * 12 + 11)
        self.assertEqual(L.month_label(L.month_index("2027-01")), "2027-01")
        self.assertIsNone(L.month_index("2026-00"))


class NoIO(unittest.TestCase):
    def test_no_socket_no_subprocess(self):
        root = repo()
        with no_io():
            self.assertEqual(run(root, "lint")[0], 0)
            self.assertEqual(run(root, "register", "--by", "Dana")[0], 0)
            self.assertEqual(check(root, "p", "hosting", "5")[0], 0)
            self.assertEqual(run(root, "record", "--id", "p", "--amount", "5", "--currency",
                                 "USD", "--month", "2026-10", "--by", "Dana")[0], 0)
            self.assertEqual(run(root, "withdraw", "--id", "p", "--by", "Dana", "--reason",
                                 "done")[0], 0)
            self.assertEqual(run(root, "status")[0], 0)


class SharedAgentCheck(unittest.TestCase):
    def test_the_three_copies_are_byte_identical(self):
        """_fold(), _is_agent() and their tables are the same text in growth-review and
        idea-validation, so the three checks cannot drift (skipped when this skill is
        installed alone)."""
        def block(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            start = text.index("_CONFUSABLES = ")
            tail = "    return bool(_AGENT_RE.match(folded))\n"
            end = text.index(tail) + len(tail)
            return text[start:end]
        top = os.path.dirname(SKILL)
        others = [os.path.join(top, "growth-review", "assets", "growth.py"),
                  os.path.join(top, "idea-validation", "assets", "ideas.py")]
        if not all(os.path.isfile(p) for p in others):
            self.skipTest("growth-review or idea-validation is not next to this skill")
        mine = block(os.path.join(SKILL, "assets", "ledger.py"))
        self.assertTrue(mine.endswith("_AGENT_RE.match(folded))\n"), mine[-60:])
        for p in others:
            self.assertEqual(block(p), mine, p)


class Version(unittest.TestCase):
    def test_ledger_version_matches_skill_md(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            self.assertIn('  version: "%s"\n' % L.LEDGER_VERSION, f.read())


if __name__ == "__main__":
    unittest.main()
