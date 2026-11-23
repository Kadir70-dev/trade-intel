"""Dashboard JSON endpoints, SSE chat proxy and the static UI mount.

The UI shell (HTML/JS) is public but contains no data; every data route
below rides the ``X-API-Key`` dependency applied in ``create_app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from trade_intel.config import get_settings
from trade_intel.core.logging import get_logger
from trade_intel.db import get_db
from trade_intel.db.models import (
    Buyer,
    Demand,
    Grade,
    ImportRecord,
    IntentType,
    Inventory,
    Match,
    Message,
    Seller,
)

log = get_logger(__name__, component="dashboard")

_STATIC = Path(__file__).parent / "static"
_GRADE_ORDER = [Grade.A_PLUS, Grade.A, Grade.B, Grade.C]


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    session: str = Field(default="dashboard", max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


def dashboard_router() -> APIRouter:
    router = APIRouter(tags=["dashboard"])

    @router.get("/dashboard/config")
    def get_config() -> dict[str, object]:
        s = get_settings()
        return {"uploaders": s.uploader_names, "max_upload_mb": s.max_upload_mb}

    @router.get("/dashboard/summary")
    def summary(db: Session = Depends(get_db)) -> dict[str, object]:
        def count(stmt) -> int:
            return db.scalar(stmt) or 0

        grades: dict[Grade | None, int] = {
            g: n
            for g, n in db.execute(
                select(Match.grade, func.count(Match.id)).group_by(Match.grade)
            ).tuples()
        }
        grade_counts = {g.value: int(grades.get(g, 0)) for g in _GRADE_ORDER}
        intents: dict[IntentType | None, int] = {
            i: n
            for i, n in db.execute(
                select(Message.intent, func.count(Message.id)).group_by(Message.intent)
            ).tuples()
        }
        return {
            "messages": count(select(func.count(Message.id))),
            "imports": count(select(func.count(ImportRecord.id))),
            "buyers": count(select(func.count(Buyer.id))),
            "sellers": count(select(func.count(Seller.id))),
            "active_demand": count(
                select(func.count(Demand.id)).where(Demand.is_active.is_(True))
            ),
            "active_inventory": count(
                select(func.count(Inventory.id)).where(Inventory.is_active.is_(True))
            ),
            "matches": count(select(func.count(Match.id))),
            "match_grades": grade_counts,
            "intent": {
                "buyer": int(intents.get(IntentType.BUYER, 0)),
                "seller": int(intents.get(IntentType.SELLER, 0)),
                "broker": int(intents.get(IntentType.BROKER, 0)),
                "unknown": int(intents.get(IntentType.UNKNOWN, 0)),
            },
        }

    @router.get("/dashboard/imports")
    def imports(
        limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
    ) -> list[dict[str, object]]:
        rows = db.scalars(
            select(ImportRecord).order_by(desc(ImportRecord.id)).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "uploader": r.uploader,
                "filename": r.filename,
                "format": r.source_format.value,
                "status": r.status,
                "bytes": r.bytes_size,
                "seen": r.messages_seen,
                "inserted": r.messages_inserted,
                "duplicates": r.messages_skipped_dup,
                "skipped": r.messages_skipped_other,
                "inventory": r.inventory_inserted,
                "demand": r.demand_inserted,
                "matches": r.matches_written,
                "errors": r.errors,
                "error_detail": r.error_detail,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    @router.get("/dashboard/opportunities")
    def opportunities(
        limit: int = Query(default=25, ge=1, le=100),
        min_grade: Grade = Query(default=Grade.C),
        db: Session = Depends(get_db),
    ) -> list[dict[str, object]]:
        allowed = _GRADE_ORDER[: _GRADE_ORDER.index(min_grade) + 1]
        rows = db.execute(
            select(Match, Demand, Inventory, Buyer, Seller)
            .join(Demand, Match.demand_id == Demand.id)
            .join(Inventory, Match.inventory_id == Inventory.id)
            .outerjoin(Buyer, Demand.buyer_id == Buyer.id)
            .outerjoin(Seller, Inventory.seller_id == Seller.id)
            .where(Match.grade.in_(allowed))
            .order_by(desc(Match.final_score))
            .limit(limit)
        ).all()
        out: list[dict[str, object]] = []
        for rank, (m, d, inv, buyer, seller) in enumerate(rows, start=1):
            out.append(
                {
                    "rank": rank,
                    "match_id": m.id,
                    "grade": m.grade.value if m.grade else None,
                    "score": round(m.final_score or 0.0, 3),
                    "match_type": m.match_type.value if m.match_type else None,
                    "buyer": buyer.canonical_name if buyer else None,
                    "seller": seller.canonical_name if seller else None,
                    "wanted": d.normalized_product,
                    "wanted_qty": d.quantity,
                    "offered": inv.normalized_product,
                    "offered_qty": inv.quantity,
                    "price_aed": inv.price_aed,
                    "reasoning": m.reasoning,
                }
            )
        return out

    @router.post("/chat/stream")
    async def chat_stream(body: ChatRequest) -> StreamingResponse:
        """Proxy SSE from the Go broker (grounded in DB facts + local Ollama)."""
        settings = get_settings()
        url = settings.broker_url.rstrip("/") + "/chat/stream"
        client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
        try:
            upstream = await client.send(
                client.build_request(
                    "POST",
                    url,
                    json={"query": body.query, "session": body.session},
                ),
                stream=True,
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            log.warning("broker_unreachable", error=str(exc))
            raise HTTPException(status_code=502, detail="chat backend unavailable") from exc
        if upstream.status_code != 200:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail="chat backend error")

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            relay(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def mount_dashboard_ui(app: FastAPI) -> None:
    """Serve the data-free UI shell at ``/`` and ``/dashboard``."""

    @app.get("/", include_in_schema=False)
    @app.get("/dashboard", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC / "dashboard.html", headers={"Cache-Control": "no-store"})

    @app.get("/static/dashboard.js", include_in_schema=False)
    def script() -> FileResponse:
        return FileResponse(
            _STATIC / "dashboard.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )
