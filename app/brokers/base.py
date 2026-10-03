from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.planner import Order


class OrderStatus(StrEnum):
    OPEN = "OPEN"            # accepted, not finished yet
    COMPLETE = "COMPLETE"    # fully executed
    REJECTED = "REJECTED"    # refused; the reason is in `message`
    CANCELLED = "CANCELLED"  # stopped before fully executing
    FAILED = "FAILED"        # never accepted by the broker (our status, not a broker's)
    UNKNOWN = "UNKNOWN"      # we could not find out whether the broker has it (our status)


@dataclass
class BrokerOrder:
    broker_order_id: str
    tag: str                              # our Order.id
    status: OrderStatus
    filled_quantity: int = 0
    average_price: Decimal | None = None
    message: str = ""


class BrokerError(Exception):
    """Base class for every broker failure."""

class TemporaryError(BrokerError):
    """Might go away on its own (rate limit, broker hiccup): retry with backoff."""

class PermanentError(BrokerError):
    """Won't change by waiting (not enough shares, bad symbol, market closed): don't retry."""

class UnknownOutcome(BrokerError):
    """No answer (timeout): look the order up by its tag before deciding anything."""


class Broker(ABC):
    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Send the order tagged with order.id. Returns the broker's order ID."""

    @abstractmethod
    def get_order(self, broker_order_id: str) -> BrokerOrder:
        """Current state of one order."""

    @abstractmethod
    def find_order_by_tag(self, tag: str) -> BrokerOrder | None:
        """Today's order carrying our tag, or None if the broker never received it."""

    @abstractmethod
    def get_holdings(self) -> dict[str, int]:
        """Shares the user holds, by symbol."""
