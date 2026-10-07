from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.uix.screenmanager import Screen, ScreenManager

from finance_core import FinanceDB, money, to_cents


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "finance.db"


class AccountsScreen(Screen):

    def on_kv_post(self, base_widget):
        """
        Called after the KV layout has been built.
        """
        Clock.schedule_once(
            lambda dt: self.refresh_accounts(),
            0
        )

    def refresh_accounts(self):
        container = self.ids.accounts_container
        container.clear_widgets()

        # Get the database from the running Kivy application.
        app = App.get_running_app()
        db = app.db

        accounts = db.conn.execute(
            """
            SELECT id, name, type, balance_cents
            FROM accounts
            ORDER BY type, name
            """
        ).fetchall()

        from kivy.uix.label import Label
        from kivy.uix.boxlayout import BoxLayout

        if not accounts:
            container.add_widget(
                Label(
                    text="No accounts yet.\nTap + to add your first account.",
                    font_size="18sp",
                    color=(0.4, 0.4, 0.4, 1),
                    halign="center",
                    valign="middle",
                )
            )
            return

        for account in accounts:
            card = BoxLayout(
                orientation="vertical",
                size_hint_y=None,
                height=90,
                padding=12,
            )

            name = Label(
                text=account["name"],
                font_size="20sp",
                halign="left",
                valign="middle",
                color=(0.1, 0.1, 0.1, 1),
            )
            name.bind(size=name.setter("text_size"))

            if account["type"] == "credit_card":
                balance_text = f"{money(account['balance_cents'])} owed"
                balance_color = (0.75, 0.15, 0.15, 1)
            else:
                balance_text = money(account["balance_cents"])
                balance_color = (0.15, 0.55, 0.25, 1)

            balance = Label(
                text=balance_text,
                font_size="18sp",
                halign="left",
                valign="middle",
                color=balance_color,
            )
            balance.bind(size=balance.setter("text_size"))

            card.add_widget(name)
            card.add_widget(balance)

            container.add_widget(card)

    def open_add_account(self):
        self.ids.add_account_popup.open()

    def add_account(self):
        name = self.ids.account_name.text.strip()
        account_type = self.ids.account_type.text
        balance_text = self.ids.account_balance.text.strip()

        if not name:
            return

        if account_type not in (
            "savings",
            "checking",
            "credit_card",
        ):
            return

        try:
            balance_cents = to_cents(balance_text or "0")
        except (ValueError, ArithmeticError):
            return

        if balance_cents < 0:
            return

        app = App.get_running_app()

        try:
            app.db.add_account(
                name,
                account_type,
                balance_cents,
            )
        except Exception as exc:
            print(f"Could not add account: {exc}")
            return

        self.ids.account_name.text = ""
        self.ids.account_balance.text = ""
        self.ids.account_type.text = "checking"

        self.ids.add_account_popup.dismiss()

        self.refresh_accounts()


class MainScreenManager(ScreenManager):
    pass


class FinanceApp(App):
    title = "Personal Finance"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.db = FinanceDB(
            str(DATABASE_PATH)
        )

    def build(self):
        Builder.load_file(
            str(BASE_DIR / "finance.kv")
        )

        return MainScreenManager()

    def on_stop(self):
        self.db.close()


if __name__ == "__main__":
    FinanceApp().run()