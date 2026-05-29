"""Broker assistant — grounds a local LLM in the matcher's verified output.

Flow (buyer -> seller -> reasoning -> WhatsApp message):

1. Parse the free-text trader request into a transient :class:`Demand`
   (product via the normalization dictionary, qty/storage via light regex,
   urgency via the rule-based intent classifier). Nothing is persisted.
2. Build the live inventory index and pull the top-K nearest suppliers
   (:func:`find_inventory_candidates`), then score each pair with the same
   :func:`score_match` the persistence layer uses. **This is the source of
   truth.** Grades, prices, quantities all come from the DB.
3. Hand the scored, verified candidate facts to the local LLM (Ollama /
   qwen3:4b by default) for ranking narrative + margin suggestion + a
   ready-to-send WhatsApp message. The LLM may not invent anything.

If the LLM is disabled/unreachable, :meth:`answer` still returns the verified
candidate facts plus a deterministic template reply — the engine never depends
on the model being up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from trade_intel.config import get_settings
from trade_intel.core.intent import classify_intent
from trade_intel.core.logging import get_logger
from trade_intel.db.models import Demand, Inventory
from trade_intel.llm.client import LlmResult, OllamaClient, OllamaError
from trade_intel.llm.prompts import (
    SYSTEM_PROMPT,
    build_broker_prompt,
    strip_thinking,
)
from trade_intel.matching import (
    build_inventory_index,
    find_inventory_candidates,
    score_match,
)
from trade_intel.ml import HashEmbedder
from trade_intel.ml.embeddings import Embedder
from trade_intel.normalization import extract_products

log = get_logger(__name__, component="broker")

_QTY_RE = re.compile(r"(?<!\w)(\d{1,5})\s*(?:x|pcs?|units?|qty|nos?)?\b", re.I)
_STORAGE_RE = re.compile(r"(?<!\w)(\d{2,4})\s*(gb|tb)\b", re.I)

#: recency multiplier -> human freshness tier (CLAUDE.md table).
_FRESHNESS_BY_MULT: dict[float, str] = {
    1.00: "HOT",
    0.90: "ACTIVE",
    0.70: "WARM",
    0.40: "COOL",
    0.15: "STALE-recoverable",
    0.05: "STALE",
    0.50: "unknown",
}


def _extract_quantity(text: str) -> int | None:
    """First standalone number that isn't obviously a storage size."""
    storages = {m.group(1) for m in _STORAGE_RE.finditer(text)}
    for m in _QTY_RE.finditer(text):
        token = m.group(1)
        if token in storages:
            continue
        val = int(token)
        if 1 <= val <= 99999:
            return val
    return None


def _extract_storage(text: str) -> str | None:
    m = _STORAGE_RE.search(text)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2).upper()}"


def query_to_demand(text: str, *, now: datetime) -> Demand | None:
    """Turn a free-text buyer request into a transient (unpersisted) Demand."""
    products = extract_products(text)
    if not products:
        return None
    canonical = products[0].canonical
    intent = classify_intent(text)
    # Storage extraction must run before qty so '256gb' isn't read as a count.
    storage = _extract_storage(text)
    quantity = _extract_quantity(text)
    return Demand(
        normalized_product=canonical.canonical_name,
        brand=canonical.brand,
        storage=storage,
        quantity=quantity,
        urgency=intent.urgency,
        posted_at=now,
        is_active=True,
    )


@dataclass(slots=True)
class BrokerResponse:
    """Everything a caller (CLI / API / WS) needs to render one broker turn."""

    query: str
    parsed_product: str | None
    parsed_quantity: int | None
    parsed_storage: str | None
    candidates: list[dict] = field(default_factory=list)
    answer_text: str = ""
    llm_used: bool = False
    llm: LlmResult | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "parsed": {
                "product": self.parsed_product,
                "quantity": self.parsed_quantity,
                "storage": self.parsed_storage,
            },
            "candidates": self.candidates,
            "answer": self.answer_text,
            "llm_used": self.llm_used,
            "llm": self.llm.as_dict() if self.llm else None,
            "note": self.note,
        }


@dataclass(slots=True)
class BrokerAssistant:
    """Orchestrates matcher (truth) + local LLM (reasoning)."""

    session: Session
    embedder: Embedder = field(default_factory=HashEmbedder)
    client: OllamaClient | None = None
    k: int = 5

    def _candidate_facts(
        self, demand: Demand, *, now: datetime
    ) -> list[dict]:
        """Top-K scored suppliers as plain dicts — the verified ground truth."""
        index, _ = build_inventory_index(self.session, embedder=self.embedder)
        if index.size == 0:
            return []
        hits = find_inventory_candidates(
            demand, embedder=self.embedder, index=index, k=self.k
        )
        facts: list[dict] = []
        for inv_id, semantic in hits:
            inv = self.session.get(Inventory, inv_id)
            if inv is None:
                continue
            result = score_match(inv, demand, semantic_score=semantic, now=now)
            seller = inv.seller
            facts.append(
                {
                    "inventory_id": inv.id,
                    "seller": seller.canonical_name if seller else "unknown",
                    "seller_post_count": seller.post_count if seller else 0,
                    "product": inv.normalized_product,
                    "storage": inv.storage,
                    "condition": inv.condition.value if inv.condition else None,
                    "quantity": inv.quantity,
                    "price_aed": inv.price_aed,
                    "location": inv.location,
                    "freshness": _FRESHNESS_BY_MULT.get(
                        round(result.recency, 2), "?"
                    ),
                    "grade": result.grade.value,
                    "match_type": result.match_type.value,
                    "score": round(result.final, 4),
                }
            )
        facts.sort(key=lambda c: c["score"], reverse=True)
        return facts

    def _template_answer(self, candidates: list[dict]) -> str:
        """Deterministic fallback when the LLM is off/unreachable."""
        if not candidates:
            return "Found: 0 suppliers. No matching inventory — broaden the request."
        best = candidates[0]
        price = best["price_aed"]
        price_str = f"AED {price:g}" if price is not None else "not stated"
        return (
            f"Found: {len(candidates)} suppliers.\n"
            f"Best: {best['seller']} (Confidence: {best['grade']})\n"
            f"Why: {best['freshness']} stock, {best['match_type']} match, "
            f"score {best['score']:.2f}.\n"
            f"Suggested margin: review vs {price_str}/unit (suggestion only)\n"
            f'WhatsApp message:\n"Salaam {best["seller"]}, do you still have '
            f"{best['product']}? Kindly send LP and available qty.\""
        )

    def answer(self, query: str, *, now: datetime | None = None) -> BrokerResponse:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        demand = query_to_demand(query, now=now)
        if demand is None:
            return BrokerResponse(
                query=query,
                parsed_product=None,
                parsed_quantity=None,
                parsed_storage=None,
                note="No known product recognized in the request.",
                answer_text=(
                    "I couldn't recognize a product in that request. "
                    "Try e.g. 'need 20 iphone 16 pro max 256gb'."
                ),
            )
        candidates = self._candidate_facts(demand, now=now)
        resp = BrokerResponse(
            query=query,
            parsed_product=demand.normalized_product,
            parsed_quantity=demand.quantity,
            parsed_storage=demand.storage,
            candidates=candidates,
        )

        settings = get_settings()
        if not settings.llm_enabled:
            resp.answer_text = self._template_answer(candidates)
            resp.note = "LLM disabled (llm_enabled=false) — template reply."
            return resp

        client = self.client or OllamaClient()
        prompt = build_broker_prompt(query, candidates)
        try:
            result = client.generate(prompt, system=SYSTEM_PROMPT)
        except OllamaError as e:
            log.warning("llm_unavailable", error=str(e))
            resp.answer_text = self._template_answer(candidates)
            resp.note = f"LLM unavailable — template reply. ({e})"
            return resp

        resp.answer_text = strip_thinking(result.text)
        resp.llm_used = True
        resp.llm = result
        return resp


__all__ = ["BrokerAssistant", "BrokerResponse", "query_to_demand"]
