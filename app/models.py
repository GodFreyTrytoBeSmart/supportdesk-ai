"""Validated input/output contracts. Identity is intentionally absent from chat input."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Intent(StrEnum):
    GENERAL_FAQ = "GENERAL_FAQ"
    SUBSCRIPTION_STATUS = "SUBSCRIPTION_STATUS"
    BILLING_HISTORY = "BILLING_HISTORY"
    CANCEL_SUBSCRIPTION = "CANCEL_SUBSCRIPTION"
    REFUND_REQUEST = "REFUND_REQUEST"
    TECHNICAL_ISSUE = "TECHNICAL_ISSUE"
    UNKNOWN = "UNKNOWN"


AUTH_INTENTS = {Intent.SUBSCRIPTION_STATUS, Intent.BILLING_HISTORY, Intent.REFUND_REQUEST}


class Classification(StrictModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    requires_authentication: bool


TransactionId = Annotated[str, StringConstraints(pattern=r"^tx_[a-z0-9_]{1,60}$")]


class SessionCreate(StrictModel):
    persona: Literal["guest", "alex", "blair", "casey"]


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    event_id: UUID
    transaction_id: TransactionId | None = None
    request_human: bool = False


class TicketCreate(StrictModel):
    event_id: UUID
    category: Intent
    description: str = Field(min_length=1, max_length=500)
    transaction_id: TransactionId | None = None


class ReceiptAck(StrictModel):
    receipt_token: str = Field(min_length=20, max_length=200)


class Ticket(StrictModel):
    id: str
    customer_ref: str
    category: Intent
    description: str
    transaction_id: str | None
    status: Literal["open"]
    created_at: str
    escalation_reason: str


class Source(StrictModel):
    id: str
    title: str
    source: str


class DebugInfo(StrictModel):
    classification_source: str
    fallback_reason: str | None = None
    selected_tool: str
    routing_decision: str
    execution_result: str


class ChatResponse(StrictModel):
    response_id: str
    receipt_token: str
    message: str = Field(min_length=1, max_length=12000)
    intent: Intent
    requires_authentication: bool
    confidence: float
    sources: list[Source] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)
    ticket: Ticket | None = None
    delivery_status: Literal["generated"] = "generated"
    debug: DebugInfo
    case_status: Literal["answered", "needs_authentication", "needs_clarification", "escalated"]
