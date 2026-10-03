from enum import StrEnum
from typing import Annotated, Literal
from pydantic import BaseModel, Field, model_validator
from collections import Counter

class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

class StockQty(BaseModel):
    symbol: str
    quantity: int = Field(gt=0)

class Adjustment(StockQty):
    side: Side

def repeated_symbols(orders) -> list[str]:
    counts = Counter(o.symbol for o in orders)
    return sorted(s for s, n in counts.items() if n > 1)

class FirstTimeRequest(BaseModel):
    mode: Literal["FIRST_TIME"]
    user_id: str
    broker: str
    stocks: list[StockQty] = Field(min_length=1)

    @model_validator(mode="after")
    def check_orders(self):
        repeated = repeated_symbols(self.stocks)
        if repeated:
            raise ValueError(f"each symbol may appear only once; repeated: {repeated}")
        return self

class RebalanceRequest(BaseModel):
    mode: Literal["REBALANCE"]
    user_id: str
    broker: str
    sell: list[StockQty] = []
    buy: list[StockQty] = []
    adjust: list[Adjustment] = []

    @model_validator(mode="after")
    def must_have_orders(self):
        if not (self.sell or self.buy or self.adjust):
            raise ValueError("rebalance needs at least one order in sell, buy or adjust")
        return self
    
    @model_validator(mode="after")
    def check_orders(self):
        orders = self.sell + self.buy + self.adjust
        if not orders:
            raise ValueError("rebalance needs at least one order in sell, buy or adjust")
        repeated = repeated_symbols(orders)
        if repeated:
            raise ValueError(f"each symbol may appear only once; repeated: {repeated}")
        return self

ExecutionRequest = Annotated[
    FirstTimeRequest | RebalanceRequest,
    Field(discriminator="mode"),
]