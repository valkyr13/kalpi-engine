from fastapi import FastAPI
from enum import StrEnum
from pydantic import BaseModel, Field
from app.schemas import ExecutionRequest
from app.planner import plan


app = FastAPI(title="Kalpi Execution Engine")

class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

class OrderIn(BaseModel):
    symbol: str
    side: Side
    quantity: int = Field(gt=0)

@app.post("/orders/preview")
def preview(order: OrderIn):
    return order


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/plans")
def preview_plan(request: ExecutionRequest):
    return request


@app.post("/plans")
def preview_plan(request: ExecutionRequest):
    return plan(request)