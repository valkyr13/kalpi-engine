from decimal import Decimal

from app.brokers.base import (
    Broker,
    BrokerOrder,
    OrderStatus,
    PermanentError,
    TemporaryError,
    UnknownOutcome,
)
from app.planner import Order
from app.schemas import Side


class FakeBroker(Broker):
    """An in-memory broker for tests and demos.

    Orders start OPEN and execute the first time someone checks on them,
    so the engine has to wait for them, just like with a real broker.

    `failures` makes chosen symbols misbehave when an order is placed:
      "permanent"     always refused (like an invalid symbol)
      "temporary"     "too many requests" twice, then accepted
      "lost_request"  times out once; the order never reached the broker
      "lost_reply"    times out once; the order WAS placed, only the reply was lost
    """

    def __init__(self, holdings=None, cash="0", prices=None, failures=None):
        self.holdings = dict(holdings or {})
        self.cash = Decimal(cash)
        self.prices = {s: Decimal(p) for s, p in (prices or {}).items()}
        self.failures = dict(failures or {})
        self._temporary_left = {s: 2 for s, kind in self.failures.items() if kind == "temporary"}
        self.orders: dict[str, BrokerOrder] = {}
        self._waiting: dict[str, Order] = {}
        self._next_id = 1

    def price(self, symbol: str) -> Decimal:
        return self.prices.get(symbol, Decimal("100"))

    def place_order(self, order: Order) -> str:
        kind = self.failures.get(order.symbol)
        if kind == "permanent":
            raise PermanentError(f"invalid symbol: {order.symbol}")
        if kind == "temporary" and self._temporary_left[order.symbol] > 0:
            self._temporary_left[order.symbol] -= 1
            raise TemporaryError("too many requests, slow down")
        if kind == "lost_request":
            del self.failures[order.symbol]  # happens once
            raise UnknownOutcome("timed out waiting for the broker")
        broker_id = self._accept(order)
        if kind == "lost_reply":
            del self.failures[order.symbol]  # happens once
            raise UnknownOutcome("timed out waiting for the broker")
        return broker_id

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        order = self._waiting.pop(broker_order_id, None)
        if order:
            self._execute(broker_order_id, order)
        return self.orders[broker_order_id]

    def find_order_by_tag(self, tag: str) -> BrokerOrder | None:
        for o in self.orders.values():
            if o.tag == tag:
                return o
        return None

    def get_holdings(self) -> dict[str, int]:
        return {s: q for s, q in self.holdings.items() if q > 0}

    def _accept(self, order: Order) -> str:
        broker_id = f"FAKE-{self._next_id}"
        self._next_id += 1

        problem = self._check(order)
        if problem:
            self.orders[broker_id] = BrokerOrder(broker_id, order.id, OrderStatus.REJECTED, message=problem)
            return broker_id

        # Block the shares or cash now, so the same share or rupee can't be used twice.
        if order.side == Side.SELL:
            self.holdings[order.symbol] -= order.quantity
        else:
            self.cash -= self.price(order.symbol) * order.quantity
        self.orders[broker_id] = BrokerOrder(broker_id, order.id, OrderStatus.OPEN)
        self._waiting[broker_id] = order
        return broker_id

    def _check(self, order: Order) -> str | None:
        if order.side == Side.SELL:
            have = self.holdings.get(order.symbol, 0)
            if have < order.quantity:
                return f"insufficient holdings: have {have} {order.symbol}, selling {order.quantity}"
        else:
            cost = self.price(order.symbol) * order.quantity
            if cost > self.cash:
                return f"insufficient funds: need ₹{cost}, have ₹{self.cash}"
        return None

    def _execute(self, broker_id: str, order: Order) -> None:
        price = self.price(order.symbol)
        if order.side == Side.SELL:
            self.cash += price * order.quantity  # sale money arrives only now
        else:
            self.holdings[order.symbol] = self.holdings.get(order.symbol, 0) + order.quantity
        o = self.orders[broker_id]
        o.status = OrderStatus.COMPLETE
        o.filled_quantity = order.quantity
        o.average_price = price
