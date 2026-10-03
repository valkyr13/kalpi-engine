from dataclasses import dataclass, field
from uuid import uuid4

from app.schemas import ExecutionRequest, FirstTimeRequest, Side


def new_order_id() -> str:
    return uuid4().hex[:16]  # short: some brokers cap tags at 20 characters


@dataclass(frozen=True)
class Order:
    side: Side
    symbol: str
    quantity: int
    id: str = field(default_factory=new_order_id)


def plan(request: ExecutionRequest) -> list[Order]:
    if isinstance(request, FirstTimeRequest):
        return [Order(Side.BUY, s.symbol, s.quantity) for s in request.stocks]

    sells = [Order(Side.SELL, s.symbol, s.quantity) for s in request.sell]
    buys = [Order(Side.BUY, s.symbol, s.quantity) for s in request.buy]
    for a in request.adjust:
        if a.side == Side.SELL:
            sells.append(Order(Side.SELL, a.symbol, a.quantity))
        else:
            buys.append(Order(Side.BUY, a.symbol, a.quantity))
    return sells + buys