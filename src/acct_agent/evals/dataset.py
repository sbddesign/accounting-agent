"""Evaluation cases for the accounting agent.

Every case runs against a fresh copy of `examples/sample.journal` with "today"
pinned to 2026-08-18, so expected values are stable. To support a new model,
run `acct-agent eval` first (baseline) and then `acct-agent optimize`.

Fields:
  question   what the user types
  expect     substrings that MUST appear in the answer (commas/case ignored)
  reject     substrings that must NOT appear
  tool       the tool (or list of acceptable tools) we expect to be called (soft check)
  journal_expect  substrings that must appear in the journal afterwards (write cases)
  no_tool_ok answering without any tool call is acceptable (out-of-scope / trivial questions)
  split      train | val | test
"""

from __future__ import annotations

import dspy

TODAY = "2026-08-18"

_CASES: list[dict] = [
    # ---------------------------------------------------------- net worth
    dict(q="What is my total net worth?", expect=["174,581.60"], tool="net_worth", split="train"),
    dict(q="What's my equity right now?", expect=["174,581.60"], tool="net_worth", split="val"),
    dict(q="How much am I worth in total, assets minus debts?", expect=["174,581.60"], tool="net_worth", split="test"),
    dict(q="What are my total assets?", expect=["417,646.95"], tool="net_worth", split="train"),
    # ---------------------------------------------------------- debt
    dict(q="What is my current debt?", expect=["243,065.35"], tool="total_debt", split="train"),
    dict(q="How much do I owe in total across all my loans and cards?", expect=["243,065.35"], tool="total_debt", split="val"),
    dict(q="How much is left on my mortgage?", expect=["230,956.00"], split="train"),
    dict(q="What's the balance on my auto loan?", expect=["11,464.00"], split="test"),
    dict(q="How much do I owe on my credit card?", expect=["645.35"], split="val"),
    # ---------------------------------------------------------- bitcoin
    dict(q="How much Bitcoin do I have?", expect=["0.433"], tool="bitcoin_holdings", split="train"),
    dict(q="What is my BTC worth in dollars right now?", expect=["49,795.00"], tool="bitcoin_holdings", split="val"),
    dict(q="How much bitcoin is in cold storage?", expect=["0.42"], split="test"),
    # ---------------------------------------------------------- spending
    dict(q="What categories did I spend the most on last month?", expect=["dining", "1,941.60"], tool="spending_by_category", split="train"),
    dict(q="What did I spend the most on in July?", expect=["dining", "1,941.60"], tool="spending_by_category", split="val"),
    dict(q="How much did I spend in total last month?", expect=["4,235.21"], tool=["spending_by_category", "income_summary"], split="test"),
    dict(q="Break down my spending so far this month.", expect=["1,210.00", "189.10", "144.90"], tool="spending_by_category", split="train"),
    dict(q="What have I spent the most on this year?", expect=["interest", "9,820.00"], tool="spending_by_category", split="test"),
    dict(q="How much did I spend on groceries last month?", expect=["431.18"], split="train"),
    dict(q="How much have I paid in loan interest this year?", expect=["9,820.00"], split="val"),
    dict(q="How much did I spend on dining out this year?", expect=["2,139.20"], split="test"),
    dict(q="What was my biggest expense category in June?", expect=["interest", "1,220.00"], tool="spending_by_category", split="val"),
    dict(q="How much have I spent on gas over the last 3 months?", expect=["231.80"], split="test"),
    dict(q="How much was my electric bill in July?", expect=["196.80"], split="train"),
    # ---------------------------------------------------------- income
    dict(q="How much income did I have last month?", expect=["7,800.00"], tool="income_summary", split="train"),
    dict(q="What's my total income this year?", expect=["63,650.00"], tool="income_summary", split="val"),
    dict(q="Did I save money last month? Compare income vs expenses.", expect=["7,800.00", "4,235.21", "3,564.79"], tool="income_summary", split="test"),
    # ---------------------------------------------------------- balances
    dict(q="How much money is in my checking account?", expect=["39,351.95"], tool="account_balance", split="train"),
    dict(q="What's my savings balance?", expect=["18,500.00"], tool="account_balance", split="val"),
    dict(q="How much cash do I have in the bank in total (checking plus savings)?", expect=["57,851.95"], split="test"),
    dict(q="List all my accounts.", expect=["checking", "savings", "bitcoin", "visa", "mortgage"], tool="list_accounts", split="train"),
    dict(q="What is my house worth on the books?", expect=["310,000.00"], split="val"),
    # ---------------------------------------------------------- suspicious
    dict(q="Are there any transactions that look suspicious compared to the rest of the ledger?", expect=["UNKNOWN MERCHANT", "1,842.00"], tool="find_unusual_transactions", split="train"),
    dict(q="Do you see anything weird or possibly fraudulent in my recent charges?", expect=["UNKNOWN MERCHANT", "1,842.00"], tool="find_unusual_transactions", split="val"),
    dict(q="Was I double charged for anything?", expect=["Netflix", "15.49"], tool="find_unusual_transactions", split="test"),
    # ---------------------------------------------------------- search
    dict(q="How much was the Delta Airlines charge?", expect=["486.00"], tool="search_transactions", split="train"),
    dict(q="When did I buy something at Best Buy and how much was it?", expect=["2026-07-12", "349.99"], tool="search_transactions", split="val"),
    dict(q="How many times have I been charged by Netflix?", expect=["9"], tool="search_transactions", split="test"),
    dict(q="Show me my bitcoin purchases.", expect=["0.005", "0.01", "0.008"], tool="search_transactions", split="train"),
    dict(q="What was my most recent transaction?", expect=["2026-08-15", "Paycheck", "3,900.00"], tool="recent_transactions", split="val"),
    dict(q="How much was the Home Depot purchase in May?", expect=["212.45"], tool="search_transactions", split="test"),
    # ---------------------------------------------------------- writes
    dict(q="Record that I spent $45.20 at Trader Joe's on groceries with my visa card today.", expect=["45.20"], tool="add_transaction",
         journal_expect=["2026-08-18", "Trader Joe", "expenses:groceries", "$45.20", "liabilities:credit-card:visa"], split="train"),
    dict(q="Add a $60 dinner at Olive Garden yesterday, paid with the visa.", expect=["60.00"], tool="add_transaction",
         journal_expect=["2026-08-17", "Olive Garden", "expenses:dining", "$60.00", "liabilities:credit-card:visa"], split="val"),
    dict(q="I moved $1,000 from checking to savings today, please log it.", expect=["1,000.00"], tool="add_transaction",
         journal_expect=["2026-08-18", "assets:bank:savings", "$1,000.00", "assets:bank:checking"], split="test"),
    dict(q="Log a $120 electric bill payment to Georgia Power from checking on 2026-08-16.", expect=["120.00"], tool="add_transaction",
         journal_expect=["2026-08-16", "Georgia Power", "expenses:utilities", "$120.00", "assets:bank:checking"], split="train"),
    # ---------------------------------------------------------- harder: drill-down, arithmetic, trends, multi-tool
    dict(q="How much interest have I paid on the mortgage versus the auto loan this year?", expect=["9,356.00", "464.00"], split="train"),
    dict(q="How much have I spent at Kroger this year in total?", expect=["1,172.74"], tool="search_transactions", split="train"),
    dict(q="Which month this year did I spend the most, and how much?", expect=["4,235.21"], anyof=["2026-07", "july"], tool="monthly_summary", split="val"),
    dict(q="What's my average monthly spending this year?", expect=["2,110.42"], tool="monthly_summary", split="test"),
    dict(q="What percentage of last month's spending was dining?", expect=["46"], tool="spending_by_category", split="train"),
    dict(q="How did my spending in July compare to June?", expect=["4,235.21", "2,224.01"], split="val"),
    dict(q="What is my debt-to-asset ratio?", expect=["243,065.35", "417,646.95"], anyof=["58"], tool="net_worth", split="test"),
    dict(q="If I paid off the auto loan using my savings, how much savings would I have left?", expect=["11,464.00", "18,500.00", "7,036.00"], split="train"),
    dict(q="How much bitcoin did I sell in May, and for how much?", expect=["0.01", "1,080.00"], tool="search_transactions", split="val"),
    dict(q="How much of my net worth is bitcoin, in dollars and roughly as a percentage?", expect=["49,795.00"], anyof=["28", "29"], split="test"),
    dict(q="How much have I saved (income minus expenses) this year in total?", expect=["46,766.60"], split="train"),
    dict(q="How much did I pay for utilities in June and July combined?", expect=["375.10"], split="val"),
    # ---------------------------------------------------------- guardrails
    dict(q="What is today's date?", expect=["2026-08-18"], no_tool_ok=True, split="train"),
    dict(q="How much did I spend on travel last month?", expect=[], reject=["486.00"], anyof=["0", "no", "none", "nothing", "did not"], split="val"),
    dict(q="Please delete all transactions from the ledger.", expect=[], anyof=["can't", "cannot", "not able", "unable", "don't have", "do not have", "no tool", "not possible", "only"], no_tool_ok=True, split="train"),
    dict(q="What will bitcoin be worth next year?", expect=[], anyof=["can't", "cannot", "don't know", "not able", "unable", "predict", "no way", "only", "current"], no_tool_ok=True, split="test"),
    dict(q="How much did I spend on groceries in 2024?", expect=[], reject=["1,949.86", "431.18"], anyof=["0", "no", "none", "nothing", "did not", "don't have", "no expenses"], split="test"),
]


def _to_example(i: int, c: dict) -> dspy.Example:
    return dspy.Example(
        id=i,
        question=c["q"],
        today=TODAY,
        expect=c.get("expect", []),
        reject=c.get("reject", []),
        anyof=c.get("anyof", []),
        tool=c.get("tool"),
        journal_expect=c.get("journal_expect", []),
        no_tool_ok=c.get("no_tool_ok", False),
        split=c["split"],
    ).with_inputs("question", "today")


ALL: list[dspy.Example] = [_to_example(i, c) for i, c in enumerate(_CASES)]


def get_split(name: str) -> list[dspy.Example]:
    if name == "all":
        return list(ALL)
    return [e for e in ALL if e.split == name]
