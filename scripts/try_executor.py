"""
Runs the executor against the fake broker and prints what happens.

For each scenario it shows the starting account, the orders, what should
happen in plain words, the engine's own log lines, what actually happened,
how many orders the broker really received, and PASS or FAIL.

Usage (from the project root, with the venv active):
    python scripts/try_executor.py
"""
import logging
import sys
import textwrap
from pathlib import Path

# This script lives in scripts/, so tell Python where the `app` package is.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brokers.base import OrderStatus  # noqa: E402
from app.brokers.fake import FakeBroker  # noqa: E402
from app.executor import execute  # noqa: E402
from app.planner import Order  # noqa: E402
from app.schemas import Side  # noqa: E402

# Show the engine's log lines, indented, so you can watch retries and lookups happen.
logging.basicConfig(level=logging.INFO, format="    [engine] %(message)s", stream=sys.stdout)

PRICES = {"ITC": "400", "TCS": "3300", "INFY": "1500"}
LINE = "=" * 70


def show_account(label: str, broker: FakeBroker) -> None:
    print(f"{label}: cash ₹{broker.cash}, holdings {broker.get_holdings() or 'none'}")


def run_scenario(name, holdings, cash, orders, explanation, expected, failures=None) -> bool:
    print(LINE)
    print(f"SCENARIO: {name}")
    print(LINE)
    broker = FakeBroker(holdings=holdings, cash=cash, prices=PRICES, failures=failures)
    print("Prices: " + ", ".join(f"{s} ₹{p}" for s, p in PRICES.items()))
    if failures:
        print("Simulated trouble: " + ", ".join(f"{s} -> {kind}" for s, kind in failures.items()))
    show_account("Before", broker)
    print("Orders, in the order they were given:")
    for o in orders:
        print(f"  {o.side} {o.symbol} {o.quantity}")
    print()
    print("What should happen:")
    print(textwrap.fill(explanation, width=68, initial_indent="  ", subsequent_indent="  "))
    print()
    print("Running:")

    results = execute(orders, broker)

    print()
    print("What actually happened, in execution order:")
    passed = True
    for r in results:
        line = f"  {r.order.side} {r.order.symbol} {r.order.quantity} -> {r.status}"
        if r.filled_quantity:
            line += f", filled {r.filled_quantity} @ ₹{r.average_price}"
        if r.message:
            line += f" ({r.message})"
        print(line)
        if r.status != expected[r.order.symbol]:
            passed = False
            print(f"    expected {expected[r.order.symbol]} here")

    # The idempotency check: the broker must never hold two orders for one of ours.
    received = {}
    for r in results:
        received[r.order.symbol] = sum(1 for b in broker.orders.values() if b.tag == r.order.id)
    print("Orders the broker actually received: "
          + ", ".join(f"{s} x{n}" for s, n in received.items()))
    for r in results:
        should_have = 0 if r.status == OrderStatus.FAILED else 1
        if received[r.order.symbol] != should_have:
            passed = False
            print(f"    expected the broker to have {should_have} order(s) for {r.order.symbol}")

    show_account("After", broker)
    print()
    print(">> PASS" if passed else ">> FAIL")
    print()
    return passed


def main() -> None:
    buy_first = [Order(Side.BUY, "TCS", 3), Order(Side.SELL, "ITC", 40), Order(Side.BUY, "INFY", 5)]
    outcomes = [
        run_scenario(
            name="1. Enough cash once the sell lands",
            holdings={"ITC": 40},
            cash="2000",
            orders=buy_first,
            explanation=(
                "A BUY is listed first, but the executor sells first. Selling 40 ITC at ₹400 "
                "brings in ₹16,000, so together with the ₹2,000 already there, both buys "
                "(₹9,900 for TCS and ₹7,500 for INFY) fit. Everything completes."
            ),
            expected={"ITC": "COMPLETE", "TCS": "COMPLETE", "INFY": "COMPLETE"},
        ),
        run_scenario(
            name="2. Not enough cash for every buy",
            holdings={"ITC": 40},
            cash="0",
            orders=[Order(Side.BUY, "TCS", 3), Order(Side.SELL, "ITC", 40), Order(Side.BUY, "INFY", 5)],
            explanation=(
                "The sale brings in ₹16,000. TCS (₹9,900) fits and leaves ₹6,100, which is "
                "not enough for INFY (₹7,500). TCS completes, INFY is rejected with the "
                "reason, and nothing crashes."
            ),
            expected={"ITC": "COMPLETE", "TCS": "COMPLETE", "INFY": "REJECTED"},
        ),
        run_scenario(
            name="3. A sell fails, but the buys still go ahead",
            holdings={"ITC": 30},
            cash="10000",
            orders=[Order(Side.SELL, "ITC", 40), Order(Side.BUY, "TCS", 3), Order(Side.BUY, "INFY", 5)],
            explanation=(
                "The user holds only 30 ITC, so selling 40 is rejected. The executor does not "
                "halt: it still runs the buys with the ₹10,000 available. TCS (₹9,900) fits; "
                "INFY (₹7,500) does not."
            ),
            expected={"ITC": "REJECTED", "TCS": "COMPLETE", "INFY": "REJECTED"},
        ),
        run_scenario(
            name="4. Temporary error: retry with growing waits",
            holdings={"ITC": 40},
            cash="2000",
            orders=[Order(Side.SELL, "ITC", 40), Order(Side.BUY, "TCS", 3)],
            failures={"TCS": "temporary"},
            explanation=(
                "The broker answers 'too many requests' twice for TCS. That can clear on its "
                "own, so the engine waits 0.5s, then 1s, and tries again. The third attempt "
                "goes through and TCS completes."
            ),
            expected={"ITC": "COMPLETE", "TCS": "COMPLETE"},
        ),
        run_scenario(
            name="5. Permanent error: no retry",
            holdings={"ITC": 40},
            cash="2000",
            orders=[Order(Side.SELL, "ITC", 40), Order(Side.BUY, "TCS", 3), Order(Side.BUY, "INFY", 5)],
            failures={"INFY": "permanent"},
            explanation=(
                "The broker refuses INFY as an invalid symbol. Waiting won't fix that, so the "
                "engine marks it FAILED straight away, with no retries, and the other orders "
                "carry on normally."
            ),
            expected={"ITC": "COMPLETE", "TCS": "COMPLETE", "INFY": "FAILED"},
        ),
        run_scenario(
            name="6. Timeout, and the order never arrived",
            holdings={"ITC": 40},
            cash="2000",
            orders=[Order(Side.SELL, "ITC", 40), Order(Side.BUY, "TCS", 3)],
            failures={"TCS": "lost_request"},
            explanation=(
                "The TCS request times out. The engine doesn't know what happened, so it looks "
                "for the order by its tag. The broker never got it, so sending it again is safe. "
                "The broker ends up with exactly one TCS order."
            ),
            expected={"ITC": "COMPLETE", "TCS": "COMPLETE"},
        ),
        run_scenario(
            name="7. Timeout, but the order DID arrive",
            holdings={"ITC": 40},
            cash="2000",
            orders=[Order(Side.SELL, "ITC", 40), Order(Side.BUY, "TCS", 3)],
            failures={"TCS": "lost_reply"},
            explanation=(
                "The TCS order reached the broker, but the reply got lost. From the engine's "
                "side this looks exactly like scenario 6. The lookup finds the order by its tag, "
                "so the engine tracks that order instead of sending a second one. The broker "
                "still has exactly one TCS order: no double buy."
            ),
            expected={"ITC": "COMPLETE", "TCS": "COMPLETE"},
        ),
    ]
    print(LINE)
    print(f"SUMMARY: {sum(outcomes)} of {len(outcomes)} scenarios passed")
    print(LINE)
    sys.exit(0 if all(outcomes) else 1)


if __name__ == "__main__":
    main()
