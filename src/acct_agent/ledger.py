"""Thin wrapper around the hledger CLI.

Everything hledger-specific lives here. Tools in `tools.py` build on this and
expose plain-English accounting operations to the model, so the model never
needs to know hledger exists.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_JOURNAL_NAMES = ("main.journal", "ledger.journal", "journal.journal")
BASE_COMMODITY = "$"


class LedgerError(Exception):
    """Raised when hledger fails or the journal is unusable."""


def find_journal(directory: str | os.PathLike | None = None) -> Path:
    """Locate the journal for a directory.

    Order: $LEDGER_FILE, then a well-known name in the directory, then the
    single/first `*.journal` file, else `main.journal` in the directory
    (which will be created on first write).
    """
    env = os.environ.get("LEDGER_FILE")
    if env:
        return Path(env).expanduser().resolve()
    d = Path(directory or os.getcwd()).resolve()
    for name in DEFAULT_JOURNAL_NAMES:
        if (d / name).exists():
            return d / name
    candidates = sorted(d.glob("*.journal"))
    if candidates:
        return candidates[0]
    return d / "main.journal"


@dataclass
class Amount:
    quantity: float
    commodity: str

    def __str__(self) -> str:
        return fmt_amount(self.quantity, self.commodity)


@dataclass
class Posting:
    account: str
    amounts: list[Amount] = field(default_factory=list)


@dataclass
class Transaction:
    index: int
    date: str
    description: str
    postings: list[Posting]

    def to_dict(self) -> dict:
        return {
            "id": self.index,
            "date": self.date,
            "description": self.description,
            "postings": [
                {"account": p.account, "amount": ", ".join(str(a) for a in p.amounts)}
                for p in self.postings
            ],
        }


def fmt_amount(q: float, commodity: str) -> str:
    if commodity == "$":
        sign = "-" if q < 0 else ""
        return f"{sign}${abs(q):,.2f}"
    if commodity.upper() == "BTC":
        return f"{q:.8f} BTC"
    return f"{q:,.2f} {commodity}"


_AMT_RE = re.compile(r"^\s*(-?)\$?\s*(-?[\d,]*\.?\d+)\s*([A-Za-z\"][^\s]*)?\s*$")


def parse_amount(text: str) -> Amount:
    """Parse an hledger-printed amount like '$1,234.50', '-$5.00', '0.5 BTC'."""
    text = text.strip()
    if not text or text == "0":
        return Amount(0.0, BASE_COMMODITY)
    m = _AMT_RE.match(text)
    if not m:
        raise LedgerError(f"cannot parse amount: {text!r}")
    neg, num, comm = m.groups()
    q = float(num.replace(",", ""))
    if neg == "-":
        q = -q
    commodity = comm.strip('"') if comm else "$"
    if "$" not in text and not comm:
        commodity = ""
    return Amount(q, commodity)


def parse_amounts(cell: str) -> list[Amount]:
    """A CSV balance cell may hold several commodities separated by ', '."""
    cell = cell.strip()
    if not cell or cell == "0":
        return []
    return [parse_amount(part) for part in cell.split(", ")]


class Ledger:
    def __init__(self, journal: str | os.PathLike | None = None, today: dt.date | None = None):
        self.journal = Path(journal) if journal else find_journal()
        self.today = today or dt.date.today()
        if shutil.which("hledger") is None:
            raise LedgerError("hledger is not installed or not on PATH")

    # ------------------------------------------------------------------ core
    def run(self, *args: str, allow_missing: bool = True) -> str:
        if not self.journal.exists():
            if allow_missing:
                return ""
            raise LedgerError(f"journal not found: {self.journal}")
        cmd = ["hledger", "-f", str(self.journal), "--infer-market-prices", *args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise LedgerError(proc.stderr.strip() or f"hledger failed: {' '.join(cmd)}")
        return proc.stdout

    def _csv(self, *args: str) -> list[dict[str, str]]:
        out = self.run(*args, "-O", "csv")
        if not out.strip():
            return []
        return list(csv.DictReader(io.StringIO(out)))

    def check(self) -> None:
        self.run("check", allow_missing=False)

    # -------------------------------------------------------------- balances
    def balances(
        self,
        query: str = "",
        *,
        period: str | None = None,
        in_base: bool = False,
        depth: int | None = None,
        flat: bool = True,
        end: str | None = None,
    ) -> list[tuple[str, list[Amount]]]:
        """Account balances for a query. Returns [(account, [amounts])]."""
        args = ["balance", "-N"]
        if flat:
            args.append("--flat")
        if query:
            args += query.split()
        if period:
            args += ["-p", period]
        if end:
            args += ["-e", end]
        if in_base:
            args += ["-X", BASE_COMMODITY]
        if depth is not None:
            args += ["--depth", str(depth)]
        rows = self._csv(*args)
        result = []
        for r in rows:
            result.append((r["account"], parse_amounts(r["balance"])))
        return result

    def total(self, query: str, *, period: str | None = None, in_base: bool = True, end: str | None = None) -> float:
        """Total of a query in base currency (sums the parsed balances)."""
        rows = self.balances(query, period=period, in_base=in_base, end=end)
        return round(sum(a.quantity for _, amts in rows for a in amts if a.commodity == BASE_COMMODITY), 2)

    def commodity_total(self, query: str, commodity: str) -> float:
        rows = self.balances(query, in_base=False)
        return sum(a.quantity for _, amts in rows for a in amts if a.commodity == commodity)

    def account_names(self) -> list[str]:
        out = self.run("accounts")
        return [l.strip() for l in out.splitlines() if l.strip()]

    # ---------------------------------------------------------- transactions
    def transactions(self, query: str = "", *, period: str | None = None) -> list[Transaction]:
        args = ["print", "-O", "json"]
        if query:
            args += query.split()
        if period:
            args += ["-p", period]
        out = self.run(*args)
        if not out.strip():
            return []
        data = json.loads(out)
        txns = []
        for t in data:
            postings = []
            for p in t["tpostings"]:
                amts = [
                    Amount(float(a["aquantity"]["floatingPoint"]), a["acommodity"])
                    for a in p["pamount"]
                ]
                postings.append(Posting(p["paccount"], amts))
            txns.append(Transaction(int(t["tindex"]), t["tdate"], t["tdescription"], postings))
        return txns

    def latest_price(self, commodity: str) -> float | None:
        out = self.run("prices")
        price = None
        for line in out.splitlines():
            parts = line.split()
            # P DATE COMMODITY PRICE
            if len(parts) >= 4 and parts[0] == "P" and parts[2] == commodity:
                try:
                    price = parse_amount(" ".join(parts[3:])).quantity
                except LedgerError:
                    continue
        return price

    # -------------------------------------------------------------- writing
    def append_transaction(
        self,
        date: str,
        description: str,
        postings: list[tuple[str, str]],
    ) -> str:
        """Append a transaction. `postings` = [(account, amount_text_or_empty)]."""
        try:
            dt.date.fromisoformat(date)
        except ValueError as e:
            raise LedgerError(f"invalid date {date!r}: use YYYY-MM-DD") from e
        lines = [f"{date} {description.strip()}"]
        for account, amount in postings:
            if amount:
                lines.append(f"    {account:<40} {amount}")
            else:
                lines.append(f"    {account}")
        block = "\n".join(lines) + "\n"
        before = self.journal.read_text(encoding="utf-8") if self.journal.exists() else ""
        sep = "" if (not before or before.endswith("\n\n")) else ("\n" if before.endswith("\n") else "\n\n")
        self.journal.write_text(before + sep + block, encoding="utf-8")
        try:
            self.check()
        except LedgerError as e:
            self.journal.write_text(before, encoding="utf-8")  # roll back
            raise LedgerError(f"transaction rejected: {e}") from e
        return block

    # -------------------------------------------------------------- periods
    def resolve_period(self, period: str | None) -> str | None:
        """Turn friendly period words into hledger period expressions."""
        if not period:
            return None
        p = period.strip().lower()
        t = self.today
        tomorrow = (t + dt.timedelta(days=1)).isoformat()
        first_this = t.replace(day=1)
        last_month_end = first_this - dt.timedelta(days=1)
        first_last = last_month_end.replace(day=1)
        table = {
            "this month": f"{first_this.isoformat()}..{tomorrow}",
            "current month": f"{first_this.isoformat()}..{tomorrow}",
            "month to date": f"{first_this.isoformat()}..{tomorrow}",
            "last month": f"{first_last.isoformat()}..{first_this.isoformat()}",
            "previous month": f"{first_last.isoformat()}..{first_this.isoformat()}",
            "this year": f"{t.year}",
            "current year": f"{t.year}",
            "year to date": f"{t.year}-01-01..{tomorrow}",
            "ytd": f"{t.year}-01-01..{tomorrow}",
            "last year": f"{t.year - 1}",
            "previous year": f"{t.year - 1}",
            "today": f"{t.isoformat()}..{tomorrow}",
            "all": None,
            "all time": None,
            "ever": None,
            "": None,
        }
        if p in table:
            return table[p]
        m = re.match(r"last (\d+) days?", p)
        if m:
            start = t - dt.timedelta(days=int(m.group(1)))
            return f"{start.isoformat()}..{tomorrow}"
        m = re.match(r"last (\d+) months?", p)
        if m:
            n = int(m.group(1))
            y, mo = t.year, t.month - n
            while mo <= 0:
                mo += 12
                y -= 1
            return f"{y}-{mo:02d}-01..{tomorrow}"
        # month names like "july" or "july 2026"
        m = re.match(r"([a-z]+)(?:\s+(\d{4}))?$", p)
        if m:
            try:
                month = dt.datetime.strptime(m.group(1)[:3], "%b").month
                year = int(m.group(2)) if m.group(2) else t.year
                if not m.group(2) and month > t.month:
                    year -= 1
                return f"{year}-{month:02d}"
            except ValueError:
                pass
        # otherwise trust hledger's own period syntax (2026, 2026-07, 2026-01..2026-03, ...)
        return period
