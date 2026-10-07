import unittest
from datetime import date

from finance_core import (AlreadyPostponed, FinanceDB, FinanceError,
                          is_business_day, occurrences, previous_business_day,
                          to_cents)


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.db = FinanceDB(":memory:")
        self.savings = self.db.add_account("Savings", "savings")
        self.card = self.db.add_account("Visa", "credit_card", to_cents(300))
        self.item = self.db.add_goal("New item", to_cents(2500), self.savings)
        self.big = self.db.add_goal("Savings goal", to_cents(4000), self.savings,
                                    target_date="2027-06-01")
        self.db.add_card_requirement(self.item, self.card)  # must be paid off

    def test_goals_do_not_share_money(self):
        self.db.record_deposit(self.savings, to_cents(2500), goal_id=self.item)
        big = self.db.goal_status(self.big)
        self.assertEqual(big["requirements"][0]["current"], 0)  # untouched
        self.assertFalse(big["all_met"])
        self.assertEqual(self.db.unassigned(self.savings), 0)

    def test_card_requirement_blocks_ready(self):
        self.db.record_deposit(self.savings, to_cents(2500), goal_id=self.item)
        self.assertFalse(self.db.goal_status(self.item)["ready"])  # card owes $300
        self.db.record_card_payment(self.card, to_cents(300))
        self.assertTrue(self.db.goal_status(self.item)["ready"])

    def test_completing_one_goal_leaves_other_alone(self):
        self.db.record_deposit(self.savings, to_cents(2500), goal_id=self.item)
        self.db.record_deposit(self.savings, to_cents(1500), goal_id=self.big)
        self.db.complete_goal(self.item)
        self.assertEqual(self.db.conn.execute(
            "SELECT balance_cents FROM accounts WHERE id=?", (self.savings,)
        ).fetchone()[0], to_cents(1500))
        self.assertEqual(self.db.goal_status(self.big)["requirements"][0]["current"], to_cents(1500))

    def test_cannot_spend_earmarked_money_without_goal(self):
        self.db.record_deposit(self.savings, to_cents(500), goal_id=self.item)
        with self.assertRaises(FinanceError):
            self.db.record_withdrawal(self.savings, to_cents(100))

    def test_fund_goal_from_unassigned(self):
        self.db.record_deposit(self.savings, to_cents(1000))
        self.db.fund_goal(self.big, to_cents(600))
        self.assertEqual(self.db.unassigned(self.savings), to_cents(400))
        with self.assertRaises(FinanceError):
            self.db.fund_goal(self.big, to_cents(500))

    def test_over_assignment_is_flagged(self):
        self.db.record_deposit(self.savings, to_cents(1000), goal_id=self.item)
        self.db.set_balance(self.savings, to_cents(700))
        self.assertEqual(self.db.over_assigned()[0]["short_by_cents"], to_cents(300))


class BillTests(unittest.TestCase):
    def setUp(self):
        self.db = FinanceDB(":memory:")
        # biweekly paydays: Oct 9, Oct 23, Nov 6 ...
        self.db.add_payday("2026-10-09", to_cents(1800), "biweekly")
        self.rent = self.db.add_bill("Rent", to_cents(1200), "2026-10-15", "monthly")
        self.today = date(2026, 10, 7)

    def test_postpone_moves_to_next_payday_once(self):
        new = self.db.postpone_bill(self.rent, self.today)
        self.assertEqual(new, date(2026, 10, 23))
        with self.assertRaises(AlreadyPostponed):
            self.db.postpone_bill(self.rent, self.today)

    def test_postponed_bill_still_listed_after_normal_date_passes(self):
        self.db.postpone_bill(self.rent, self.today)
        later = date(2026, 10, 20)  # normal date (Oct 15) is past
        bills = self.db.upcoming_bills(later, days=10)
        self.assertEqual(bills[0]["effective_date"], date(2026, 10, 23))
        self.assertTrue(bills[0]["postponed"])
        with self.assertRaises(AlreadyPostponed):
            self.db.postpone_bill(self.rent, later)

    def test_next_month_is_unaffected_and_can_be_postponed(self):
        self.db.postpone_bill(self.rent, self.today)
        after = date(2026, 10, 24)
        nxt = self.db.upcoming_bills(after, days=30)[0]
        self.assertEqual(nxt["normal_date"], date(2026, 11, 15))
        self.assertFalse(nxt["postponed"])
        self.assertEqual(self.db.postpone_bill(self.rent, after), date(2026, 11, 20))

    def test_undo_allows_repostponing(self):
        self.db.postpone_bill(self.rent, self.today)
        self.db.undo_postpone(self.rent, "2026-10-15")
        self.assertEqual(self.db.postpone_bill(self.rent, self.today), date(2026, 10, 23))

    def test_postpone_requires_a_payday(self):
        db = FinanceDB(":memory:")
        bill = db.add_bill("Phone", 5000, "2026-10-15")
        with self.assertRaises(FinanceError):
            db.postpone_bill(bill, self.today)


class ScheduleTests(unittest.TestCase):
    def test_monthly_31st_clamps_without_drifting(self):
        days = list(occurrences("2026-01-31", "monthly", "2026-01-01", "2026-04-30"))
        self.assertEqual(days, [date(2026, 1, 31), date(2026, 2, 28),
                                date(2026, 3, 31), date(2026, 4, 30)])

    def test_biweekly(self):
        days = list(occurrences("2026-10-09", "biweekly", "2026-10-20", "2026-11-10"))
        self.assertEqual(days, [date(2026, 10, 23), date(2026, 11, 6)])


class PaydayRuleTests(unittest.TestCase):
    """Paydays on the 15th and 30th, moved back to the previous business day."""

    def setUp(self):
        self.db = FinanceDB(":memory:")
        self.p15 = self.db.add_payday_on_day(15, to_cents(1800))
        self.p30 = self.db.add_payday_on_day(30, to_cents(1800))

    def dates(self, start, days):
        return [p["date"] for p in self.db.upcoming_paydays(start, days)]

    def test_saturday_15th_moves_to_friday(self):
        # Aug 15 2026 is a Saturday; Aug 30 2026 is a Sunday.
        self.assertEqual(self.dates("2026-08-01", 31),
                         [date(2026, 8, 14), date(2026, 8, 28)])

    def test_holiday_friday_moves_to_thursday(self):
        self.db.add_day_off("2026-08-14", "company holiday")
        self.assertEqual(self.dates("2026-08-01", 20), [date(2026, 8, 13)])
        self.db.remove_day_off("2026-08-14")
        self.assertEqual(self.dates("2026-08-01", 20), [date(2026, 8, 14)])

    def test_normal_weekdays_do_not_move(self):
        self.assertEqual(self.dates("2026-10-01", 30),
                         [date(2026, 10, 15), date(2026, 10, 30)])

    def test_federal_holiday_on_the_15th(self):
        # Feb 15 2027 is Presidents' Day (Monday): back past the weekend to Fri 12th.
        # The 30th clamps to Feb 28 (a Sunday) -> Fri 26th.
        self.assertEqual(self.dates("2027-02-01", 27),
                         [date(2027, 2, 12), date(2027, 2, 26)])

    def test_sunday_15th(self):
        # Nov 15 2026 is a Sunday -> Fri 13th. Nov 30 2026 is a Monday.
        self.assertEqual(self.dates("2026-11-01", 30),
                         [date(2026, 11, 13), date(2026, 11, 30)])

    def test_each_payday_can_have_its_own_amount(self):
        self.db.update_payday_amount(self.p30, to_cents(2100))
        got = [(p["date"], p["expected_cents"]) for p in self.db.upcoming_paydays("2026-10-01", 30)]
        self.assertEqual(got, [(date(2026, 10, 15), to_cents(1800)),
                               (date(2026, 10, 30), to_cents(2100))])

    def test_bill_postpones_to_adjusted_payday(self):
        # Bill due Aug 14 2026 (which is itself payday). Postpones to the NEXT one: Aug 28.
        bill = self.db.add_bill("Phone", to_cents(80), "2026-08-14", "monthly")
        self.assertEqual(self.db.postpone_bill(bill, date(2026, 8, 10)), date(2026, 8, 28))

    def test_business_day_helpers(self):
        self.assertFalse(is_business_day("2027-07-05"))  # July 4 2027 is Sunday -> Monday observed
        self.assertFalse(is_business_day("2026-11-26"))  # Thanksgiving
        self.assertTrue(is_business_day("2026-11-27"))   # day after is a bank business day
        self.assertEqual(previous_business_day("2026-08-15"), date(2026, 8, 14))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.db = FinanceDB(":memory:")

        self.checking = self.db.add_account(
            "Checking",
            "checking",
            to_cents(2000)
        )

        self.savings = self.db.add_account(
            "Savings",
            "savings",
            to_cents(500)
        )

        self.card = self.db.add_account(
            "Visa",
            "credit_card",
            to_cents(300)
        )

    def test_transfer_between_accounts(self):
        self.db.transfer(
            self.checking,
            self.savings,
            to_cents(500),
            note="Move to savings"
        )

        checking = self.db._row(
            "accounts",
            self.checking,
            "account"
        )

        savings = self.db._row(
            "accounts",
            self.savings,
            "account"
        )

        self.assertEqual(
            checking["balance_cents"],
            to_cents(1500)
        )

        self.assertEqual(
            savings["balance_cents"],
            to_cents(1000)
        )

    def test_transfer_does_not_change_total_money(self):
        before = (
            self.db._row(
                "accounts",
                self.checking,
                "account"
            )["balance_cents"]
            +
            self.db._row(
                "accounts",
                self.savings,
                "account"
            )["balance_cents"]
        )

        self.db.transfer(
            self.checking,
            self.savings,
            to_cents(500)
        )

        after = (
            self.db._row(
                "accounts",
                self.checking,
                "account"
            )["balance_cents"]
            +
            self.db._row(
                "accounts",
                self.savings,
                "account"
            )["balance_cents"]
        )

        self.assertEqual(before, after)

    def test_transfer_creates_two_ledger_entries(self):
        self.db.transfer(
            self.checking,
            self.savings,
            to_cents(500),
            note="Savings transfer"
        )

        checking_transactions = self.db.recent_transactions(
            self.checking
        )

        savings_transactions = self.db.recent_transactions(
            self.savings
        )

        self.assertEqual(len(checking_transactions), 1)
        self.assertEqual(len(savings_transactions), 1)

        self.assertEqual(
            checking_transactions[0]["amount_cents"],
            -to_cents(500)
        )

        self.assertEqual(
            savings_transactions[0]["amount_cents"],
            to_cents(500)
        )

        self.assertEqual(
            checking_transactions[0]["kind"],
            "transfer"
        )

        self.assertEqual(
            savings_transactions[0]["kind"],
            "transfer"
        )

    def test_cannot_transfer_more_than_unassigned_money(self):
        goal = self.db.add_goal(
            "Savings Goal",
            to_cents(1500),
            self.checking
        )

        self.db.fund_goal(
            goal,
            to_cents(1500)
        )

        with self.assertRaises(FinanceError):
            self.db.transfer(
                self.checking,
                self.savings,
                to_cents(501)
            )

    def test_credit_card_cannot_be_transfer_source(self):
        with self.assertRaises(FinanceError):
            self.db.transfer(
                self.card,
                self.checking,
                to_cents(100)
            )

    def test_credit_card_cannot_be_transfer_destination(self):
        with self.assertRaises(FinanceError):
            self.db.transfer(
                self.checking,
                self.card,
                to_cents(100)
            )

    def test_cannot_transfer_account_to_itself(self):
        with self.assertRaises(FinanceError):
            self.db.transfer(
                self.checking,
                self.checking,
                to_cents(100)
            )

    def test_record_expense(self):
        self.db.record_expense(
            self.checking,
            to_cents(75),
            note="Groceries"
        )

        checking = self.db._row(
            "accounts",
            self.checking,
            "account"
        )

        self.assertEqual(
            checking["balance_cents"],
            to_cents(1925)
        )

    def test_record_expense_appears_in_ledger(self):
        self.db.record_expense(
            self.checking,
            to_cents(75),
            note="Groceries"
        )

        transactions = self.db.recent_transactions(
            self.checking
        )

        self.assertEqual(len(transactions), 1)

        self.assertEqual(
            transactions[0]["amount_cents"],
            -to_cents(75)
        )

        self.assertEqual(
            transactions[0]["kind"],
            "expense"
        )

        self.assertEqual(
            transactions[0]["note"],
            "Groceries"
        )

    def test_record_expense_cannot_use_credit_card(self):
        with self.assertRaises(FinanceError):
            self.db.record_expense(
                self.card,
                to_cents(50)
            )

    def test_cannot_spend_earmarked_money_on_expense(self):
        goal = self.db.add_goal(
            "New Item",
            to_cents(1000),
            self.checking
        )

        self.db.fund_goal(
            goal,
            to_cents(1000)
        )

        # $2,000 started in checking.
        # $1,000 is earmarked for the goal.
        # Therefore only $1,000 remains unassigned.
        #
        # Trying to spend $1,001 would require spending
        # $1 of the money earmarked for the goal.
        with self.assertRaises(FinanceError):
            self.db.record_expense(
                self.checking,
                to_cents(1001)
            )

            
    def test_goal_expense_reduces_goal_envelope(self):
        goal = self.db.add_goal(
            "New Item",
            to_cents(1000),
            self.checking
        )

        self.db.fund_goal(
            goal,
            to_cents(1000)
        )

        self.db.record_expense(
            self.checking,
            to_cents(250),
            goal_id=goal,
            note="Bought item"
        )

        status = self.db.goal_status(goal)

        self.assertEqual(
            status["requirements"][0]["current"],
            to_cents(750)
        )

        checking = self.db._row(
            "accounts",
            self.checking,
            "account"
        )

        self.assertEqual(
            checking["balance_cents"],
            to_cents(1750)
        )


if __name__ == "__main__":
    unittest.main()