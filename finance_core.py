"""
finance_core.py - data layer and logic for the personal finance/goals app.

No UI code lives here, so it can be tested on a desktop and later wired into
Kivy/KivyMD screens. All money is stored as integer cents. All dates are
stored as ISO strings (YYYY-MM-DD) and returned as datetime.date objects.

Key ideas
---------
 - Accounts hold the real balances. For a credit_card account, balance_cents is
  the amount OWED (so "balance of zero" means paid off).
 - Goals own an "envelope": a slice of one savings/checking account's balance.
  Money in one goal's envelope never counts toward another goal.
  unassigned = account balance - sum(envelopes of active goals on it).
 - A goal's savings requirement is envelope >= target. Extra requirements can
  say "credit card X must owe at most $Y" (default $0).
 - Paydays can be "the 15th and 30th, moved back to the previous business day".
 - Bills repeat on a schedule. Any single occurrence can be postponed ONCE, to
  the next payday after its normal date.
"""
import calendar
import sqlite3
from datetime import date, timedelta
from decimal import Decimal

ACCOUNT_TYPES = ("savings", "checking", "credit_card")
REPEATS = ("none", "weekly", "biweekly", "monthly")


class FinanceError(Exception):
    pass


class AlreadyPostponed(FinanceError):
    pass


# ----------------------------------------------------------------- helpers --
def to_cents(value):
    """'2500', '2,500.50', 2500.5 -> integer cents."""
    text = str(value).replace(",", "").replace("$", "").strip()
    return int((Decimal(text) * 100).to_integral_value())


def money(cents):
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _d(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def _add_months(d, n):
    m = d.month - 1 + n
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _nth_weekday(year, month, weekday, n):
    """n-th given weekday (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_bank_holidays(year):
    """US Federal Reserve holidays (when banks and most payrolls are closed).
    A holiday on a Sunday is observed Monday. A holiday on a Saturday is NOT
    moved to Friday (that matches the Fed; if your employer observes Friday,
    add it with FinanceDB.add_day_off)."""
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4),
             date(year, 11, 11), date(year, 12, 25)]
    holidays = {d + timedelta(days=1) if d.weekday() == 6 else d for d in fixed}
    holidays |= {
        _nth_weekday(year, 1, 0, 3),    # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),    # Presidents' Day
        _nth_weekday(year, 5, 0, -1),   # Memorial Day
        _nth_weekday(year, 9, 0, 1),    # Labor Day
        _nth_weekday(year, 10, 0, 2),   # Columbus Day
        _nth_weekday(year, 11, 3, 4),   # Thanksgiving
    }
    return holidays


def is_business_day(d, extra_days_off=()):
    """Mon-Fri, not a bank holiday, not in your own extra days off (ISO strings)."""
    d = _d(d)
    return (d.weekday() < 5 and d not in us_bank_holidays(d.year)
            and d.isoformat() not in extra_days_off)


def previous_business_day(d, extra_days_off=()):
    """d itself if it's a business day, otherwise the most recent one before it."""
    d = _d(d)
    while not is_business_day(d, extra_days_off):
        d -= timedelta(days=1)
    return d


def occurrences(anchor, repeat, start, end):
    """Yield each date in [start, end] on which an event anchored at `anchor`
    with the given repeat rule happens. Monthly events keep their original
    day and clamp to month end (the 31st becomes Feb 28, then Mar 31 again)."""
    anchor, start, end = _d(anchor), _d(start), _d(end)
    if repeat == "none":
        if start <= anchor <= end:
            yield anchor
    elif repeat in ("weekly", "biweekly"):
        step = 7 if repeat == "weekly" else 14
        n = max(0, (start - anchor).days // step)
        while True:
            d = anchor + timedelta(days=step * n)
            if d > end:
                return
            if d >= start:
                yield d
            n += 1
    elif repeat == "monthly":
        n = max(0, (start.year - anchor.year) * 12 + start.month - anchor.month - 1)
        while True:
            d = _add_months(anchor, n)
            if d > end:
                return
            if d >= start:
                yield d
            n += 1
    else:
        raise FinanceError(f"Unknown repeat rule: {repeat}")


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('savings','checking','credit_card')),
    balance_cents INTEGER NOT NULL DEFAULT 0   -- credit card: amount owed
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    target_cents INTEGER NOT NULL CHECK (target_cents >= 0),
    target_date TEXT,
    envelope_cents INTEGER NOT NULL DEFAULT 0 CHECK (envelope_cents >= 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed'))
);
CREATE TABLE IF NOT EXISTS goal_card_requirements (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    card_id INTEGER NOT NULL REFERENCES accounts(id),
    max_owed_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    goal_id INTEGER REFERENCES goals(id),
    date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    kind TEXT NOT NULL,   -- deposit, withdrawal, card_payment, card_charge,
                          -- allocate, release, purchase, adjustment
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS paydays (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    first_date TEXT NOT NULL,
    expected_cents INTEGER NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none',   -- or 'monthly_on_day' (see day_of_month)
    day_of_month INTEGER
);
CREATE TABLE IF NOT EXISTS extra_days_off (
    date TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    first_due TEXT NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'monthly',
    account_id INTEGER REFERENCES accounts(id)
);
CREATE TABLE IF NOT EXISTS bill_postponements (
    id INTEGER PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    normal_date TEXT NOT NULL,
    postponed_to TEXT NOT NULL,
    UNIQUE (bill_id, normal_date)          -- enforces "only once" per occurrence
);
"""


class FinanceDB:
    def __init__(self, path="finance.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(paydays)")]
        if "day_of_month" not in cols:
            self.conn.execute("ALTER TABLE paydays ADD COLUMN day_of_month INTEGER")
            self.conn.commit()

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------ lookups --
    def _row(self, table, row_id, label):
        row = self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            raise FinanceError(f"No {label} with id {row_id}")
        return row

    def _active_goal(self, goal_id):
        g = self._row("goals", goal_id, "goal")
        if g["status"] != "active":
            raise FinanceError(f"Goal '{g['name']}' is already completed")
        return g

    @staticmethod
    def _positive(amount_cents):
        if amount_cents <= 0:
            raise FinanceError("Amount must be greater than zero")

    def _log(self, account_id, goal_id, on, amount_cents, kind, note=""):
        self.conn.execute(
            "INSERT INTO transactions (account_id, goal_id, date, amount_cents, kind, note) "
            "VALUES (?,?,?,?,?,?)",
            (account_id, goal_id, _d(on or date.today()).isoformat(), amount_cents, kind, note),
        )

    # ----------------------------------------------------------- accounts --
    def add_account(self, name, type_, balance_cents=0):
        if type_ not in ACCOUNT_TYPES:
            raise FinanceError(f"type must be one of {ACCOUNT_TYPES}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO accounts (name, type, balance_cents) VALUES (?,?,?)",
                (name, type_, balance_cents),
            )
        return cur.lastrowid

    def envelope_total(self, account_id):
        return self.conn.execute(
            "SELECT COALESCE(SUM(envelope_cents),0) FROM goals "
            "WHERE account_id = ? AND status = 'active'", (account_id,)
        ).fetchone()[0]

    def unassigned(self, account_id):
        return self._row("accounts", account_id, "account")["balance_cents"] - self.envelope_total(account_id)

    def over_assigned(self):
        """Accounts whose envelopes add up to more than the real balance."""
        out = []
        for a in self.conn.execute("SELECT * FROM accounts WHERE type != 'credit_card'"):
            free = a["balance_cents"] - self.envelope_total(a["id"])
            if free < 0:
                out.append({"account_id": a["id"], "name": a["name"], "short_by_cents": -free})
        return out

    def set_balance(self, account_id, new_balance_cents, on=None, note="manual correction"):
        """Fix a balance to match your bank. May cause over-assignment."""
        acct = self._row("accounts", account_id, "account")
        with self.conn:
            self.conn.execute("UPDATE accounts SET balance_cents = ? WHERE id = ?",
                              (new_balance_cents, account_id))
            self._log(account_id, None, on, new_balance_cents - acct["balance_cents"], "adjustment", note)

    # -------------------------------------------------------------- money --


    def record_deposit(self, account_id, amount_cents, on=None, goal_id=None, note=""):
        """Add money to savings/checking, optionally into a goal's envelope."""
        self._positive(amount_cents)
        acct = self._row("accounts", account_id, "account")
        if acct["type"] == "credit_card":
            raise FinanceError("Use record_card_payment for credit cards")
        with self.conn:
            if goal_id is not None:
                g = self._active_goal(goal_id)
                if g["account_id"] != account_id:
                    raise FinanceError(f"Goal '{g['name']}' belongs to a different account")
                self.conn.execute("UPDATE goals SET envelope_cents = envelope_cents + ? WHERE id = ?",
                                  (amount_cents, goal_id))
            self.conn.execute("UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                              (amount_cents, account_id))
            self._log(account_id, goal_id, on, amount_cents, "deposit", note)

    def record_withdrawal(self, account_id, amount_cents, on=None, goal_id=None, note=""):
        """Take money out. With goal_id it comes from that envelope; without,
        it can only come from unassigned money."""
        self._positive(amount_cents)
        acct = self._row("accounts", account_id, "account")
        if acct["type"] == "credit_card":
            raise FinanceError("Use record_card_charge for credit cards")
        with self.conn:
            if goal_id is not None:
                g = self._active_goal(goal_id)
                if g["account_id"] != account_id:
                    raise FinanceError(f"Goal '{g['name']}' belongs to a different account")
                if g["envelope_cents"] < amount_cents:
                    raise FinanceError(f"Goal envelope only has {money(g['envelope_cents'])}")
                self.conn.execute("UPDATE goals SET envelope_cents = envelope_cents - ? WHERE id = ?",
                                  (amount_cents, goal_id))
            elif self.unassigned(account_id) < amount_cents:
                raise FinanceError(
                    f"Only {money(self.unassigned(account_id))} is unassigned; the rest is earmarked for goals")
            self.conn.execute("UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
                              (amount_cents, account_id))
            self._log(account_id, goal_id, on, -amount_cents, "withdrawal", note)

    def transfer(self, from_account_id, to_account_id, amount_cents,
                 on=None, note=""):
        """Transfer money from one normal account to another.

        Transfers move existing money and therefore do not count as
        income or expenses.

        Credit cards cannot be the source of a transfer. Use
        record_card_payment() to pay a credit card.
        """
        self._positive(amount_cents)

        if from_account_id == to_account_id:
            raise FinanceError(
                "Source and destination accounts must be different"
            )

        source = self._row(
            "accounts",
            from_account_id,
            "account"
        )

        destination = self._row(
            "accounts",
            to_account_id,
            "account"
        )

        if source["type"] == "credit_card":
            raise FinanceError(
                "A credit card cannot be the source of a transfer"
            )

        if destination["type"] == "credit_card":
            raise FinanceError(
                "Use record_card_payment() to pay a credit card"
            )

        available = self.unassigned(from_account_id)

        if available < amount_cents:
            raise FinanceError(
                f"Only {money(available)} is available in "
                f"'{source['name']}'"
            )

        transfer_date = _d(on or date.today()).isoformat()

        with self.conn:
            self.conn.execute(
                "UPDATE accounts "
                "SET balance_cents = balance_cents - ? "
                "WHERE id = ?",
                (amount_cents, from_account_id),
            )

            self.conn.execute(
                "UPDATE accounts "
                "SET balance_cents = balance_cents + ? "
                "WHERE id = ?",
                (amount_cents, to_account_id),
            )

            self.conn.execute(
                "INSERT INTO transactions "
                "(account_id, goal_id, date, amount_cents, kind, note) "
                "VALUES (?,?,?,?,?,?)",
                (
                    from_account_id,
                    None,
                    transfer_date,
                    -amount_cents,
                    "transfer",
                    note or f"Transfer to {destination['name']}",
                ),
            )

            self.conn.execute(
                "INSERT INTO transactions "
                "(account_id, goal_id, date, amount_cents, kind, note) "
                "VALUES (?,?,?,?,?,?)",
                (
                    to_account_id,
                    None,
                    transfer_date,
                    amount_cents,
                    "transfer",
                    note or f"Transfer from {source['name']}",
                ),
            )

    def record_expense(self, account_id, amount_cents, on=None,
                       goal_id=None, note=""):
        """Record an expense paid directly from checking or savings.

        If goal_id is supplied, the expense comes from that goal's
        earmarked money. Otherwise it must come from unassigned money.
        """
        self._positive(amount_cents)

        acct = self._row(
            "accounts",
            account_id,
            "account"
        )

        if acct["type"] == "credit_card":
            raise FinanceError(
                "Use record_card_charge() for credit-card purchases"
            )

        with self.conn:
            if goal_id is not None:
                g = self._active_goal(goal_id)

                if g["account_id"] != account_id:
                    raise FinanceError(
                        f"Goal '{g['name']}' belongs to a different account"
                    )

                if g["envelope_cents"] < amount_cents:
                    raise FinanceError(
                        f"Goal envelope only has "
                        f"{money(g['envelope_cents'])}"
                    )

                self.conn.execute(
                    "UPDATE goals "
                    "SET envelope_cents = envelope_cents - ? "
                    "WHERE id = ?",
                    (amount_cents, goal_id),
                )

            elif self.unassigned(account_id) < amount_cents:
                raise FinanceError(
                    f"Only {money(self.unassigned(account_id))} is "
                    f"unassigned; the rest is earmarked for goals"
                )

            self.conn.execute(
                "UPDATE accounts "
                "SET balance_cents = balance_cents - ? "
                "WHERE id = ?",
                (amount_cents, account_id),
            )

            self._log(
                account_id,
                goal_id,
                on,
                -amount_cents,
                "expense",
                note,
            )


    def record_card_payment(self, card_id, amount_cents, on=None, note=""):
        self._positive(amount_cents)
        card = self._row("accounts", card_id, "account")
        if card["type"] != "credit_card":
            raise FinanceError(f"'{card['name']}' is not a credit card")
        if amount_cents > card["balance_cents"]:
            raise FinanceError(f"Payment exceeds the {money(card['balance_cents'])} owed")
        with self.conn:
            self.conn.execute("UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
                              (amount_cents, card_id))
            self._log(card_id, None, on, -amount_cents, "card_payment", note)

    def record_card_charge(self, card_id, amount_cents, on=None, note=""):
        self._positive(amount_cents)
        card = self._row("accounts", card_id, "account")
        if card["type"] != "credit_card":
            raise FinanceError(f"'{card['name']}' is not a credit card")
        with self.conn:
            self.conn.execute("UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                              (amount_cents, card_id))
            self._log(card_id, None, on, amount_cents, "card_charge", note)

    def recent_transactions(self, account_id=None, limit=50):
        sql = "SELECT t.*, a.name AS account_name FROM transactions t JOIN accounts a ON a.id = t.account_id"
        args = []
        if account_id is not None:
            sql += " WHERE t.account_id = ?"
            args.append(account_id)
        sql += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    # -------------------------------------------------------------- goals --
    def add_goal(self, name, target_cents, account_id, target_date=None):
        acct = self._row("accounts", account_id, "account")
        if acct["type"] == "credit_card":
            raise FinanceError("A goal's savings must live in a savings or checking account")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO goals (name, account_id, target_cents, target_date) VALUES (?,?,?,?)",
                (name, account_id, target_cents, _d(target_date).isoformat() if target_date else None),
            )
        return cur.lastrowid

    def add_card_requirement(self, goal_id, card_id, max_owed_cents=0):
        self._active_goal(goal_id)
        if self._row("accounts", card_id, "account")["type"] != "credit_card":
            raise FinanceError("Requirement account must be a credit card")
        with self.conn:
            self.conn.execute(
                "INSERT INTO goal_card_requirements (goal_id, card_id, max_owed_cents) VALUES (?,?,?)",
                (goal_id, card_id, max_owed_cents))

    def fund_goal(self, goal_id, amount_cents, on=None):
        """Earmark existing unassigned money for a goal (no balance change)."""
        self._positive(amount_cents)
        g = self._active_goal(goal_id)
        free = self.unassigned(g["account_id"])
        if amount_cents > free:
            raise FinanceError(f"Only {money(free)} is unassigned")
        with self.conn:
            self.conn.execute("UPDATE goals SET envelope_cents = envelope_cents + ? WHERE id = ?",
                              (amount_cents, goal_id))
            self._log(g["account_id"], goal_id, on, amount_cents, "allocate")

    def release_from_goal(self, goal_id, amount_cents, on=None):
        """Move money from a goal's envelope back to unassigned."""
        self._positive(amount_cents)
        g = self._active_goal(goal_id)
        if amount_cents > g["envelope_cents"]:
            raise FinanceError(f"Envelope only has {money(g['envelope_cents'])}")
        with self.conn:
            self.conn.execute("UPDATE goals SET envelope_cents = envelope_cents - ? WHERE id = ?",
                              (amount_cents, goal_id))
            self._log(g["account_id"], goal_id, on, -amount_cents, "release")

    def complete_goal(self, goal_id, spent_cents=None, on=None):
        """Mark a goal done (you bought the thing). Spends `spent_cents` from
        the envelope (default: all of it); any leftover becomes unassigned."""
        g = self._active_goal(goal_id)
        spent = g["envelope_cents"] if spent_cents is None else spent_cents
        if spent < 0 or spent > g["envelope_cents"]:
            raise FinanceError(f"Can spend at most {money(g['envelope_cents'])} from this goal")
        with self.conn:
            if spent:
                self.conn.execute("UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
                                  (spent, g["account_id"]))
                self._log(g["account_id"], goal_id, on, -spent, "purchase", g["name"])
            self.conn.execute("UPDATE goals SET envelope_cents = 0, status = 'completed' WHERE id = ?",
                              (goal_id,))

    def goal_status(self, goal_id):
        g = self._row("goals", goal_id, "goal")
        reqs = [{
            "label": f"Saved {money(g['envelope_cents'])} of {money(g['target_cents'])}",
            "current": g["envelope_cents"], "needed": g["target_cents"],
            "met": g["envelope_cents"] >= g["target_cents"],
        }]
        for r in self.conn.execute(
            "SELECT r.max_owed_cents, a.name, a.balance_cents FROM goal_card_requirements r "
            "JOIN accounts a ON a.id = r.card_id WHERE r.goal_id = ?", (goal_id,)
        ):
            limit = "paid off" if r["max_owed_cents"] == 0 else f"owe at most {money(r['max_owed_cents'])}"
            reqs.append({
                "label": f"{r['name']}: owe {money(r['balance_cents'])} (needs {limit})",
                "current": r["balance_cents"], "needed": r["max_owed_cents"],
                "met": r["balance_cents"] <= r["max_owed_cents"],
            })
        all_met = all(r["met"] for r in reqs)
        return {
            "id": g["id"], "name": g["name"], "status": g["status"],
            "target_date": _d(g["target_date"]) if g["target_date"] else None,
            "requirements": reqs, "all_met": all_met,
            "ready": all_met and g["status"] == "active",
        }

    def all_goal_statuses(self, include_completed=False):
        sql = "SELECT id FROM goals" + ("" if include_completed else " WHERE status = 'active'")
        return [self.goal_status(r["id"]) for r in self.conn.execute(sql + " ORDER BY id")]

    # ----------------------------------------------------------- paydays --
    def add_payday(self, first_date, expected_cents, repeat="none", label=""):
        if repeat not in REPEATS:
            raise FinanceError(f"repeat must be one of {REPEATS}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO paydays (label, first_date, expected_cents, repeat) VALUES (?,?,?,?)",
                (label, _d(first_date).isoformat(), expected_cents, repeat))
        return cur.lastrowid

    def update_payday_amount(self, payday_id, expected_cents):
        self._row("paydays", payday_id, "payday")
        with self.conn:
            self.conn.execute("UPDATE paydays SET expected_cents = ? WHERE id = ?",
                              (expected_cents, payday_id))

    def add_payday_on_day(self, day_of_month, expected_cents, label=""):
        """A payday that falls on the same day every month (e.g. 15 and 30).
        If that day is a weekend/holiday/day off, it moves back to the most
        recent business day. Days past month end (30 in February) use the
        last day of the month. Add one row per payday (15th and 30th)."""
        if not 1 <= day_of_month <= 31:
            raise FinanceError("day_of_month must be 1-31")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO paydays (label, first_date, expected_cents, repeat, day_of_month) "
                "VALUES (?,?,?,?,?)",
                (label, date.today().isoformat(), expected_cents, "monthly_on_day", day_of_month))
        return cur.lastrowid

    def add_day_off(self, day, note=""):
        """Mark an extra non-business day (company holiday, bank closure)."""
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO extra_days_off (date, note) VALUES (?,?)",
                              (_d(day).isoformat(), note))

    def remove_day_off(self, day):
        with self.conn:
            self.conn.execute("DELETE FROM extra_days_off WHERE date = ?", (_d(day).isoformat(),))

    def _extra_days_off(self):
        return {r["date"] for r in self.conn.execute("SELECT date FROM extra_days_off")}

    def _payday_dates(self, p, start, end, off):
        if p["repeat"] != "monthly_on_day":
            yield from occurrences(p["first_date"], p["repeat"], start, end)
            return
        y, m = start.year, start.month
        while date(y, m, 1) <= end + timedelta(days=10):
            last = calendar.monthrange(y, m)[1]
            d = previous_business_day(date(y, m, min(p["day_of_month"], last)), off)
            if start <= d <= end:
                yield d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    def upcoming_paydays(self, today=None, days=45):
        today = _d(today or date.today())
        end = today + timedelta(days=days)
        off = self._extra_days_off()
        out = []
        for p in self.conn.execute("SELECT * FROM paydays"):
            for d in self._payday_dates(p, today, end, off):
                out.append({"payday_id": p["id"], "label": p["label"], "date": d,
                            "expected_cents": p["expected_cents"]})
        return sorted(out, key=lambda x: x["date"])

    def next_payday(self, today=None, strictly_after=False):
        today = _d(today or date.today())
        start = today + timedelta(days=1) if strictly_after else today
        found = self.upcoming_paydays(start, days=400)
        return found[0] if found else None

    # ------------------------------------------------------------- bills --
    def add_bill(self, name, amount_cents, first_due, repeat="monthly", account_id=None):
        if repeat not in REPEATS:
            raise FinanceError(f"repeat must be one of {REPEATS}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO bills (name, amount_cents, first_due, repeat, account_id) VALUES (?,?,?,?,?)",
                (name, amount_cents, _d(first_due).isoformat(), repeat, account_id))
        return cur.lastrowid

    def _bill_occurrences(self, bill, today, days):
        posts = {r["normal_date"]: r["postponed_to"] for r in self.conn.execute(
            "SELECT normal_date, postponed_to FROM bill_postponements WHERE bill_id = ?", (bill["id"],))}
        out = []
        # Look back far enough to catch an occurrence postponed into the future.
        for d in occurrences(bill["first_due"], bill["repeat"],
                             today - timedelta(days=120), today + timedelta(days=days + 62)):
            p = posts.get(d.isoformat())
            out.append({
                "bill_id": bill["id"], "name": bill["name"], "amount_cents": bill["amount_cents"],
                "account_id": bill["account_id"], "normal_date": d,
                "effective_date": _d(p) if p else d, "postponed": p is not None,
            })
        return out

    def upcoming_bills(self, today=None, days=45):
        """Bills whose expected withdrawal date (after any postponement) falls
        within the next `days` days, soonest first."""
        today = _d(today or date.today())
        end = today + timedelta(days=days)
        out = []
        for bill in self.conn.execute("SELECT * FROM bills"):
            out += [o for o in self._bill_occurrences(bill, today, days)
                    if today <= o["effective_date"] <= end]
        return sorted(out, key=lambda o: (o["effective_date"], o["name"]))

    def _current_occurrence(self, bill, today):
        for o in sorted(self._bill_occurrences(bill, today, 400), key=lambda o: o["normal_date"]):
            if o["effective_date"] >= today:
                return o
        return None

    def postpone_bill(self, bill_id, today=None):
        """Move the bill's current occurrence to the next payday after its
        normal date. Allowed once per occurrence. Returns the new date."""
        today = _d(today or date.today())
        bill = self._row("bills", bill_id, "bill")
        cur = self._current_occurrence(bill, today)
        if cur is None:
            raise FinanceError("This bill has no upcoming occurrence")
        if cur["postponed"]:
            raise AlreadyPostponed(
                f"'{bill['name']}' was already postponed to {cur['effective_date']}")
        payday = self.next_payday(cur["normal_date"], strictly_after=True)
        if payday is None:
            raise FinanceError("No upcoming payday to postpone to - add one first")
        with self.conn:
            self.conn.execute(
                "INSERT INTO bill_postponements (bill_id, normal_date, postponed_to) VALUES (?,?,?)",
                (bill_id, cur["normal_date"].isoformat(), payday["date"].isoformat()))
        return payday["date"]

    def undo_postpone(self, bill_id, normal_date):
        """Take back a postponement (e.g. a mis-tap)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM bill_postponements WHERE bill_id = ? AND normal_date = ?",
                (bill_id, _d(normal_date).isoformat()))

    # --------------------------------------------------------- dashboard --
    def dashboard(self, today=None, days=45):
        """Everything the main screen needs, in one call."""
        today = _d(today or date.today())
        accounts = []
        for a in self.conn.execute("SELECT * FROM accounts ORDER BY type, name"):
            row = dict(a)
            if a["type"] != "credit_card":
                row["unassigned_cents"] = self.unassigned(a["id"])
            accounts.append(row)
        return {
            "accounts": accounts,
            "goals": self.all_goal_statuses(),
            "next_payday": self.next_payday(today),
            "upcoming_bills": self.upcoming_bills(today, days),
            "warnings": self.over_assigned(),
        }