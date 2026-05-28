"""Pydantic request / response models for the HTTP API (P6.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trade_intel.db.models.enums import (
    Condition,
    Grade,
    IntentType,
    MatchType,
    PipelineStage,
    Priority,
    SourceType,
    Urgency,
)

# Note: from_attributes=True lets us pass SQLAlchemy ORM rows directly to
# `model_validate`; ConfigDict is the Pydantic v2 idiom.
_ORM = ConfigDict(from_attributes=True)


# ---------- shared ----------


class HealthResponse(BaseModel):
    """Liveness probe payload."""

    status: Literal["ok"] = "ok"
    version: str
    environment: str


class IngestResponse(BaseModel):
    """Returned from POST /ingest after the upload is processed."""

    source_type: SourceType
    filename: str
    matcher_scheduled: bool
    stats: dict[str, int]


# ---------- messages ----------


class MessageOut(BaseModel):
    model_config = _ORM

    id: int
    group_id: int | None = None
    sender_name: str | None = None
    sender_phone: str | None = None
    raw_text: str
    message_ts: datetime | None = None
    ingested_at: datetime
    intent: IntentType | None = None
    intent_confidence: float | None = None
    hash: str
    buyer_id: int | None = None
    seller_id: int | None = None


# ---------- sellers / buyers ----------


class SellerOut(BaseModel):
    model_config = _ORM

    id: int
    canonical_name: str
    phone: str | None = None
    aliases: list[str] | None = None
    location: str | None = None
    tags: list[str] | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    post_count: int
    trust_score: float | None = None


class BuyerOut(BaseModel):
    model_config = _ORM

    id: int
    canonical_name: str
    phone: str | None = None
    aliases: list[str] | None = None
    location: str | None = None
    tags: list[str] | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    request_count: int


class InventoryOut(BaseModel):
    model_config = _ORM

    id: int
    message_id: int | None = None
    seller_id: int | None = None
    brand: str | None = None
    product_raw: str | None = None
    normalized_product: str
    variant: str | None = None
    storage: str | None = None
    color: str | None = None
    condition: Condition | None = None
    quantity: int | None = None
    quantity_raw: str | None = None
    price_aed: float | None = None
    price_raw: str | None = None
    location: str | None = None
    posted_at: datetime | None = None
    is_active: bool


class DemandOut(BaseModel):
    model_config = _ORM

    id: int
    message_id: int | None = None
    buyer_id: int | None = None
    brand: str | None = None
    product_raw: str | None = None
    normalized_product: str
    variant: str | None = None
    storage: str | None = None
    condition: Condition | None = None
    quantity: int | None = None
    quantity_raw: str | None = None
    budget_aed: float | None = None
    location: str | None = None
    urgency: Urgency | None = None
    posted_at: datetime | None = None
    is_active: bool


class SellerDetail(SellerOut):
    """Seller record + its currently active inventory."""

    inventory_items: list[InventoryOut] = []


class BuyerDetail(BuyerOut):
    """Buyer record + their active demand history."""

    demands: list[DemandOut] = []


# ---------- match ----------


class MatchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    side: Literal["seller", "buyer"] = "seller"
    k: int = Field(default=10, ge=1, le=100)


class MatchHit(BaseModel):
    id: int
    score: float
    product: str


class MatchQueryResponse(BaseModel):
    side: Literal["seller", "buyer"]
    query: str
    hits: list[MatchHit]


class MatchOut(BaseModel):
    model_config = _ORM

    id: int
    demand_id: int | None = None
    inventory_id: int | None = None
    match_type: MatchType | None = None
    semantic_score: float | None = None
    spec_fit_score: float | None = None
    recency_multiplier: float | None = None
    evidence_weight: float | None = None
    final_score: float | None = None
    grade: Grade | None = None
    reasoning: str | None = None
    created_at: datetime


# ---------- leads ----------


class LeadOut(BaseModel):
    model_config = _ORM

    id: int
    match_id: int | None = None
    stage: PipelineStage
    priority: Priority | None = None
    close_probability: float | None = None
    last_action: str | None = None
    last_action_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class LeadAdvanceRequest(BaseModel):
    to_stage: PipelineStage
    action: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


# ---------- brief ----------


class BriefSummary(BaseModel):
    messages: int
    sellers: int
    buyers: int
    active_inventory: int
    active_demand: int
    matches: int
    new_pipeline: int


class BriefResponse(BaseModel):
    summary: BriefSummary
    top_matches: list[MatchOut]
    urgent_demand: list[DemandOut]
    fresh_inventory: list[InventoryOut]


__all__ = [
    "BriefResponse",
    "BriefSummary",
    "BuyerDetail",
    "BuyerOut",
    "DemandOut",
    "HealthResponse",
    "IngestResponse",
    "InventoryOut",
    "LeadAdvanceRequest",
    "LeadOut",
    "MatchHit",
    "MatchOut",
    "MatchQuery",
    "MatchQueryResponse",
    "MessageOut",
    "SellerDetail",
    "SellerOut",
]
