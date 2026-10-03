"""Tests for the Upstox adapter, with no account and no network.

Every response below is shaped like the examples in Upstox's API docs.
httpx.MockTransport hands each request to a function we write, instead of
sending it over the internet, so we can check exactly what the adapter sends
and how it translates each kind of reply.
"""
import json
from decimal import Decimal

import httpx
import pytest

from app.brokers.base import OrderStatus, PermanentError, TemporaryError, UnknownOutcome
from app.brokers.upstox import UpstoxBroker
from app.executor import execute
from app.planner import Order
from app.schemas import Side

INSTRUMENTS = {
    "INFY": "NSE_EQ|INE009A01021",
    "TCS": "NSE_EQ|INE467B01029",
}

# ---- Responses copied from the shapes in Upstox's docs -------------------

PLACE_OK = {"status": "success", "data": {"order_ids": ["1644490272000"]}, "metadata": {"latency": 30}}


def upstox_error(code: str, message: str) -> dict:
    # Upstox repeats each field in camelCase and snake_case.
    return {"status": "error", "errors": [{
        "errorCode": code, "message": message, "propertyPath": None, "invalidValue": None,
        "error_code": code, "property_path": None, "invalid_value": None,
    }]}


def order_details(status: str, filled: int = 0, avg: float = 0.0, message=None) -> dict:
    return {"status": "success", "data": {
        "exchange": "NSE", "product": "D", "price": 0.0, "quantity": 3, "status": status,
        "tag": "abc123", "instrument_token": "NSE_EQ|INE467B01029", "trading_symbol": "TCS",
        "order_type": "MARKET", "transaction_type": "BUY", "average_price": avg,
        "filled_quantity": filled, "status_message": message, "order_id": "240108010445130",
    }}


HISTORY = {"status": "success", "data": [
    {"status": "put order req received", "order_id": "240108010445130", "tag": "abc123",
     "filled_quantity": 0, "average_price": 0.0, "status_message": None},
    {"status": "validation pending", "order_id": "240108010445130", "tag": "abc123",
     "filled_quantity": 0, "average_price": 0.0, "status_message": None},
    {"status": "open", "order_id": "240108010445130", "tag": "abc123",
     "filled_quantity": 0, "average_price": 0.0, "status_message": None},
    {"status": "complete", "order_id": "240108010445130", "tag": "abc123",
     "filled_quantity": 3, "average_price": 3301.5, "status_message": None},
]}

HOLDINGS = {"status": "success", "data": [
    {"isin": "INE528G01035", "quantity": 36, "trading_symbol": "YESBANK",
     "instrument_token": "NSE_EQ|INE528G01035", "average_price": 18.2},
    {"isin": "INE036A01016", "quantity": 1, "trading_symbol": "RELINFRA",
     "instrument_token": "NSE_EQ|INE036A01016", "average_price": 200.0},
]}


# ---- Helpers --------------------------------------------------------------

def make_broker(handler, sandbox=False) -> UpstoxBroker:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return UpstoxBroker("TEST_TOKEN", INSTRUMENTS, client=client, sandbox=sandbox)


def replies(status_code: int, body: dict):
    """A handler that always answers with the same response."""
    return lambda request: httpx.Response(status_code, json=body)


def raises(error: Exception):
    """A handler that fails the way the network can fail."""
    def handler(request):
        raise error
    return handler


BUY_TCS = Order(Side.BUY, "TCS", 3)


# ---- place_order ----------------------------------------------------------

def test_place_order_sends_the_right_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=PLACE_OK)

    order_id = make_broker(handler).place_order(BUY_TCS)

    assert order_id == "1644490272000"
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api-hft.upstox.com/v3/order/place"
    assert request.headers["Authorization"] == "Bearer TEST_TOKEN"
    body = json.loads(request.content)
    assert body["instrument_token"] == "NSE_EQ|INE467B01029"  # symbol translated
    assert body["transaction_type"] == "BUY"
    assert body["quantity"] == 3
    assert body["tag"] == BUY_TCS.id                           # idempotency key travels with it
    assert body["product"] == "D" and body["order_type"] == "MARKET"
    assert body["slice"] is False


def test_sandbox_sends_orders_to_the_sandbox_host():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=PLACE_OK)

    make_broker(handler, sandbox=True).place_order(BUY_TCS)
    assert seen[0].url.host == "api-sandbox.upstox.com"


def test_unknown_symbol_is_permanent_and_never_sent():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=PLACE_OK)

    with pytest.raises(PermanentError, match="WIPRO"):
        make_broker(handler).place_order(Order(Side.BUY, "WIPRO", 1))
    assert seen == []


def test_rate_limit_is_temporary():
    broker = make_broker(replies(429, upstox_error("UDAPI10005", "Too Many Request Sent")))
    with pytest.raises(TemporaryError):
        broker.place_order(BUY_TCS)


def test_rejected_input_is_permanent_with_upstox_code():
    broker = make_broker(replies(400, upstox_error("UDAPI100011", "Invalid Instrument key")))
    with pytest.raises(PermanentError, match="UDAPI100011"):
        broker.place_order(BUY_TCS)


def test_invalid_token_is_permanent():
    broker = make_broker(replies(401, upstox_error("UDAPI100050", "Invalid token used to access API")))
    with pytest.raises(PermanentError, match="UDAPI100050"):
        broker.place_order(BUY_TCS)


def test_could_not_connect_is_temporary():
    broker = make_broker(raises(httpx.ConnectError("connection refused")))
    with pytest.raises(TemporaryError):
        broker.place_order(BUY_TCS)


def test_no_reply_to_an_order_is_unknown():
    broker = make_broker(raises(httpx.ReadTimeout("timed out")))
    with pytest.raises(UnknownOutcome):
        broker.place_order(BUY_TCS)


def test_server_error_on_an_order_is_unknown():
    broker = make_broker(replies(500, {"status": "error", "errors": []}))
    with pytest.raises(UnknownOutcome):
        broker.place_order(BUY_TCS)


# ---- reads: get_order, find_order_by_tag, get_holdings ---------------------

def test_complete_order_is_translated():
    broker = make_broker(replies(200, order_details("complete", filled=3, avg=3301.5)))
    result = broker.get_order("240108010445130")
    assert result.status == OrderStatus.COMPLETE
    assert result.filled_quantity == 3
    assert result.average_price == Decimal("3301.5")


def test_rejected_order_keeps_the_reason():
    broker = make_broker(replies(200, order_details("rejected", message="Insufficient funds")))
    result = broker.get_order("240108010445130")
    assert result.status == OrderStatus.REJECTED
    assert result.message == "Insufficient funds"


@pytest.mark.parametrize("in_progress", ["put order req received", "validation pending", "open pending", "open"])
def test_in_progress_statuses_mean_open(in_progress):
    broker = make_broker(replies(200, order_details(in_progress)))
    assert broker.get_order("240108010445130").status == OrderStatus.OPEN


def test_timeout_on_a_read_is_temporary_not_unknown():
    broker = make_broker(raises(httpx.ReadTimeout("timed out")))
    with pytest.raises(TemporaryError):
        broker.get_order("240108010445130")


def test_find_by_tag_returns_the_latest_state():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=HISTORY)

    found = make_broker(handler).find_order_by_tag("abc123")
    assert seen[0].url.params["tag"] == "abc123"
    assert found.status == OrderStatus.COMPLETE
    assert found.broker_order_id == "240108010445130"


def test_find_by_tag_returns_none_when_the_broker_never_got_it():
    broker = make_broker(replies(200, {"status": "success", "data": []}))
    assert broker.find_order_by_tag("never-sent") is None


def test_holdings_are_translated_to_symbol_and_quantity():
    broker = make_broker(replies(200, HOLDINGS))
    assert broker.get_holdings() == {"YESBANK": 36, "RELINFRA": 1}


# ---- The adapter plugs into the engine unchanged ---------------------------

def test_engine_runs_unchanged_on_upstox():
    """The same executor that ran against the fake broker, now talking 'Upstox'."""
    placed = []

    def fake_upstox(request):
        if request.method == "POST":
            body = json.loads(request.content)
            placed.append(body)
            return httpx.Response(200, json={"status": "success",
                                             "data": {"order_ids": [f"ORD{len(placed)}"]}})
        details = order_details("complete", filled=5, avg=1500.0)
        details["data"]["order_id"] = request.url.params["order_id"]
        return httpx.Response(200, json=details)

    results = execute([Order(Side.BUY, "TCS", 3), Order(Side.SELL, "INFY", 5)], make_broker(fake_upstox))

    assert [p["transaction_type"] for p in placed] == ["SELL", "BUY"]  # sells still go first
    assert all(r.status == OrderStatus.COMPLETE for r in results)
