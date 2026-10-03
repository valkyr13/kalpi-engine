import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from app.brokers.base import (
    Broker,
    BrokerError,
    BrokerOrder,
    OrderStatus,
    PermanentError,
    TemporaryError,
    UnknownOutcome,
)
from app.planner import Order
from app.schemas import Side

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.5
MAX_WAIT = 30
MAX_ATTEMPTS = 4      # sends of one order, counting the first
FIRST_BACKOFF = 0.5   # seconds; doubles after every retry


@dataclass
class OrderResult:
    order: Order
    status: OrderStatus
    filled_quantity: int = 0
    average_price: Decimal | None = None
    message: str = ""
    broker_order_id: str | None = None


def execute(orders: list[Order], broker: Broker) -> list[OrderResult]:
    sells = [o for o in orders if o.side == Side.SELL]
    buys = [o for o in orders if o.side == Side.BUY]
    results = run_phase(sells, broker)  # sells first: their money pays for the buys
    results += run_phase(buys, broker)
    return results


def run_phase(orders: list[Order], broker: Broker) -> list[OrderResult]:
    results = [place(o, broker) for o in orders]
    for r in results:
        if r.broker_order_id:
            wait_until_finished(r, broker)
    return results


def place(order: Order, broker: Broker) -> OrderResult:
    """Send the order, retrying what might clear up and never sending it twice."""
    backoff = FIRST_BACKOFF
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            broker_id = broker.place_order(order)
            return OrderResult(order, OrderStatus.OPEN, broker_order_id=broker_id)
        except PermanentError as e:
            log.info("%s %s: refused, not retrying (%s)", order.side, order.symbol, e)
            return OrderResult(order, OrderStatus.FAILED, message=str(e))
        except UnknownOutcome as e:
            # The broker may have the order already; resending blindly could double it.
            log.info("%s %s: no reply (%s), looking it up by tag", order.side, order.symbol, e)
            try:
                found = find_by_tag(order, broker)
            except BrokerError as lookup_error:
                return OrderResult(order, OrderStatus.UNKNOWN,
                                   message=f"{e}; lookup failed: {lookup_error}")
            if found:
                log.info("%s %s: broker has it as %s, tracking that order",
                         order.side, order.symbol, found.broker_order_id)
                return OrderResult(order, OrderStatus.OPEN, broker_order_id=found.broker_order_id)
            log.info("%s %s: broker never got it, safe to send again", order.side, order.symbol)
            last_error = e
        except TemporaryError as e:
            log.info("%s %s: temporary error (%s)", order.side, order.symbol, e)
            last_error = e
        except BrokerError as e:
            return OrderResult(order, OrderStatus.FAILED, message=str(e))

        if attempt < MAX_ATTEMPTS:
            log.info("%s %s: retrying in %ss", order.side, order.symbol, backoff)
            time.sleep(backoff)
            backoff *= 2

    return OrderResult(order, OrderStatus.FAILED,
                       message=f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def find_by_tag(order: Order, broker: Broker) -> BrokerOrder | None:
    """Look the order up, retrying temporary errors. Raises if we still can't tell."""
    backoff = FIRST_BACKOFF
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return broker.find_order_by_tag(order.id)
        except TemporaryError:
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(backoff)
            backoff *= 2


def wait_until_finished(result: OrderResult, broker: Broker) -> None:
    deadline = time.monotonic() + MAX_WAIT
    while True:
        b = broker.get_order(result.broker_order_id)
        result.status = b.status
        result.filled_quantity = b.filled_quantity
        result.average_price = b.average_price
        result.message = b.message
        if b.status != OrderStatus.OPEN or time.monotonic() > deadline:
            return
        time.sleep(POLL_INTERVAL)
