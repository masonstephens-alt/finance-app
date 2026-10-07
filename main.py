from datetime import date

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.scrollview import ScrollView

from finance_core import FinanceDB, FinanceError

from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

DARK = (0.10, 0.12, 0.15, 1)
MUTED = (0.42, 0.45, 0.49, 1)
GREEN = (0.12, 0.55, 0.30, 1)
RED = (0.75, 0.18, 0.18, 1)
BLUE = (0.18, 0.38, 0.72, 1)
WHITE = (1, 1, 1, 1)


# ---------------------------------------------------------------------------
# KV
# ---------------------------------------------------------------------------

KV = """
#:import dp kivy.metrics.dp

<RootLayout>:
    orientation: "vertical"

    BoxLayout:
        size_hint_y: None
        height: dp(58)
        padding: dp(6)
        spacing: dp(6)

        Button:
            text: "Dashboard"
            on_release: app.go_to("dashboard")

        Button:
            text: "Ledger"
            on_release: app.go_to("ledger")

        Button:
            text: "Goals"
            on_release: app.go_to("goals")

        Button:
            text: "Bills"
            on_release: app.go_to("bills")

        Button:
            text: "Accounts"
            on_release: app.go_to("accounts")

    ScreenManager:
        id: screen_manager


<DashboardScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(8)

        Label:
            text: "Financial Dashboard"
            size_hint_y: None
            height: dp(45)
            font_size: "24sp"
            bold: True
            color: 0.1, 0.12, 0.15, 1

        ScrollView:
            do_scroll_x: False

            BoxLayout:
                id: content
                orientation: "vertical"
                size_hint_y: None
                spacing: dp(8)
                padding: dp(4)
                height: self.minimum_height



<LedgerScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(8)

        Label:
            text: "Ledger"
            size_hint_y: None
            height: dp(45)
            font_size: "24sp"
            bold: True
            color: 0.1, 0.12, 0.15, 1

        ScrollView:
            do_scroll_x: False

            BoxLayout:
                id: entries
                orientation: "vertical"
                size_hint_y: None
                spacing: dp(8)
                padding: dp(4)
                height: self.minimum_height


<GoalsScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(8)

        Label:
            text: "Goals"
            size_hint_y: None
            height: dp(45)
            font_size: "24sp"
            bold: True
            color: 0.1, 0.12, 0.15, 1

        ScrollView:
            do_scroll_x: False

            BoxLayout:
                id: goals_container
                orientation: "vertical"
                size_hint_y: None
                spacing: dp(8)
                padding: dp(4)
                height: self.minimum_height


<BillsScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(8)

        Label:
            text: "Bills"
            size_hint_y: None
            height: dp(45)
            font_size: "24sp"
            bold: True
            color: 0.1, 0.12, 0.15, 1

        Label:
            id: status_label
            text: ""
            size_hint_y: None
            height: dp(32)
            color: 0.12, 0.55, 0.30, 1

        ScrollView:
            do_scroll_x: False

            BoxLayout:
                id: bills_container
                orientation: "vertical"
                size_hint_y: None
                spacing: dp(8)
                padding: dp(4)
                height: self.minimum_height


<AccountsScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(8)

        Label:
            text: "Accounts"
            size_hint_y: None
            height: dp(45)
            font_size: "24sp"
            bold: True
            color: 0.1, 0.12, 0.15, 1

        ScrollView:
            do_scroll_x: False

            BoxLayout:
                id: accounts_container
                orientation: "vertical"
                size_hint_y: None
                spacing: dp(8)
                padding: dp(4)
                height: self.minimum_height
"""


Builder.load_string(KV)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def label(text="", font_size=16, color=DARK, bold=False, height=42):
    widget = Label(
        text=text,
        font_size=font_size,
        color=color,
        bold=bold,
        halign="left",
        valign="middle",
        size_hint_y=None,
        height=height,
    )

    widget.bind(
        size=lambda instance, value:
        setattr(instance, "text_size", (value[0], None))
    )

    return widget


def button(text, callback, color=BLUE):
    widget = Button(
        text=text,
        size_hint_y=None,
        height=46,
        background_normal="",
        background_color=color,
    )

    widget.bind(on_release=callback)

    return widget


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

class RootLayout(BoxLayout):
    pass


# ---------------------------------------------------------------------------
# Base screen
# ---------------------------------------------------------------------------

class BaseScreen(Screen):

    @property
    def db(self):
        return App.get_running_app().db

    def success(self, message):
        if "status_label" in self.ids:
            self.ids.status_label.text = message
            self.ids.status_label.color = GREEN

    def error(self, message):
        if "status_label" in self.ids:
            self.ids.status_label.text = f"Error: {message}"
            self.ids.status_label.color = RED


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardScreen(BaseScreen):

    def on_pre_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0)

    def refresh(self):

        content = self.ids.content
        content.clear_widgets()

        dashboard = self.db.dashboard(date.today(), 45)

        content.add_widget(
            label(
                date.today().strftime("%A, %B %d, %Y"),
                15,
                MUTED,
                height=32,
            )
        )

        # Accounts
        content.add_widget(
            label("Accounts", 20, DARK, True)
        )

        for account in dashboard["accounts"]:

            if account["type"] == "credit_card":

                text = (
                    f"{account['name']}  •  Credit Card\n"
                    f"Owed: ${account['balance_cents'] / 100:,.2f}"
                )

            else:

                text = (
                    f"{account['name']}  •  "
                    f"{account['type'].title()}\n"
                    f"Balance: "
                    f"${account['balance_cents'] / 100:,.2f}\n"
                    f"Unassigned: "
                    f"${account['unassigned_cents'] / 100:,.2f}"
                )

            content.add_widget(
                label(text, 15, DARK, height=70)
            )

        # Goals
        content.add_widget(
            label("Goals", 20, DARK, True)
        )

        if not dashboard["goals"]:

            content.add_widget(
                label("No active goals.", 15, MUTED)
            )

        else:

            for goal in dashboard["goals"]:

                status = "READY" if goal["ready"] else "IN PROGRESS"

                text = f"{goal['name']}  •  {status}\n"

                for requirement in goal["requirements"]:

                    symbol = "✓" if requirement["met"] else "○"

                    text += (
                        f"{symbol} "
                        f"{requirement['label']}\n"
                    )

                content.add_widget(
                    label(
                        text,
                        15,
                        GREEN if goal["ready"] else DARK,
                        height=90,
                    )
                )

        # Payday
        content.add_widget(
            label("Next Payday", 20, DARK, True)
        )

        payday = dashboard["next_payday"]

        if payday:

            text = (
                f"{payday['date'].strftime('%A, %B %d, %Y')}\n"
                f"Expected: "
                f"${payday['expected_cents'] / 100:,.2f}"
            )

            content.add_widget(
                label(text, 15, DARK, height=65)
            )

        else:

            content.add_widget(
                label("No upcoming payday.", 15, MUTED)
            )

        # Bills
        content.add_widget(
            label("Upcoming Bills", 20, DARK, True)
        )

        bills = dashboard["upcoming_bills"]

        if not bills:

            content.add_widget(
                label("No upcoming bills.", 15, MUTED)
            )

        else:

            for bill in bills[:10]:

                postponed = (
                    " • POSTPONED"
                    if bill["postponed"]
                    else ""
                )

                text = (
                    f"{bill['effective_date'].strftime('%b %d')}"
                    f"  •  {bill['name']}\n"
                    f"${bill['amount_cents'] / 100:,.2f}"
                    f"{postponed}"
                )

                content.add_widget(
                    label(text, 15, DARK, height=62)
                )

        # Warnings
        if dashboard["warnings"]:

            content.add_widget(
                label("Warnings", 20, RED, True)
            )

            for warning in dashboard["warnings"]:

                content.add_widget(
                    label(
                        f"{warning['name']} is over-assigned by "
                        f"${warning['short_by_cents'] / 100:,.2f}",
                        15,
                        RED,
                    )
                )


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class LedgerScreen(BaseScreen):

    def on_pre_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0)

    def refresh(self):

        entries = self.ids.entries
        entries.clear_widgets()

        transactions = self.db.recent_transactions(limit=100)

        if not transactions:

            entries.add_widget(
                label(
                    "No transactions recorded yet.",
                    15,
                    MUTED,
                )
            )

            return

        for transaction in transactions:

            amount = transaction["amount_cents"]

            if amount >= 0:

                amount_text = (
                    f"+${amount / 100:,.2f}"
                )

                color = GREEN

            else:

                amount_text = (
                    f"-${abs(amount) / 100:,.2f}"
                )

                color = RED

            text = (
                f"{transaction['date']}\n"
                f"{transaction['account_name']}\n"
                f"{transaction['kind'].replace('_', ' ').title()}"
                f"  {amount_text}"
            )

            if transaction["note"]:

                text += (
                    f"\n{transaction['note']}"
                )

            entries.add_widget(
                label(
                    text,
                    14,
                    color,
                    height=82,
                )
            )


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

class GoalsScreen(BaseScreen):

    def on_pre_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0)

    def refresh(self):

        container = self.ids.goals_container
        container.clear_widgets()

        goals = self.db.all_goal_statuses()

        if not goals:

            container.add_widget(
                label(
                    "No active goals.",
                    15,
                    MUTED,
                )
            )

            return

        for goal in goals:

            status = (
                "READY"
                if goal["ready"]
                else "NOT READY"
            )

            text = (
                f"{goal['name']}  •  {status}\n"
            )

            for requirement in goal["requirements"]:

                symbol = (
                    "✓"
                    if requirement["met"]
                    else "○"
                )

                text += (
                    f"{symbol} "
                    f"{requirement['label']}\n"
                )

            if goal["target_date"]:

                text += (
                    f"Target: "
                    f"{goal['target_date'].strftime('%B %d, %Y')}"
                )

            container.add_widget(
                label(
                    text,
                    15,
                    GREEN if goal["ready"] else DARK,
                    height=100,
                )
            )


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------

class BillsScreen(BaseScreen):

    def on_pre_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0)

    def refresh(self):

        container = self.ids.bills_container
        container.clear_widgets()

        bills = self.db.upcoming_bills(
            date.today(),
            60,
        )

        if not bills:

            container.add_widget(
                label(
                    "No upcoming bills.",
                    15,
                    MUTED,
                )
            )

            return

        for bill in bills:

            postponed = bill["postponed"]

            text = (
                f"{bill['name']}\n"
                f"Amount: "
                f"${bill['amount_cents'] / 100:,.2f}\n"
                f"Normal date: "
                f"{bill['normal_date'].strftime('%b %d, %Y')}\n"
                f"Withdrawal date: "
                f"{bill['effective_date'].strftime('%b %d, %Y')}"
            )

            if postponed:

                text += "\nPOSTPONED"

            box = BoxLayout(
                orientation="vertical",
                size_hint_y=None,
                height=145 if not postponed else 105,
                spacing=5,
                padding=5,
            )

            box.add_widget(
                label(
                    text,
                    14,
                    DARK,
                    height=95 if not postponed else 75,
                )
            )

            if not postponed:

                box.add_widget(
                    button(
                        "Postpone to Next Payday",
                        lambda instance,
                               bill_id=bill["bill_id"]:
                        self.postpone_bill(bill_id),
                    )
                )

            container.add_widget(box)

    def postpone_bill(self, bill_id):

        try:

            new_date = self.db.postpone_bill(
                bill_id,
                date.today(),
            )

            self.success(
                f"Postponed to "
                f"{new_date.strftime('%B %d, %Y')}."
            )

            self.refresh()

        except FinanceError as exc:

            self.error(str(exc))


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class AccountsScreen(BaseScreen):

    def on_pre_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0)

    def refresh(self):

        container = self.ids.accounts_container
        container.clear_widgets()

        accounts = self.db.conn.execute(
            """
            SELECT *
            FROM accounts
            ORDER BY type, name
            """
        ).fetchall()

        if not accounts:

            container.add_widget(
                label(
                    "No accounts yet.",
                    15,
                    MUTED,
                )
            )

            return

        for account in accounts:

            if account["type"] == "credit_card":

                text = (
                    f"{account['name']}\n"
                    f"Credit Card\n"
                    f"Owed: "
                    f"${account['balance_cents'] / 100:,.2f}"
                )

            else:

                unassigned = self.db.unassigned(
                    account["id"]
                )

                text = (
                    f"{account['name']}\n"
                    f"{account['type'].title()}\n"
                    f"Balance: "
                    f"${account['balance_cents'] / 100:,.2f}\n"
                    f"Unassigned: "
                    f"${unassigned / 100:,.2f}"
                )

            container.add_widget(
                label(
                    text,
                    15,
                    DARK,
                    height=85,
                )
            )


# ---------------------------------------------------------------------------
# Screen manager
# ---------------------------------------------------------------------------

class MainScreenManager(ScreenManager):
    pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class FinanceApp(App):

    def build(self):

        self.title = "Personal Finance"

        self.db = FinanceDB(
            "finance.db"
        )

        root = RootLayout()

        manager = root.ids.screen_manager

        manager.add_widget(
            DashboardScreen(
                name="dashboard"
            )
        )

        manager.add_widget(
            LedgerScreen(
                name="ledger"
            )
        )

        manager.add_widget(
            GoalsScreen(
                name="goals"
            )
        )

        manager.add_widget(
            BillsScreen(
                name="bills"
            )
        )

        manager.add_widget(
            AccountsScreen(
                name="accounts"
            )
        )

        manager.current = "dashboard"

        return root

    def go_to(self, screen_name):

        manager = self.root.ids.screen_manager

        manager.current = screen_name

        screen = manager.get_screen(
            screen_name
        )

        if hasattr(screen, "refresh"):

            Clock.schedule_once(
                lambda dt: screen.refresh(),
                0,
            )

    def on_stop(self):

        if hasattr(self, "db"):

            self.db.close()


if __name__ == "__main__":
    FinanceApp().run()