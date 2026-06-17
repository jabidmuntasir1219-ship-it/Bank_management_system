import tkinter as tk
from tkinter import messagebox, ttk
import sqlite3
import random
import hashlib
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
DB_NAME = "bank_system.db"

# Default admin password: "admin"  — change before deployment.
_ADMIN_PASSWORD_HASH = hashlib.sha256("admin".encode()).hexdigest()

MIN_INITIAL_DEPOSIT      = 500.0
MIN_BALANCE_RESERVE      = 500.0    # Cannot withdraw below this
MAX_LOAN_MULTIPLIER      = 10.0     # Max loan = 10× current balance
ANNUAL_SAVINGS_RATE      = 0.05     # 5%  conventional savings interest (cost)
ANNUAL_LOAN_RATE         = 0.10     # 10% conventional loan interest (revenue)
DAYS_PER_MONTH           = 30.4375  # Average calendar month

# Mudarabah profit-sharing ratios
MUDARABAH_CUSTOMER_SHARE = 0.70     # 70% of net profit → Shariah customers
MUDARABAH_BANK_SHARE     = 0.30     # 30% of net profit → bank
STATUTORY_RESERVE_RATE   = 0.20     # 20% of bank's share → statutory reserve
RETAINED_EARNINGS_RATE   = 0.80     # 80% of bank's share → retained earnings

# Provisional Shariah rate used ONLY as a temporary placeholder credit
# while the real Mudarabah profit is being accumulated.  When the admin
# distributes Mudarabah profit the per-account share overrides this.
ANNUAL_SHARIAH_PROVISIONAL = 0.00   # 0% — no provisional credit; real profit
                                     # is distributed via Mudarabah calculation


# ---------------------------------------------------------------------------
# DATABASE INITIALISATION
# ---------------------------------------------------------------------------
def _migrate_db(conn: sqlite3.Connection) -> None:
    """
    Idempotent migration: upgrades an old-schema DB (column 'username') to
    the new schema (column 'account_no').  Safe to call on already-correct DBs.
    """
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(accounts)")
    acc_cols = {row[1] for row in cur.fetchall()}

    if "username" in acc_cols and "account_no" not in acc_cols:
        cur.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TABLE IF NOT EXISTS accounts_new (
                account_no   TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                mobile       TEXT NOT NULL DEFAULT '',
                nid          TEXT NOT NULL DEFAULT '' UNIQUE,
                account_type TEXT NOT NULL,
                balance      REAL NOT NULL DEFAULT 0.0,
                loan         REAL NOT NULL DEFAULT 0.0,
                last_update  TEXT NOT NULL
            );
            INSERT INTO accounts_new
                (account_no,name,mobile,nid,account_type,balance,loan,last_update)
            SELECT username, name,
                   COALESCE(mobile,''), COALESCE(nid,username),
                   COALESCE(account_type,'Conventional'),
                   COALESCE(balance,0.0), COALESCE(loan,0.0),
                   COALESCE(last_update,datetime('now'))
            FROM accounts;
            DROP TABLE accounts;
            ALTER TABLE accounts_new RENAME TO accounts;
            PRAGMA foreign_keys = ON;
        """)

    cur.execute("PRAGMA table_info(transactions)")
    txn_cols = {row[1] for row in cur.fetchall()}

    if "username" in txn_cols and "account_no" not in txn_cols:
        cur.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TABLE IF NOT EXISTS transactions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_no TEXT NOT NULL,
                type       TEXT NOT NULL,
                amount     REAL NOT NULL,
                timestamp  TEXT NOT NULL
            );
            INSERT INTO transactions_new(id,account_no,type,amount,timestamp)
            SELECT id,username,type,amount,timestamp FROM transactions;
            DROP TABLE transactions;
            ALTER TABLE transactions_new RENAME TO transactions;
            PRAGMA foreign_keys = ON;
        """)

    conn.commit()


def init_db() -> None:
    with sqlite3.connect(DB_NAME) as conn:
        _migrate_db(conn)
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_no   TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                mobile       TEXT NOT NULL DEFAULT '',
                nid          TEXT NOT NULL UNIQUE,
                account_type TEXT NOT NULL,
                balance      REAL NOT NULL DEFAULT 0.0,
                loan         REAL NOT NULL DEFAULT 0.0,
                last_update  TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_no TEXT NOT NULL,
                type       TEXT NOT NULL,
                amount     REAL NOT NULL,
                timestamp  TEXT NOT NULL,
                FOREIGN KEY(account_no) REFERENCES accounts(account_no)
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_txn_account
            ON transactions(account_no)
        """)

        conn.commit()


init_db()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_account_no() -> str:
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        while True:
            candidate = "".join(random.choices("0123456789", k=10))
            cur.execute("SELECT 1 FROM accounts WHERE account_no = ?", (candidate,))
            if not cur.fetchone():
                return candidate


def _validate_mobile(mobile: str) -> bool:
    return bool(re.fullmatch(r"01[3-9]\d{8}", mobile))


def _validate_nid(nid: str) -> bool:
    return bool(re.fullmatch(r"\d{10}|\d{17}|[A-Z]{2}\d{7}", nid, re.IGNORECASE))


def _safe_float(text: str) -> tuple[bool, float]:
    try:
        v = float(text.strip())
        return True, v
    except (ValueError, AttributeError):
        return False, 0.0


def _center_window(win, w: int, h: int) -> None:
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")


def _make_toplevel(parent, title: str, w: int, h: int) -> tk.Toplevel:
    win = tk.Toplevel(parent)
    win.title(title)
    win.resizable(False, False)
    win.grab_set()
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    _center_window(win, w, h)
    return win


# ---------------------------------------------------------------------------
# INTEREST / PROVISIONAL PROFIT ENGINE
# ---------------------------------------------------------------------------
def apply_accrued_interest(account_no: str) -> dict | None:
    """
    Conventional accounts: compound savings interest credited to balance.
    Shariah accounts     : NO provisional credit is applied here.
                           Real profit is distributed by calculate_and_distribute_mudarabah().
                           last_update is still refreshed so time doesn't accumulate.

    Loan interest (Conventional only): accrued on outstanding loan balance.
    """
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT account_type, balance, loan, last_update "
            "FROM accounts WHERE account_no = ?",
            (account_no,)
        )
        row = cur.fetchone()
        if row is None:
            return None

        ac_type, balance, loan, last_update_str = row
        now   = datetime.now()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")

        try:
            last_update = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            last_update = now

        days_passed   = (now - last_update).total_seconds() / 86400.0
        months_passed = days_passed / DAYS_PER_MONTH

        savings_credit = 0.0
        loan_interest  = 0.0
        t_entries      = []

        if months_passed > 0.0001:

            # ── Conventional savings interest (bank's cost) ───────────────────
            if ac_type == "Conventional" and balance > 0.0:
                monthly_rate   = ANNUAL_SAVINGS_RATE / 12
                new_balance    = balance * ((1 + monthly_rate) ** months_passed)
                savings_credit = new_balance - balance
                label = f"Auto Interest Accrual ({months_passed:.4f} mo)"
                if round(savings_credit, 2) >= 0.01:
                    t_entries.append((account_no, label,
                                      round(savings_credit, 4), now_s))

            # ── Shariah: no provisional credit — Mudarabah distributes profit ─
            # (ANNUAL_SHARIAH_PROVISIONAL = 0.0, so nothing is added here)

            # ── Loan interest (Conventional only, bank's revenue) ─────────────
            if loan > 0.0 and ac_type == "Conventional":
                loan_monthly_rate = ANNUAL_LOAN_RATE / 12
                new_loan          = loan * ((1 + loan_monthly_rate) ** months_passed)
                loan_interest     = new_loan - loan
                l_label = f"Auto Loan Interest Accrual ({months_passed:.4f} mo)"
                if round(loan_interest, 2) >= 0.01:
                    t_entries.append((account_no, l_label,
                                      round(loan_interest, 4), now_s))

        # Always refresh last_update
        cur.execute(
            "UPDATE accounts "
            "SET balance = balance + ?, loan = loan + ?, last_update = ? "
            "WHERE account_no = ?",
            (savings_credit, loan_interest, now_s, account_no)
        )
        if t_entries:
            cur.executemany(
                "INSERT INTO transactions (account_no, type, amount, timestamp) "
                "VALUES (?, ?, ?, ?)",
                t_entries
            )
        conn.commit()

        cur.execute("SELECT * FROM accounts WHERE account_no = ?", (account_no,))
        r = cur.fetchone()
        if r is None:
            return None
        cols = ["account_no", "name", "mobile", "nid",
                "account_type", "balance", "loan", "last_update"]
        return dict(zip(cols, r))


def log_transaction(account_no: str, t_type: str, amount: float) -> None:
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "INSERT INTO transactions (account_no, type, amount, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (account_no, t_type, amount, _now_str())
        )
        conn.commit()


# ---------------------------------------------------------------------------
# MUDARABAH ENGINE
# ---------------------------------------------------------------------------
def calculate_bank_total_profit() -> tuple[float, float, float]:
    """
    Reads the transactions ledger to compute the bank's P&L.

    Returns (total_revenue, total_cost, net_profit) — all in BDT.

    Total Revenue = SUM of all 'Auto Loan Interest Accrual' rows
                    (interest the bank EARNED from borrowers)

    Total Cost    = SUM of all 'Auto Interest Accrual' rows
                    (conventional savings interest the bank PAID out)
                  + SUM of all 'Auto Halal Profit Accrual' rows
                    (any provisional Shariah profit paid out — currently 0
                     since ANNUAL_SHARIAH_PROVISIONAL = 0, but kept for
                     forward-compatibility if a provisional rate is added)

    Net Profit    = Total Revenue - Total Cost
    """
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()

        # Revenue: loan interest charged to borrowers
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0.0)
            FROM transactions
            WHERE type LIKE 'Auto Loan Interest Accrual%'
        """)
        total_revenue = cur.fetchone()[0]

        # Cost: conventional savings interest credited to depositors
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0.0)
            FROM transactions
            WHERE type LIKE 'Auto Interest Accrual%'
        """)
        conventional_cost = cur.fetchone()[0]

        # Cost: any provisional Shariah profit credited (currently 0)
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0.0)
            FROM transactions
            WHERE type LIKE 'Auto Halal Profit Accrual%'
        """)
        shariah_provisional_cost = cur.fetchone()[0]

        total_cost  = conventional_cost + shariah_provisional_cost
        net_profit  = total_revenue - total_cost

    return round(total_revenue, 2), round(total_cost, 2), round(net_profit, 2)


def calculate_and_distribute_mudarabah() -> dict:
    """
    Core Mudarabah distribution engine.

    Step 1 — Compute bank P&L:
        Net Profit = Total Loan Interest Revenue − Total Deposit Interest Cost

    Step 2 — Split net profit:
        Mudarabah Pool (70%) → distributed to Shariah customers
        Bank's Share   (30%) → split into statutory reserve & retained earnings

    Step 3 — Per-customer distribution (pro-rata by Shariah balance):
        Each Shariah customer's share =
            (their balance / total Shariah deposits) × Mudarabah Pool

    Step 4 — Credit each customer's balance & log a transaction.

    Returns a result dict describing the full P&L and per-account payouts,
    or an error/info string if distribution cannot proceed.
    """
    total_revenue, total_cost, net_profit = calculate_bank_total_profit()

    if net_profit <= 0:
        return {
            "status":        "no_profit",
            "total_revenue": total_revenue,
            "total_cost":    total_cost,
            "net_profit":    net_profit,
            "message":       (
                "Bank is operating at a net loss or break-even. "
                "No Mudarabah profit to distribute.\n\n"
                f"Total Revenue (Loan Interest) : {total_revenue:,.2f} BDT\n"
                f"Total Cost   (Deposit Interest): {total_cost:,.2f} BDT\n"
                f"Net Profit                    : {net_profit:,.2f} BDT"
            )
        }

    # ── Profit split ─────────────────────────────────────────────────────────
    mudarabah_pool      = round(net_profit * MUDARABAH_CUSTOMER_SHARE, 2)  # 70%
    bank_share          = round(net_profit * MUDARABAH_BANK_SHARE,     2)  # 30%
    statutory_reserve   = round(bank_share  * STATUTORY_RESERVE_RATE,  2)  # 20% of 30%
    retained_earnings   = round(bank_share  * RETAINED_EARNINGS_RATE,  2)  # 80% of 30%
    # Note: statutory_reserve + retained_earnings = bank_share exactly (no residual)

    # ── Fetch all Shariah accounts and their current balances ─────────────────
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT account_no, name, balance
            FROM accounts
            WHERE account_type = 'Shariah' AND balance > 0
        """)
        shariah_accounts = cur.fetchall()   # [(account_no, name, balance), ...]

    if not shariah_accounts:
        return {
            "status":             "no_shariah_accounts",
            "total_revenue":      total_revenue,
            "total_cost":         total_cost,
            "net_profit":         net_profit,
            "mudarabah_pool":     mudarabah_pool,
            "bank_share":         bank_share,
            "statutory_reserve":  statutory_reserve,
            "retained_earnings":  retained_earnings,
            "distributions":      [],
            "message":            (
                "No active Shariah accounts found. "
                "Mudarabah pool of "
                f"{mudarabah_pool:,.2f} BDT cannot be distributed."
            )
        }

    total_shariah_balance = sum(row[2] for row in shariah_accounts)

    # ── Pro-rata distribution & DB update ────────────────────────────────────
    distributions = []
    now_s = _now_str()

    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        for acc_no, name, bal in shariah_accounts:
            share_ratio   = bal / total_shariah_balance
            customer_payout = round(mudarabah_pool * share_ratio, 2)

            if customer_payout < 0.01:
                # Too small to be meaningful — skip to avoid dust entries
                continue

            # Credit customer's balance
            cur.execute(
                "UPDATE accounts SET balance = balance + ? WHERE account_no = ?",
                (customer_payout, acc_no)
            )
            # Log as a Mudarabah distribution transaction
            cur.execute(
                "INSERT INTO transactions (account_no, type, amount, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (acc_no,
                 f"Mudarabah Profit Distribution ({share_ratio*100:.2f}% share)",
                 customer_payout,
                 now_s)
            )
            distributions.append({
                "account_no":    acc_no,
                "name":          name,
                "balance":       round(bal, 2),
                "share_ratio":   round(share_ratio * 100, 4),
                "payout":        customer_payout,
            })

        conn.commit()

    return {
        "status":             "distributed",
        "total_revenue":      total_revenue,
        "total_cost":         total_cost,
        "net_profit":         net_profit,
        "mudarabah_pool":     mudarabah_pool,       # 70%
        "bank_share":         bank_share,            # 30%
        "statutory_reserve":  statutory_reserve,     # 20% of bank share
        "retained_earnings":  retained_earnings,     # 80% of bank share
        "total_shariah_bal":  round(total_shariah_balance, 2),
        "distributions":      distributions,
    }


# ---------------------------------------------------------------------------
# MAIN APPLICATION
# ---------------------------------------------------------------------------
class BankManagerApp:
    BUTTON_FONT = ("Segoe UI", 11)
    LABEL_FONT  = ("Segoe UI", 10)
    TITLE_FONT  = ("Segoe UI", 18, "bold")
    BG          = "#f0f2f5"
    ACCENT      = "#2c3e50"
    SUCCESS     = "#27ae60"
    DANGER      = "#e74c3c"
    INFO        = "#2980b9"
    ISLAMIC     = "#1a7a4a"   # green accent for Mudarabah UI

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Management System")
        self.root.config(bg=self.BG)
        self.root.resizable(False, False)
        _center_window(self.root, 460, 590)

        tk.Label(root, text="🏛  BANK MANAGEMENT SYSTEM",
                 font=self.TITLE_FONT, bg=self.BG, fg=self.ACCENT).pack(pady=(28, 4))
        tk.Label(root, text="Real-Time Automated Banking System",
                 font=("Segoe UI", 9), bg=self.BG, fg="#7f8c8d").pack(pady=(0, 20))

        menu_items = [
            ("➕  Open New Account",       self.open_add_account),
            ("💰  Deposit Money",           self.open_deposit),
            ("💸  Withdraw Money",          self.open_withdraw),
            ("📊  Balance & Statement",     self.open_check_balance),
            ("🏦  Loan Management",         self.open_loan_manager),
            ("🕌  Mudarabah Distribution",  self.open_mudarabah_panel),
            ("🔐  Admin Portal",            self.open_admin_portal),
        ]
        for label, cmd in menu_items:
            color = self.ISLAMIC if "Mudarabah" in label else self.ACCENT
            self._menu_btn(label, cmd, color)

        tk.Button(root, text="✖  Exit System", command=root.destroy,
                  bg=self.DANGER, fg="white",
                  font=(self.BUTTON_FONT[0], 11, "bold"),
                  bd=0, padx=6, pady=8, cursor="hand2",
                  relief="flat").pack(pady=(20, 0), fill="x", padx=60)

    # ── shared UI helpers ────────────────────────────────────────────────────
    def _menu_btn(self, text: str, command, color=None) -> None:
        tk.Button(self.root, text=text, command=command,
                  bg=color or self.ACCENT, fg="white",
                  font=self.BUTTON_FONT, bd=0, padx=6, pady=8,
                  cursor="hand2", relief="flat",
                  activebackground="#34495e",
                  activeforeground="white").pack(pady=4, fill="x", padx=60)

    def _lbl(self, parent, text):
        tk.Label(parent, text=text, font=self.LABEL_FONT,
                 bg="white").pack(pady=(6, 0))

    def _entry(self, parent, show=None):
        e = tk.Entry(parent, font=("Consolas", 11),
                     bd=1, relief="solid", show=show or "")
        e.pack(pady=(2, 0), padx=30, fill="x")
        return e

    def _action_btn(self, parent, text, command, color=None):
        tk.Button(parent, text=text, command=command,
                  bg=color or self.SUCCESS, fg="white",
                  font=(self.BUTTON_FONT[0], 10, "bold"),
                  bd=0, pady=7, cursor="hand2",
                  relief="flat").pack(pady=(14, 4), padx=30, fill="x")

    # ── FEATURE 1: ADD ACCOUNT ───────────────────────────────────────────────
    def open_add_account(self):
        win = _make_toplevel(self.root, "Open New Account", 380, 490)
        win.config(bg="white")

        tk.Label(win, text="Open New Account",
                 font=("Segoe UI", 13, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(16, 8))

        self._lbl(win, "Full Name")
        ent_name = self._entry(win)
        self._lbl(win, "Mobile Number  (e.g. 01XXXXXXXXX)")
        ent_mobile = self._entry(win)
        self._lbl(win, "NID / Passport Number")
        ent_nid = self._entry(win)
        self._lbl(win, f"Initial Deposit (BDT, min {MIN_INITIAL_DEPOSIT:.0f})")
        ent_dep = self._entry(win)
        self._lbl(win, "Account Type")
        cmb_type = ttk.Combobox(
            win,
            values=["Conventional (Interest-Based)",
                    "Shariah-Based (Mudarabah)"],
            state="readonly", font=self.LABEL_FONT
        )
        cmb_type.current(0)
        cmb_type.pack(pady=(2, 0), padx=30, fill="x")

        def save_account():
            name   = ent_name.get().strip()
            mobile = ent_mobile.get().strip()
            nid    = ent_nid.get().strip().upper()
            mode   = ("Conventional" if "Conventional" in cmb_type.get()
                      else "Shariah")

            if not name or len(name) < 3:
                messagebox.showerror("Validation Error",
                    "Full name must be at least 3 characters.", parent=win)
                return
            if not _validate_mobile(mobile):
                messagebox.showerror("Validation Error",
                    "Mobile must be a valid Bangladeshi number (01XXXXXXXXX).",
                    parent=win)
                return
            if not _validate_nid(nid):
                messagebox.showerror("Validation Error",
                    "NID must be 10 or 17 digits, or a valid passport number.",
                    parent=win)
                return
            ok, dep_amount = _safe_float(ent_dep.get())
            if not ok or dep_amount < MIN_INITIAL_DEPOSIT:
                messagebox.showerror("Validation Error",
                    f"Initial deposit must be at least "
                    f"{MIN_INITIAL_DEPOSIT:.0f} BDT.", parent=win)
                return

            try:
                with sqlite3.connect(DB_NAME) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT account_no FROM accounts WHERE nid = ?", (nid,))
                    existing = cur.fetchone()
                    if existing:
                        messagebox.showerror("Duplicate NID",
                            f"An account with this NID already exists.\n"
                            f"Account No: {existing[0]}", parent=win)
                        return

                    acc_no = _generate_account_no()
                    cur.execute(
                        "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)",
                        (acc_no, name, mobile, nid, mode,
                         dep_amount, 0.0, _now_str()))
                    conn.commit()

                log_transaction(acc_no, "Account Opening Deposit", dep_amount)
                messagebox.showinfo("Account Created",
                    f"✅ Account opened successfully!\n\n"
                    f"Account No : {acc_no}\n"
                    f"Name       : {name}\n"
                    f"Type       : {mode}\n"
                    f"Balance    : {dep_amount:.2f} BDT", parent=win)
                win.destroy()

            except sqlite3.IntegrityError:
                messagebox.showerror("Database Error",
                    "A database integrity error occurred.", parent=win)
            except Exception as e:
                messagebox.showerror("Unexpected Error", str(e), parent=win)

        self._action_btn(win, "✅  Create Account", save_account, self.SUCCESS)

    # ── FEATURE 2: DEPOSIT ───────────────────────────────────────────────────
    def open_deposit(self):
        win = _make_toplevel(self.root, "Deposit Money", 360, 240)
        win.config(bg="white")
        tk.Label(win, text="Deposit Money", font=("Segoe UI", 13, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(16, 8))
        self._lbl(win, "Account Number")
        ent_user = self._entry(win)
        self._lbl(win, "Deposit Amount (BDT)")
        ent_amount = self._entry(win)

        def proceed():
            user = ent_user.get().strip()
            if not user:
                messagebox.showerror("Error",
                    "Please enter an account number.", parent=win)
                return
            acc = apply_accrued_interest(user)
            if acc is None:
                messagebox.showerror("Error", "Account not found.", parent=win)
                return
            ok, amount = _safe_float(ent_amount.get())
            if not ok or amount <= 0:
                messagebox.showerror("Error",
                    "Enter a valid positive amount.", parent=win)
                return
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute(
                        "UPDATE accounts SET balance = balance + ? "
                        "WHERE account_no = ?", (amount, user))
                    conn.commit()
                log_transaction(user, "Deposit", amount)
                messagebox.showinfo("Success",
                    f"✅ Deposited {amount:.2f} BDT\n"
                    f"New Balance : {acc['balance'] + amount:.2f} BDT",
                    parent=win)
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        self._action_btn(win, "💰  Deposit", proceed, self.INFO)

    # ── FEATURE 3: WITHDRAW ──────────────────────────────────────────────────
    def open_withdraw(self):
        win = _make_toplevel(self.root, "Withdraw Money", 360, 260)
        win.config(bg="white")
        tk.Label(win, text="Withdraw Money", font=("Segoe UI", 13, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(16, 8))
        self._lbl(win, "Account Number")
        ent_user = self._entry(win)
        self._lbl(win, "Withdrawal Amount (BDT)")
        ent_amount = self._entry(win)

        def proceed():
            user = ent_user.get().strip()
            if not user:
                messagebox.showerror("Error",
                    "Please enter an account number.", parent=win)
                return
            acc = apply_accrued_interest(user)
            if acc is None:
                messagebox.showerror("Error", "Account not found.", parent=win)
                return
            ok, amount = _safe_float(ent_amount.get())
            if not ok or amount <= 0:
                messagebox.showerror("Error",
                    "Enter a valid positive amount.", parent=win)
                return
            available = acc["balance"] - MIN_BALANCE_RESERVE
            if amount > available:
                messagebox.showerror("Insufficient Balance",
                    f"Available for withdrawal: {available:.2f} BDT\n"
                    f"(A reserve of {MIN_BALANCE_RESERVE:.0f} BDT must be "
                    f"maintained)", parent=win)
                return
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute(
                        "UPDATE accounts SET balance = balance - ? "
                        "WHERE account_no = ?", (amount, user))
                    conn.commit()
                log_transaction(user, "Withdrawal", amount)
                messagebox.showinfo("Success",
                    f"✅ Withdrawn {amount:.2f} BDT\n"
                    f"Remaining Balance : {acc['balance'] - amount:.2f} BDT",
                    parent=win)
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        self._action_btn(win, "💸  Withdraw", proceed, "#e67e22")

    # ── FEATURE 4: CHECK BALANCE & STATEMENT ─────────────────────────────────
    def open_check_balance(self):
        win = _make_toplevel(self.root, "Account Dashboard", 520, 500)
        win.config(bg="white")
        tk.Label(win, text="Account Dashboard",
                 font=("Segoe UI", 13, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(16, 4))

        frm = tk.Frame(win, bg="white")
        frm.pack(fill="x", padx=30, pady=4)
        tk.Label(frm, text="Account Number:",
                 font=self.LABEL_FONT, bg="white").pack(side="left")
        ent_user = tk.Entry(frm, font=("Consolas", 11),
                            bd=1, relief="solid", width=16)
        ent_user.pack(side="left", padx=(6, 0))
        tk.Button(frm, text="Fetch", command=lambda: fetch_details(),
                  bg=self.ACCENT, fg="white",
                  font=(self.BUTTON_FONT[0], 9, "bold"),
                  bd=0, padx=8, pady=3, cursor="hand2").pack(
            side="left", padx=(6, 0))

        txt_frame = tk.Frame(win, bg="white")
        txt_frame.pack(fill="both", expand=True, padx=20, pady=10)
        sb = tk.Scrollbar(txt_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(txt_frame, font=("Consolas", 9),
                      yscrollcommand=sb.set, bd=1, relief="solid",
                      wrap="none", state="disabled")
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        def fetch_details():
            user = ent_user.get().strip()
            if not user:
                messagebox.showerror("Error",
                    "Enter an account number.", parent=win)
                return
            acc = apply_accrued_interest(user)
            if acc is None:
                messagebox.showerror("Error",
                    "Account not found.", parent=win)
                return

            lines = [
                "=" * 57,
                "  ACCOUNT STATEMENT — NEXUS BANK PLC",
                "=" * 57,
                f"  Account No   : {acc['account_no']}",
                f"  Account Name : {acc['name']}",
                f"  Mobile       : {acc['mobile']}",
                f"  NID          : {acc['nid']}",
                f"  Type         : {acc['account_type']}",
                f"  Balance      : {acc['balance']:.4f} BDT",
                f"  Loan O/S     : {acc['loan']:.4f} BDT",
                f"  Last Sync    : {acc['last_update']}",
                "=" * 57,
                "  RECENT TRANSACTIONS (Latest 15)",
                "-" * 57,
            ]
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT type, amount, timestamp FROM transactions "
                        "WHERE account_no = ? ORDER BY id DESC LIMIT 15",
                        (user,))
                    rows = cur.fetchall()
            except Exception as e:
                messagebox.showerror("DB Error", str(e), parent=win)
                return

            for r in rows:
                lines.append(
                    f"  [{r[2]}]  {r[0]:<42}  {r[1]:>10.2f} BDT")
            if not rows:
                lines.append("  No transactions found.")
            lines.append("=" * 57)

            txt.config(state="normal")
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, "\n".join(lines))
            txt.config(state="disabled")

    # ── FEATURE 5: LOAN MANAGEMENT ───────────────────────────────────────────
    def open_loan_manager(self):
        win = _make_toplevel(self.root, "Loan Management", 380, 310)
        win.config(bg="white")
        tk.Label(win, text="Loan Management",
                 font=("Segoe UI", 13, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(16, 8))
        self._lbl(win, "Account Number")
        ent_user = self._entry(win)
        self._lbl(win, "Amount (BDT)")
        ent_amount = self._entry(win)

        def apply_loan():
            user = ent_user.get().strip()
            if not user:
                messagebox.showerror("Error",
                    "Enter an account number.", parent=win)
                return
            acc = apply_accrued_interest(user)
            if acc is None:
                messagebox.showerror("Error",
                    "Account not found.", parent=win)
                return
            if acc["account_type"] == "Shariah":
                messagebox.showerror("Shariah Restriction",
                    "Interest-bearing loans are not permitted for Shariah "
                    "accounts.\nPlease consult our Islamic Banking desk for "
                    "Murabaha financing.", parent=win)
                return
            ok, amount = _safe_float(ent_amount.get())
            if not ok or amount <= 0:
                messagebox.showerror("Error",
                    "Enter a valid positive amount.", parent=win)
                return
            max_loan   = acc["balance"] * MAX_LOAN_MULTIPLIER
            total_loan = acc["loan"] + amount
            if total_loan > max_loan:
                max_new = max(0, max_loan - acc["loan"])
                messagebox.showerror("Loan Limit Exceeded",
                    f"Maximum eligible loan: {max_loan:.2f} BDT "
                    f"(= {MAX_LOAN_MULTIPLIER:.0f}× your balance).\n"
                    f"Current outstanding  : {acc['loan']:.2f} BDT\n"
                    f"Additional eligible  : {max_new:.2f} BDT",
                    parent=win)
                return
            if not messagebox.askyesno("Confirm Loan",
                    f"Sanction {amount:.2f} BDT loan?\n\n"
                    f"Funds will be credited to your account.\n"
                    f"Annual interest rate: "
                    f"{ANNUAL_LOAN_RATE*100:.0f}%", parent=win):
                return
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute(
                        "UPDATE accounts "
                        "SET loan = loan + ?, balance = balance + ? "
                        "WHERE account_no = ?",
                        (amount, amount, user))
                    conn.commit()
                log_transaction(user, "Loan Disbursement", amount)
                messagebox.showinfo("Loan Sanctioned",
                    f"✅ Loan of {amount:.2f} BDT sanctioned.\n"
                    f"Interest accrues at "
                    f"{ANNUAL_LOAN_RATE*100:.0f}% p.a.", parent=win)
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        def pay_loan():
            user = ent_user.get().strip()
            if not user:
                messagebox.showerror("Error",
                    "Enter an account number.", parent=win)
                return
            acc = apply_accrued_interest(user)
            if acc is None:
                messagebox.showerror("Error",
                    "Account not found.", parent=win)
                return
            if acc["loan"] <= 0:
                messagebox.showinfo("No Loan",
                    "This account has no outstanding loan.", parent=win)
                return
            ok, amount = _safe_float(ent_amount.get())
            if not ok or amount <= 0:
                messagebox.showerror("Error",
                    "Enter a valid positive amount.", parent=win)
                return
            if amount > acc["loan"]:
                messagebox.showerror("Over-Payment",
                    f"Repayment ({amount:.2f}) exceeds outstanding loan "
                    f"({acc['loan']:.2f} BDT).", parent=win)
                return
            available = acc["balance"] - MIN_BALANCE_RESERVE
            if amount > available:
                messagebox.showerror("Insufficient Balance",
                    f"Available for repayment: {available:.2f} BDT "
                    f"(after {MIN_BALANCE_RESERVE:.0f} BDT reserve).",
                    parent=win)
                return
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute(
                        "UPDATE accounts "
                        "SET loan = loan - ?, balance = balance - ? "
                        "WHERE account_no = ?",
                        (amount, amount, user))
                    conn.commit()
                log_transaction(user, "Loan Repayment", amount)
                messagebox.showinfo("Repayment Successful",
                    f"✅ {amount:.2f} BDT repaid.\n"
                    f"Remaining loan: {acc['loan'] - amount:.2f} BDT",
                    parent=win)
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        btn_frame = tk.Frame(win, bg="white")
        btn_frame.pack(pady=12, padx=30, fill="x")
        tk.Button(btn_frame, text="🏦  Take Loan (Conventional)",
                  command=apply_loan, bg="#8e44ad", fg="white",
                  font=(self.BUTTON_FONT[0], 10, "bold"),
                  bd=0, pady=8, cursor="hand2",
                  relief="flat").pack(fill="x", pady=3)
        tk.Button(btn_frame, text="✅  Repay Loan From Balance",
                  command=pay_loan, bg=self.SUCCESS, fg="white",
                  font=(self.BUTTON_FONT[0], 10, "bold"),
                  bd=0, pady=8, cursor="hand2",
                  relief="flat").pack(fill="x", pady=3)

    # ── FEATURE 6: MUDARABAH PANEL ───────────────────────────────────────────
    def open_mudarabah_panel(self):
        """
        Dedicated Mudarabah panel — shows the full bank P&L, the profit
        split breakdown, and lets the admin trigger distribution with one click.
        Accessible from the main menu (not password-gated here; integrate
        behind admin auth if required for your deployment).
        """
        win = _make_toplevel(self.root, "Mudarabah Profit Distribution", 580, 600)
        win.config(bg="white")
        win.resizable(True, True)

        tk.Label(win, text="🕌  Mudarabah Profit Distribution",
                 font=("Segoe UI", 14, "bold"),
                 bg="white", fg=self.ISLAMIC).pack(pady=(16, 4))
        tk.Label(win,
                 text="Bank Net Profit  =  Loan Interest Revenue  −  Deposit Interest Cost\n"
                      "Customer Share (70%)  distributed pro-rata to Shariah accounts\n"
                      "Bank's Share (30%)  →  20% Statutory Reserve  +  80% Retained Earnings",
                 font=("Segoe UI", 8), bg="white", fg="#555",
                 justify="center").pack(pady=(0, 8))

        # ── scrollable result area ────────────────────────────────────────────
        txt_frame = tk.Frame(win, bg="white")
        txt_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        sb = tk.Scrollbar(txt_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(txt_frame, font=("Consolas", 9),
                      yscrollcommand=sb.set, bd=1, relief="solid",
                      wrap="none", state="disabled", bg="#fafafa")
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        def preview():
            """Show P&L and projected distribution WITHOUT committing to DB."""
            total_revenue, total_cost, net_profit = calculate_bank_total_profit()

            lines = [
                "=" * 60,
                "  BANK P&L PREVIEW (read-only — no distribution yet)",
                "=" * 60,
                f"  Total Revenue  (Loan Interest Earned)  : "
                f"{total_revenue:>12,.2f} BDT",
                f"  Total Cost     (Deposit Interest Paid) : "
                f"{total_cost:>12,.2f} BDT",
                "-" * 60,
                f"  Bank Net Profit                        : "
                f"{net_profit:>12,.2f} BDT",
                "=" * 60,
            ]

            if net_profit <= 0:
                lines.append(
                    "  ⚠  Bank is at a net loss or break-even.")
                lines.append(
                    "     No Mudarabah profit available to distribute.")
            else:
                mudarabah_pool    = net_profit * MUDARABAH_CUSTOMER_SHARE
                bank_share        = net_profit * MUDARABAH_BANK_SHARE
                statutory_reserve = bank_share  * STATUTORY_RESERVE_RATE
                retained_earnings = bank_share  * RETAINED_EARNINGS_RATE

                lines += [
                    "",
                    "  PROJECTED SPLIT:",
                    f"  Mudarabah Pool  (70% of net profit) : "
                    f"{mudarabah_pool:>12,.2f} BDT",
                    f"  Bank's Share    (30% of net profit) : "
                    f"{bank_share:>12,.2f} BDT",
                    f"    └─ Statutory Reserve (20% of 30%) : "
                    f"{statutory_reserve:>12,.2f} BDT",
                    f"    └─ Retained Earnings (80% of 30%) : "
                    f"{retained_earnings:>12,.2f} BDT",
                    "=" * 60,
                ]

                # Show per-account projection
                with sqlite3.connect(DB_NAME) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT account_no, name, balance
                        FROM accounts
                        WHERE account_type = 'Shariah' AND balance > 0
                    """)
                    shariah_accounts = cur.fetchall()

                if shariah_accounts:
                    total_shariah = sum(r[2] for r in shariah_accounts)
                    lines.append(
                        f"  Total Shariah Deposits : {total_shariah:>12,.2f} BDT")
                    lines.append(
                        f"  {'Account No':<12}  {'Name':<20}  "
                        f"{'Balance':>10}  {'Share%':>7}  {'Payout':>10}")
                    lines.append("  " + "-" * 58)
                    for acc_no, name, bal in shariah_accounts:
                        ratio   = bal / total_shariah
                        payout  = round(mudarabah_pool * ratio, 2)
                        lines.append(
                            f"  {acc_no:<12}  {name:<20}  "
                            f"{bal:>10,.2f}  {ratio*100:>6.2f}%  "
                            f"{payout:>10,.2f}")
                else:
                    lines.append(
                        "  No active Shariah accounts found.")

            lines.append("=" * 60)

            txt.config(state="normal")
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, "\n".join(lines))
            txt.config(state="disabled")

        def distribute():
            """Run the full distribution and commit to DB."""
            _, _, net_profit = calculate_bank_total_profit()
            if net_profit <= 0:
                messagebox.showwarning("No Profit",
                    "Bank net profit is zero or negative.\n"
                    "Nothing to distribute.", parent=win)
                return

            if not messagebox.askyesno("Confirm Distribution",
                    f"Distribute Mudarabah profit of "
                    f"{net_profit * MUDARABAH_CUSTOMER_SHARE:,.2f} BDT "
                    f"(70% of {net_profit:,.2f} BDT net profit) "
                    f"to all active Shariah accounts?\n\n"
                    f"This will credit each account and log a transaction.",
                    parent=win):
                return

            result = calculate_and_distribute_mudarabah()

            if result["status"] == "no_profit":
                messagebox.showinfo("No Profit", result["message"], parent=win)
                return
            if result["status"] == "no_shariah_accounts":
                messagebox.showinfo("No Shariah Accounts",
                    result["message"], parent=win)
                return

            # Build result report
            d = result
            lines = [
                "=" * 60,
                "  ✅  MUDARABAH DISTRIBUTION COMPLETED",
                "=" * 60,
                f"  Total Revenue  (Loan Interest)         : "
                f"{d['total_revenue']:>12,.2f} BDT",
                f"  Total Cost     (Deposit Interest)      : "
                f"{d['total_cost']:>12,.2f} BDT",
                f"  Bank Net Profit                        : "
                f"{d['net_profit']:>12,.2f} BDT",
                "-" * 60,
                f"  Mudarabah Pool  (70%)                  : "
                f"{d['mudarabah_pool']:>12,.2f} BDT",
                f"  Bank's Share    (30%)                  : "
                f"{d['bank_share']:>12,.2f} BDT",
                f"    └─ Statutory Reserve (20% of 30%)    : "
                f"{d['statutory_reserve']:>12,.2f} BDT",
                f"    └─ Retained Earnings (80% of 30%)    : "
                f"{d['retained_earnings']:>12,.2f} BDT",
                "=" * 60,
                f"  Total Shariah Deposits : "
                f"{d['total_shariah_bal']:>12,.2f} BDT",
                "",
                f"  {'Account No':<12}  {'Name':<20}  "
                f"{'Balance':>10}  {'Share%':>7}  {'Credited':>10}",
                "  " + "-" * 58,
            ]
            for dist in d["distributions"]:
                lines.append(
                    f"  {dist['account_no']:<12}  {dist['name']:<20}  "
                    f"{dist['balance']:>10,.2f}  "
                    f"{dist['share_ratio']:>6.2f}%  "
                    f"{dist['payout']:>10,.2f}")
            lines.append("=" * 60)

            txt.config(state="normal")
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, "\n".join(lines))
            txt.config(state="disabled")

            messagebox.showinfo("Distribution Complete",
                f"✅ Mudarabah profit distributed successfully!\n\n"
                f"  Net Profit       : {d['net_profit']:,.2f} BDT\n"
                f"  Customer Pool    : {d['mudarabah_pool']:,.2f} BDT\n"
                f"  Accounts Paid    : {len(d['distributions'])}\n"
                f"  Bank Retained    : {d['bank_share']:,.2f} BDT",
                parent=win)

        # ── button row ────────────────────────────────────────────────────────
        btn_row = tk.Frame(win, bg="white")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))

        tk.Button(btn_row, text="🔍  Preview P&L (no commit)",
                  command=preview,
                  bg=self.INFO, fg="white",
                  font=(self.BUTTON_FONT[0], 10, "bold"),
                  bd=0, pady=7, cursor="hand2",
                  relief="flat").pack(side="left", fill="x",
                                      expand=True, padx=(0, 6))

        tk.Button(btn_row, text="🕌  Distribute Mudarabah Profit",
                  command=distribute,
                  bg=self.ISLAMIC, fg="white",
                  font=(self.BUTTON_FONT[0], 10, "bold"),
                  bd=0, pady=7, cursor="hand2",
                  relief="flat").pack(side="left", fill="x", expand=True)

        # Auto-load preview on open
        preview()

    # ── FEATURE 7: ADMIN PORTAL ──────────────────────────────────────────────
    def open_admin_portal(self):
        win = _make_toplevel(self.root, "Admin Authentication", 320, 180)
        win.config(bg="white")
        tk.Label(win, text="Admin Authentication",
                 font=("Segoe UI", 13, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(18, 8))
        self._lbl(win, "Enter Admin Password")
        ent_pass = self._entry(win, show="●")

        def auth():
            if _hash_password(ent_pass.get()) == _ADMIN_PASSWORD_HASH:
                win.destroy()
                self._show_admin_dashboard()
            else:
                messagebox.showerror("Access Denied",
                    "Incorrect admin password.", parent=win)
                ent_pass.delete(0, tk.END)

        ent_pass.bind("<Return>", lambda _: auth())
        self._action_btn(win, "🔐  Login as Admin", auth, self.ACCENT)

    def _show_admin_dashboard(self):
        dash = _make_toplevel(self.root, "Admin Control Panel", 880, 480)
        dash.config(bg="white")
        dash.resizable(True, True)

        tk.Label(dash, text="👥  All Registered Accounts — Live View",
                 font=("Segoe UI", 12, "bold"),
                 bg="white", fg=self.ACCENT).pack(pady=(14, 4))

        stats_frame = tk.Frame(dash, bg="#ecf0f1")
        stats_frame.pack(fill="x", padx=14, pady=(0, 6))

        cols       = ("account_no", "name", "mobile", "nid",
                      "type", "balance", "loan", "last_update")
        col_labels = ("Acc No", "Name", "Mobile", "NID",
                      "Type", "Balance (BDT)", "Loan (BDT)", "Last Updated")
        col_widths = (105, 130, 105, 120, 95, 105, 105, 145)

        tree_frame = tk.Frame(dash)
        tree_frame.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                            yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=tree.yview)
        hsb.config(command=tree.xview)
        tree.pack(fill="both", expand=True)

        for col, label, width in zip(cols, col_labels, col_widths):
            tree.heading(col, text=label,
                         command=lambda c=col: self._sort_treeview(
                             tree, c, False))
            tree.column(col, width=width, anchor="center")

        tree.tag_configure("odd",  background="#f9f9f9")
        tree.tag_configure("even", background="white")

        def refresh():
            for item in tree.get_children():
                tree.delete(item)
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT account_no FROM accounts ORDER BY name")
                    account_nos = [r[0] for r in cur.fetchall()]
            except Exception as e:
                messagebox.showerror("DB Error", str(e), parent=dash)
                return

            total_balance = 0.0
            total_loan    = 0.0
            for idx, acc_no in enumerate(account_nos):
                acc = apply_accrued_interest(acc_no)
                if acc is None:
                    continue
                tag = "odd" if idx % 2 else "even"
                tree.insert("", tk.END, values=(
                    acc["account_no"], acc["name"], acc["mobile"],
                    acc["nid"], acc["account_type"],
                    f"{acc['balance']:.2f}", f"{acc['loan']:.2f}",
                    acc["last_update"]
                ), tags=(tag,))
                total_balance += acc["balance"]
                total_loan    += acc["loan"]

            # Stats bar
            for w in stats_frame.winfo_children():
                w.destroy()
            total_revenue, total_cost, net_profit = calculate_bank_total_profit()
            stats = [
                ("Total Accounts",    str(len(account_nos))),
                ("Total Deposits",    f"{total_balance:,.2f} BDT"),
                ("Total Loans O/S",   f"{total_loan:,.2f} BDT"),
                ("Bank Net Profit",   f"{net_profit:,.2f} BDT"),
            ]
            for s_label, s_val in stats:
                f = tk.Frame(stats_frame, bg="#ecf0f1")
                f.pack(side="left", padx=16, pady=6)
                tk.Label(f, text=s_label, font=("Segoe UI", 8),
                         bg="#ecf0f1", fg="#7f8c8d").pack()
                color = (self.ISLAMIC if net_profit > 0
                         else self.DANGER) if "Profit" in s_label else self.ACCENT
                tk.Label(f, text=s_val,
                         font=("Segoe UI", 11, "bold"),
                         bg="#ecf0f1", fg=color).pack()

        ctrl_frame = tk.Frame(dash, bg="white")
        ctrl_frame.pack(fill="x", padx=14, pady=(0, 10))
        tk.Button(ctrl_frame, text="🔄  Refresh Data", command=refresh,
                  bg=self.INFO, fg="white",
                  font=(self.BUTTON_FONT[0], 9, "bold"),
                  bd=0, padx=10, pady=5, cursor="hand2").pack(side="left")

        refresh()

    @staticmethod
    def _sort_treeview(tree: ttk.Treeview, col: str, reverse: bool) -> None:
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            data.sort(key=lambda x: float(x[0].replace(",", "")),
                      reverse=reverse)
        except ValueError:
            data.sort(reverse=reverse)
        for index, (_, k) in enumerate(data):
            tree.move(k, "", index)
        tree.heading(col,
                     command=lambda: BankManagerApp._sort_treeview(
                         tree, col, not reverse))


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    app  = BankManagerApp(root)
    root.mainloop()