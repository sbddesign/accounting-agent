"""Accounting tools exposed to the agent.

Each public method on `AccountingTools` is a tool the model can call. They
speak plain accounting language (net worth, debt, spending, bitcoin) and hide
hledger completely. Every tool returns a short, readable string so a small
model can quote it directly.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from .ledger import BASE_COMMODITY, Ledger, LedgerError, fmt_amount, parse_amounts

BTC = "BTC"


def _usd(x: float) -> str:
    return fmt_amount(x, BASE_COMMODITY)


class AccountingTools:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    # ---------------------------------------------------------------- helpers
    def _period(self, period: str | None) -> str | None:
        return self.ledger.resolve_period(period)

    def _period_label(self, period: str | None) -> str:
        p = self._period(period)
        return f" for {period.strip()} ({p})" if p else " (all time)"

    def _safe(self, fn):
        try:
            return fn()
        except LedgerError as e:
            return f"ERROR: {e}"
        except (ValueError, TypeError) as e:
            return f"ERROR: bad argument: {e}"

    # ------------------------------------------------------------------ tools
    def list_accounts(self) -> str:
        """List every account in the ledger with its current balance. Use this first when you are unsure which accounts exist or how they are named."""

        def go():
            rows = self.ledger.balances("", in_base=False)
            if not rows:
                return "The ledger is empty: no accounts or transactions yet."
            lines = []
            for acct, amts in rows:
                bal = ", ".join(str(a) for a in amts) if amts else "$0.00"
                lines.append(f"{acct}: {bal}")
            return "\n".join(lines)

        return self._safe(go)

    def account_balance(self, account: str) -> str:
        """Current balance of one account (and its sub-accounts). `account` may be a full name like 'assets:bank:checking' or a partial name like 'checking' or 'savings'."""

        def go():
            rows = self.ledger.balances(account, in_base=False)
            if not rows:
                return f"No account matching '{account}'. Call list_accounts to see the available accounts."
            lines = [f"{a}: {', '.join(str(x) for x in amts) if amts else '$0.00'}" for a, amts in rows]
            if len(rows) > 1:
                total = self.ledger.total(account, in_base=True)
                lines.append(f"TOTAL (in USD): {_usd(total)}")
            return "\n".join(lines)

        return self._safe(go)

    def net_worth(self) -> str:
        """Total net worth (also called equity): all assets minus all liabilities, valued in USD at the latest known prices. Also shows the assets and liabilities breakdown. Use for questions about net worth, equity, or 'how much am I worth'."""

        def go():
            assets = self.ledger.balances("assets", in_base=True)
            liabs = self.ledger.balances("liabilities", in_base=True)
            a_total = round(sum(x.quantity for _, am in assets for x in am), 2)
            l_total = round(-sum(x.quantity for _, am in liabs for x in am), 2)
            lines = ["ASSETS (USD value):"]
            for acct, am in assets:
                lines.append(f"  {acct}: {_usd(sum(x.quantity for x in am))}")
            lines.append(f"  Total assets: {_usd(a_total)}")
            lines.append("LIABILITIES (what is owed):")
            for acct, am in liabs:
                lines.append(f"  {acct}: {_usd(-sum(x.quantity for x in am))}")
            lines.append(f"  Total liabilities: {_usd(l_total)}")
            lines.append(f"NET WORTH (assets - liabilities): {_usd(round(a_total - l_total, 2))}")
            btc = self.ledger.commodity_total("assets", BTC)
            if btc:
                lines.append(f"(includes {btc:.8f} BTC valued at {_usd(self.ledger.latest_price(BTC) or 0)} per BTC)")
            return "\n".join(lines)

        return self._safe(go)

    def total_debt(self) -> str:
        """Total debt: every liability (credit cards, auto loans, mortgages, other loans) with the amount owed on each, plus the grand total. Use for questions about debt, loans, what I owe, or credit card balances."""

        def go():
            liabs = self.ledger.balances("liabilities", in_base=True)
            if not liabs:
                return "No liabilities recorded. Total debt: $0.00"
            lines = []
            total = 0.0
            for acct, am in liabs:
                owed = -sum(x.quantity for x in am)
                total += owed
                lines.append(f"{acct}: {_usd(owed)} owed")
            lines.append(f"TOTAL DEBT: {_usd(round(total, 2))}")
            return "\n".join(lines)

        return self._safe(go)

    def bitcoin_holdings(self) -> str:
        """How much Bitcoin (BTC) is held, in which accounts, and its current USD value at the latest known price."""

        def go():
            rows = self.ledger.balances("assets", in_base=False)
            btc_rows = [(a, x.quantity) for a, am in rows for x in am if x.commodity == BTC]
            if not btc_rows:
                return "No Bitcoin holdings found in the ledger."
            total = sum(q for _, q in btc_rows)
            price = self.ledger.latest_price(BTC)
            lines = [f"{a}: {q:.8f} BTC" for a, q in btc_rows]
            lines.append(f"TOTAL BITCOIN: {total:.8f} BTC")
            if price:
                lines.append(f"Latest price: {_usd(price)} per BTC -> USD value {_usd(round(total * price, 2))}")
            return "\n".join(lines)

        return self._safe(go)

    def spending_by_category(self, period: str = "last month", category: str = "") -> str:
        """Total spending per expense category for a time period, largest first, with percentages and the overall total. `period` examples: 'last month', 'this month', 'this year', 'last 3 months', 'july', 'july 2026', '2026-07', 'all'. Optional `category` (e.g. 'interest', 'utilities', 'groceries') drills into that category's sub-categories instead. Use for questions like 'what did I spend the most on', 'how much did I spend on X'."""

        def go():
            p = self._period(period)
            cat = category.strip().strip(":").lower()
            if cat:
                query = cat if cat.startswith("expenses") else f"expenses:{cat}"
                rows = self.ledger.balances(query, period=p, in_base=True, depth=3)
                if not rows:
                    return f"No expenses in category '{category}'{self._period_label(period)}. Call spending_by_category without a category to see the available ones."
                title = f"Spending in '{cat}'{self._period_label(period)}:"
            else:
                rows = self.ledger.balances("expenses", period=p, in_base=True, depth=2)
                if not rows:
                    return f"No expenses found{self._period_label(period)}."
                title = f"Spending by category{self._period_label(period)}:"
            items = sorted(((a.split(":", 1)[-1], sum(x.quantity for x in am)) for a, am in rows), key=lambda t: -t[1])
            total = sum(v for _, v in items)
            lines = [title]
            for name, v in items:
                pct = (v / total * 100) if total else 0
                lines.append(f"  {name}: {_usd(v)} ({pct:.0f}%)")
            lines.append(f"TOTAL SPENDING: {_usd(round(total, 2))}")
            return "\n".join(lines)

        return self._safe(go)

    def income_summary(self, period: str = "this month") -> str:
        """Total income by source (salary, refunds, etc.) for a time period, plus total expenses and net savings for the same period. `period` works like in spending_by_category."""

        def go():
            p = self._period(period)
            inc = self.ledger.balances("income", period=p, in_base=True, depth=2)
            income_total = round(-sum(x.quantity for _, am in inc for x in am), 2)
            exp_total = round(self.ledger.total("expenses", period=p), 2)
            lines = [f"Income{self._period_label(period)}:"]
            for a, am in inc:
                lines.append(f"  {a.split(':',1)[-1]}: {_usd(-sum(x.quantity for x in am))}")
            lines.append(f"TOTAL INCOME: {_usd(income_total)}")
            lines.append(f"TOTAL EXPENSES: {_usd(exp_total)}")
            lines.append(f"NET (income - expenses): {_usd(round(income_total - exp_total, 2))}")
            return "\n".join(lines)

        return self._safe(go)

    def search_transactions(self, query: str = "", period: str = "all", limit: int = 20) -> str:
        """Find transactions. `query` matches text in the description or account name (e.g. 'Netflix', 'groceries', 'bitcoin'); leave empty for all. `period` like 'last month' or 'all'. Returns date, description, and amounts, newest first."""

        def go():
            p = self._period(period)
            q = query.strip()
            hq = f"desc:{q}" if q else ""
            txns = self.ledger.transactions(hq, period=p)
            if q and not txns:
                txns = self.ledger.transactions(f"acct:{q}", period=p)
            if q and not txns:
                # try each word as description OR account
                txns = [t for t in self.ledger.transactions("", period=p)
                        if q.lower() in t.description.lower()
                        or any(q.lower() in po.account.lower() for po in t.postings)]
            if not txns:
                return f"No transactions found for '{q}'{self._period_label(period)}."
            txns = sorted(txns, key=lambda t: (t.date, t.index), reverse=True)
            n = len(txns)
            txns = txns[: max(1, int(limit))]
            lines = [f"{n} transaction(s) found (showing {len(txns)}):"]
            for t in txns:
                lines.append(_fmt_txn(t))
            all_txns = sorted(self.ledger.transactions(hq, period=p) if q else self.ledger.transactions("", period=p), key=lambda t: t.date) if n > len(txns) else txns
            exp = sum(a.quantity for t in all_txns for po in t.postings if po.account.startswith("expenses") for a in po.amounts if a.commodity == BASE_COMMODITY)
            if exp:
                lines.append(f"TOTAL spent across all {n} matching transaction(s): {_usd(round(exp, 2))}")
            return "\n".join(lines)

        return self._safe(go)

    def recent_transactions(self, count: int = 10) -> str:
        """The most recent transactions in the ledger (newest first)."""

        def go():
            txns = sorted(self.ledger.transactions(""), key=lambda t: (t.date, t.index), reverse=True)
            if not txns:
                return "No transactions in the ledger."
            return "\n".join(_fmt_txn(t) for t in txns[: max(1, int(count))])

        return self._safe(go)

    def find_unusual_transactions(self, period: str = "all") -> str:
        """Flag transactions that look suspicious or unusual: expenses far larger than normal for their category, duplicate charges (same description and amount on the same day), and unfamiliar merchants seen only once. Use for questions about suspicious, weird, fraudulent, or unusual transactions."""

        def go():
            p = self._period(period)
            txns = self.ledger.transactions("expenses", period=p)
            if not txns:
                return "No expense transactions to analyse."
            # gather (txn, category, amount)
            per_cat: dict[str, list[tuple]] = defaultdict(list)
            seen: dict[tuple, list] = defaultdict(list)
            desc_counts: dict[str, int] = defaultdict(int)
            for t in txns:
                for po in t.postings:
                    if po.account.startswith("expenses"):
                        usd = sum(a.quantity for a in po.amounts if a.commodity == BASE_COMMODITY)
                        if usd <= 0:
                            continue
                        cat = ":".join(po.account.split(":")[:2])
                        per_cat[cat].append((t, usd))
                        seen[(t.date, t.description.lower(), round(usd, 2))].append(t)
                desc_counts[t.description.lower()] += 1
            findings = []
            for cat, items in per_cat.items():
                if len(items) < 4:
                    continue
                vals = [v for _, v in items]
                med = statistics.median(vals)
                mad = statistics.median([abs(v - med) for v in vals]) or (statistics.pstdev(vals) or 1.0)
                for t, v in items:
                    if v > med * 4 and (v - med) / mad > 6:
                        sev = "HIGHLY UNUSUAL" if v > med * 15 else "somewhat high"
                        findings.append(
                            f"- [{sev}] {t.date} '{t.description}' {_usd(v)} in {cat.split(':',1)[-1]}: "
                            f"about {v/med:.0f}x the typical {cat.split(':',1)[-1]} charge of {_usd(med)}"
                            + (" (merchant seen only once)" if desc_counts[t.description.lower()] == 1 else "")
                        )
            for (date, desc, amt), ts in seen.items():
                if len(ts) > 1:
                    findings.append(f"- [POSSIBLE DUPLICATE] {date} '{ts[0].description}' {_usd(amt)} appears {len(ts)} times on the same day: possible duplicate charge")
            if not findings:
                return f"No unusual transactions detected{self._period_label(period)}. All expenses look consistent with their categories."
            findings.sort(key=lambda f: 0 if "HIGHLY" in f else (1 if "DUPLICATE" in f else 2))
            return f"Unusual transactions{self._period_label(period)} (most suspicious first):\n" + "\n".join(findings)

        return self._safe(go)

    def monthly_summary(self, period: str = "this year") -> str:
        """Month-by-month table of total income, total expenses and net savings over a period (default: this year). Use for trend questions: which month I spent the most, how spending changed over time, average monthly spending."""

        def go():
            p = self._period(period) or str(self.ledger.today.year)
            rows_e = self.ledger._csv("balance", "expenses", "-M", "-p", p, "--depth", "1", "-N", "-X", BASE_COMMODITY)
            rows_i = self.ledger._csv("balance", "income", "-M", "-p", p, "--depth", "1", "-N", "-X", BASE_COMMODITY)
            if not rows_e and not rows_i:
                return f"No activity{self._period_label(period)}."
            months = [k for k in (rows_e[0] if rows_e else rows_i[0]).keys() if k != "account"]
            def val(rows, m):
                if not rows:
                    return 0.0
                amts = parse_amounts(rows[0].get(m, "0"))
                return sum(a.quantity for a in amts)
            lines = [f"Monthly summary{self._period_label(period)} (month: income / expenses / net):"]
            tot_i = tot_e = 0.0
            shown = 0
            for m in months:
                e = val(rows_e, m)
                i = -val(rows_i, m)
                if e == 0 and i == 0:
                    continue
                shown += 1
                tot_i += i
                tot_e += e
                lines.append(f"  {m}: income {_usd(i)} / expenses {_usd(e)} / net {_usd(round(i - e, 2))}")
            if shown:
                lines.append(f"TOTAL over {shown} month(s): income {_usd(round(tot_i,2))} / expenses {_usd(round(tot_e,2))} / net {_usd(round(tot_i-tot_e,2))}")
                lines.append(f"AVERAGE per month: income {_usd(round(tot_i/shown,2))} / expenses {_usd(round(tot_e/shown,2))}")
                top = max(((m, val(rows_e, m)) for m in months), key=lambda t: t[1])
                lines.append(f"Highest-spending month: {top[0]} ({_usd(top[1])})")
            return "\n".join(lines)

        return self._safe(go)

    def add_transaction(self, date: str, description: str, amount: float, from_account: str, to_account: str) -> str:
        """Record a new transaction. Money moves FROM `from_account` TO `to_account`. `amount` is a positive number in USD (or append ' BTC' as a string like '0.01 BTC' for bitcoin). Examples: a $40 grocery purchase on a credit card -> from_account='liabilities:credit-card:visa', to_account='expenses:groceries'; a paycheck -> from_account='income:salary', to_account='assets:bank:checking'; a transfer -> from checking to savings. `date` is YYYY-MM-DD (use today's date if not specified)."""

        def go():
            amt_text = str(amount).strip()
            if amt_text.upper().endswith("BTC"):
                q = float(amt_text[:-3].strip())
                amt = f"{q:.8f} BTC"
                neg = f"{-q:.8f} BTC"
            else:
                q = float(amt_text.replace("$", "").replace(",", ""))
                amt = f"${q:,.2f}"
                neg = f"$-{q:,.2f}"
            if q <= 0:
                return "ERROR: amount must be a positive number."
            block = self.ledger.append_transaction(
                date.strip(), description, [(to_account.strip(), amt), (from_account.strip(), neg)]
            )
            return f"Recorded:\n{block}"

        return self._safe(go)

    def current_date(self) -> str:
        """Today's date (YYYY-MM-DD)."""
        return self.ledger.today.isoformat()

    # ------------------------------------------------------------ registry
    def all_tools(self) -> list:
        return [
            self.list_accounts,
            self.account_balance,
            self.net_worth,
            self.total_debt,
            self.bitcoin_holdings,
            self.spending_by_category,
            self.income_summary,
            self.search_transactions,
            self.recent_transactions,
            self.find_unusual_transactions,
            self.monthly_summary,
            self.add_transaction,
            self.current_date,
        ]


def _fmt_txn(t) -> str:
    parts = []
    for po in t.postings:
        if po.amounts:
            parts.append(f"{po.account} {', '.join(str(a) for a in po.amounts)}")
        else:
            parts.append(po.account)
    return f"{t.date} {t.description}: " + " | ".join(parts)
