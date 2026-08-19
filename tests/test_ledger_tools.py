"""Offline tests (no model needed): the hledger wrapper and the tools."""

import datetime as dt
import shutil
from pathlib import Path

import pytest

from acct_agent.ledger import Ledger, LedgerError, find_journal, parse_amount, parse_amounts
from acct_agent.tools import AccountingTools

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "sample.journal"
TODAY = dt.date(2026, 8, 18)


@pytest.fixture
def journal(tmp_path):
    p = tmp_path / "main.journal"
    shutil.copy(FIXTURE, p)
    return p


@pytest.fixture
def tools(journal):
    return AccountingTools(Ledger(journal, today=TODAY))


def test_parse_amounts():
    assert parse_amount("$1,234.50").quantity == 1234.5
    assert parse_amount("-$5.00").quantity == -5.0
    a = parse_amount("0.42000000 BTC")
    assert (a.quantity, a.commodity) == (0.42, "BTC")
    assert [x.commodity for x in parse_amounts("$10.00, 0.5 BTC")] == ["$", "BTC"]


def test_find_journal(tmp_path, monkeypatch):
    monkeypatch.delenv("LEDGER_FILE", raising=False)
    assert find_journal(tmp_path) == tmp_path / "main.journal"
    (tmp_path / "books.journal").write_text("")
    assert find_journal(tmp_path) == tmp_path / "books.journal"
    (tmp_path / "main.journal").write_text("")
    assert find_journal(tmp_path) == tmp_path / "main.journal"


def test_resolve_period(journal):
    l = Ledger(journal, today=TODAY)
    assert l.resolve_period("last month") == "2026-07-01..2026-08-01"
    assert l.resolve_period("this month") == "2026-08-01..2026-08-19"
    assert l.resolve_period("july") == "2026-07"
    assert l.resolve_period("December") == "2025-12"
    assert l.resolve_period("2026-07") == "2026-07"
    assert l.resolve_period("all") is None


def test_net_worth_and_debt(tools):
    nw = tools.net_worth()
    assert "NET WORTH (assets - liabilities): $174,581.60" in nw
    assert "TOTAL DEBT: $243,065.35" in tools.total_debt()


def test_bitcoin(tools):
    out = tools.bitcoin_holdings()
    assert "TOTAL BITCOIN: 0.43300000 BTC" in out
    assert "$49,795.00" in out


def test_spending(tools):
    out = tools.spending_by_category("last month")
    assert out.splitlines()[1].strip().startswith("dining: $1,941.60")
    assert "TOTAL SPENDING: $4,235.21" in out
    assert "No expenses" in tools.spending_by_category("2019")


def test_unusual(tools):
    out = tools.find_unusual_transactions()
    assert "UNKNOWN MERCHANT XJ-9921" in out
    assert out.index("HIGHLY UNUSUAL") < out.index("POSSIBLE DUPLICATE")


def test_search(tools):
    assert "486.00" in tools.search_transactions("Delta")
    assert "9 transaction(s)" in tools.search_transactions("netflix")
    assert "No transactions" in tools.search_transactions("zzz-nothing")


def test_add_transaction_and_rollback(tools, journal):
    out = tools.add_transaction("2026-08-18", "Trader Joe's", 45.20, "liabilities:credit-card:visa", "expenses:groceries")
    assert "Recorded" in out
    text = journal.read_text()
    assert "2026-08-18 Trader Joe's" in text and "$45.20" in text
    assert "45.20" in tools.search_transactions("Trader")
    before = journal.read_text()
    bad = tools.add_transaction("not-a-date", "x", 1, "a", "b")
    assert bad.startswith("ERROR")
    assert journal.read_text() == before


def test_missing_journal(tmp_path):
    t = AccountingTools(Ledger(tmp_path / "main.journal", today=TODAY))
    assert "empty" in t.list_accounts()
    assert "Recorded" in t.add_transaction("2026-08-18", "Coffee", 4, "assets:cash", "expenses:coffee")
    assert (tmp_path / "main.journal").exists()


def test_monthly_summary_and_drilldown(tools):
    out = tools.monthly_summary("this year")
    assert "Highest-spending month: 2026-07 ($4,235.21)" in out
    assert "AVERAGE per month: income $7,956.25 / expenses $2,110.42" in out
    drill = tools.spending_by_category("this year", "interest")
    assert "interest:mortgage: $9,356.00" in drill
    assert "TOTAL spent across all 8 matching transaction(s): $1,172.74" in tools.search_transactions("Kroger", "this year", 3)
