"""Upstox adapter: translates between our Broker interface and Upstox's REST API.

API docs: https://upstox.com/developer/api-documentation
"""
import gzip
import json
import time
from decimal import Decimal

import httpx

from app.brokers.base import (
    Broker,
    BrokerOrder,
    OrderStatus,
    PermanentError,
    TemporaryError,
    UnknownOutcome,
)
from app.planner import Order

API_URL = "https://api.upstox.com"                  # reads: order details, history, holdings
ORDER_URL = "https://api-hft.upstox.com"            # writes: place, modify, cancel
SANDBOX_ORDER_URL = "https://api-sandbox.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# Upstox's status words -> ours. Every other Upstox status means "still in progress".
STATUS_MAP = {
    "complete": OrderStatus.COMPLETE,
    "rejected": OrderStatus.REJECTED,
    "cancelled": OrderStatus.CANCELLED,
    "cancelled after market order": OrderStatus.CANCELLED,
}

MIN_SECONDS_BETWEEN_ORDERS = 0.1  # at most 10 orders per second


class UpstoxBroker(Broker):
    def __init__(self, access_token: str, instruments: dict[str, str],
                 client: httpx.Client | None = None, sandbox: bool = False):
        self.instruments = instruments  # e.g. "INFY" -> "NSE_EQ|INE009A01021"
        self.order_url = SANDBOX_ORDER_URL if sandbox else ORDER_URL
        self.http = client or httpx.Client(timeout=10)
        self.http.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        })
        self._last_order_at = 0.0

    def place_order(self, order: Order) -> str:
        instrument_key = self.instruments.get(order.symbol)
        if instrument_key is None:
            raise PermanentError(f"Upstox doesn't know the symbol {order.symbol}")
        self._throttle()
        body = {
            "quantity": order.quantity,
            "product": "D",                        # delivery
            "validity": "DAY",
            "price": 0,
            "tag": order.id,                       # our idempotency key
            "instrument_token": instrument_key,
            "order_type": "MARKET",
            "transaction_type": order.side.value,  # "BUY" or "SELL"
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": False,                        # one of our orders = exactly one Upstox order
        }
        data = self._call("POST", f"{self.order_url}/v3/order/place", json=body, writes=True)
        return data["order_ids"][0]

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        data = self._call("GET", f"{API_URL}/v2/order/details", params={"order_id": broker_order_id})
        return self._to_broker_order(data)

    def find_order_by_tag(self, tag: str) -> BrokerOrder | None:
        history = self._call("GET", f"{API_URL}/v2/order/history", params={"tag": tag})
        if not history:
            return None
        return self._to_broker_order(history[-1])  # entries are oldest first; last = current state

    def get_holdings(self) -> dict[str, int]:
        rows = self._call("GET", f"{API_URL}/v2/portfolio/long-term-holdings")
        return {r["trading_symbol"]: r["quantity"] for r in rows if r["quantity"] > 0}

    # ---- translation helpers ----------------------------------------------

    @staticmethod
    def _to_broker_order(d: dict) -> BrokerOrder:
        avg = d.get("average_price")
        return BrokerOrder(
            broker_order_id=d["order_id"],
            tag=d.get("tag") or "",
            status=STATUS_MAP.get(d["status"], OrderStatus.OPEN),
            filled_quantity=d.get("filled_quantity") or 0,
            average_price=Decimal(str(avg)) if avg else None,
            message=d.get("status_message") or "",
        )

    def _call(self, method: str, url: str, writes: bool = False, **kwargs):
        """Send one request and translate every way it can fail into our three kinds."""
        try:
            response = self.http.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            # The request never left this machine, so nothing can have happened.
            raise TemporaryError(f"could not reach Upstox ({type(e).__name__})") from e
        except httpx.TransportError as e:
            # Sent, but no proper reply. A write may have gone through; a read is safe to repeat.
            if writes:
                raise UnknownOutcome(f"no reply from Upstox ({type(e).__name__})") from e
            raise TemporaryError(f"no reply from Upstox ({type(e).__name__})") from e

        if response.status_code == 429:
            raise TemporaryError("Upstox rate limit hit (HTTP 429)")
        if response.status_code >= 500:
            if writes:
                raise UnknownOutcome(f"Upstox server error (HTTP {response.status_code})")
            raise TemporaryError(f"Upstox server error (HTTP {response.status_code})")

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("status") != "success":
            raise PermanentError(self._error_text(body, response.status_code))
        return body["data"]

    @staticmethod
    def _error_text(body: dict, status_code: int) -> str:
        errors = body.get("errors") or []
        if errors:
            e = errors[0]
            return f"{e.get('error_code') or e.get('errorCode')}: {e.get('message')}"
        return f"Upstox error (HTTP {status_code})"

    def _throttle(self) -> None:
        wait = MIN_SECONDS_BETWEEN_ORDERS - (time.monotonic() - self._last_order_at)
        if wait > 0:
            time.sleep(wait)
        self._last_order_at = time.monotonic()


def load_instruments(url: str = INSTRUMENTS_URL) -> dict[str, str]:
    """Download Upstox's public instrument list; map NSE equity symbols to instrument keys."""
    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    rows = json.loads(gzip.decompress(response.content))
    return {r["trading_symbol"]: r["instrument_key"] for r in rows if r.get("segment") == "NSE_EQ"}
