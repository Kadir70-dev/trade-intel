"""FastAPI app factory + routes (Phase 6).

Single-file API: factory builds the app, mounts every route, and wires
the API-key auth dependency. Split into multiple modules only when
total lines start hurting comprehension (~600+ here).

Auth (P6.2):
    The ``X-API-Key`` header is required when ``Settings.api_key`` is
    set. When the env var is unset (dev mode), the dep returns without
    checking. Prod environments are rejected at boot if ``api_key`` is
    None (see ``Settings._enforce_prod_safety``).

Background tasks (P6.3):
    ``POST /ingest?run_matcher=true`` schedules an inventory↔demand
    rescore after the response is sent. v1 uses FastAPI's
    ``BackgroundTasks`` (in-process); D-005 reserves a Go worker path
    for higher-throughput async ingestion later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Security,
    UploadFile,
    status,
)
from fastapi.security import APIKeyHeader
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from trade_intel import __version__
from trade_intel.api.schemas import (
    BriefResponse,
    BriefSummary,
    BuyerDetail,
    BuyerOut,
    DemandOut,
    HealthResponse,
    IngestResponse,
    InventoryOut,
    LeadAdvanceRequest,
    LeadOut,
    MatchHit,
    MatchOut,
    MatchQuery,
    MatchQueryResponse,
    MessageOut,
    SellerDetail,
    SellerOut,
)
from trade_intel.config import get_settings
from trade_intel.core.logging import configure_logging, get_logger
from trade_intel.db import get_db, session_scope
from trade_intel.db.models import (
    Buyer,
    Demand,
    IntentType,
    Inventory,
    LeadPipeline,
    Match,
    Message,
    PipelineStage,
    Priority,
    Seller,
    SourceType,
    Urgency,
)
from trade_intel.matching import build_inventory_index
from trade_intel.matching.candidates import build_demand_index
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_csv, parse_excel, parse_file
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)

log = get_logger(__name__, component="api")

# Endpoint that submits the API key. Use auto_error=False so we can run
# in dev-open mode when no key is configured.
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(submitted: str | None = Security(_API_KEY_HEADER)) -> None:
    """FastAPI dependency: enforce ``X-API-Key`` if Settings.api_key is set.

    Open in dev (when ``api_key`` is unset / empty); 401 in prod / when
    a key is configured but the header is missing or wrong.
    """
    configured = get_settings().api_key
    if not configured:
        return
    if submitted != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Header"},
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info(
        "api_startup",
        env=get_settings().environment.value,
        auth_enabled=bool(get_settings().api_key),
        version=__version__,
    )
    yield
    log.info("api_shutdown")


def create_app() -> FastAPI:
    """Build the FastAPI app — call from tests / uvicorn / docker entry."""
    app = FastAPI(
        title="trade-intel API",
        description="UAE wholesale electronics sourcing intelligence",
        version=__version__,
        lifespan=_lifespan,
    )

    # Public — no auth, used by load balancers / k8s liveness probes.
    @app.get("/healthz", response_model=HealthResponse, tags=["meta"])
    def healthz() -> HealthResponse:
        settings = get_settings()
        return HealthResponse(
            status="ok",
            version=__version__,
            environment=settings.environment.value,
        )

    # All other routers ride the auth dep.
    app.include_router(_ingest_router(), dependencies=[Depends(require_api_key)])
    app.include_router(_messages_router(), dependencies=[Depends(require_api_key)])
    app.include_router(_sellers_router(), dependencies=[Depends(require_api_key)])
    app.include_router(_buyers_router(), dependencies=[Depends(require_api_key)])
    app.include_router(_match_router(), dependencies=[Depends(require_api_key)])
    app.include_router(_leads_router(), dependencies=[Depends(require_api_key)])
    app.include_router(_brief_router(), dependencies=[Depends(require_api_key)])
    return app


# ---------------------------------------------------------------- ingest


_EXTENSION_TO_TYPE: dict[str, SourceType] = {
    ".txt": SourceType.WHATSAPP,
    ".csv": SourceType.CSV,
    ".xlsx": SourceType.EXCEL,
    ".xls": SourceType.EXCEL,
}


def _resolve_source_type(filename: str, hint: str | None) -> SourceType:
    if hint:
        try:
            return SourceType(hint.lower())
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"unknown source_type {hint!r}"
            ) from exc
    suffix = Path(filename).suffix.lower()
    fmt = _EXTENSION_TO_TYPE.get(suffix)
    if fmt is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"cannot infer source_type from extension {suffix!r}; "
                "pass source_type=whatsapp|csv|excel as a form field"
            ),
        )
    return fmt


def _matcher_background_job() -> None:
    """Rescore every active Demand against the current inventory index.

    Runs in a fresh session — :class:`BackgroundTasks` invokes after the
    HTTP response has been flushed, so the request's session is gone.
    """
    embedder = HashEmbedder()
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_scope() as session:
        index, _ = build_inventory_index(session, embedder=embedder)
        if index.size == 0:
            log.info("matcher_background_skipped_empty_index")
            return
        demands = list(
            session.scalars(
                select(Demand).where(Demand.is_active.is_(True))
            ).all()
        )
        scored = 0
        for demand in demands:
            match_demand_against_inventory(
                demand,
                session=session,
                embedder=embedder,
                inventory_index=index,
                now=now,
            )
            scored += 1
        log.info("matcher_background_completed", demands_scored=scored)


def _ingest_router() -> APIRouter:
    router = APIRouter(prefix="/ingest", tags=["ingest"])

    @router.post("", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
    async def post_ingest(
        background: BackgroundTasks,
        file: UploadFile = File(...),
        source_type: str | None = Form(default=None),
        group_name: str | None = Form(default=None),
        run_matcher: bool = Query(default=False),
    ) -> IngestResponse:
        if file.filename is None:
            raise HTTPException(status_code=400, detail="filename required")
        st = _resolve_source_type(file.filename, source_type)

        # Spool to a temp file so the parsers (which expect a path / file
        # handle that supports seek) can read it; UploadFile.file is a
        # SpooledTemporaryFile which works directly.
        contents = await file.read()
        tmp_path = Path("/tmp") / f"ingest_{datetime.now(UTC).timestamp()}_{file.filename}"
        tmp_path.write_bytes(contents)

        try:
            if st is SourceType.WHATSAPP:
                parsed = parse_file(tmp_path)
            elif st is SourceType.CSV:
                parsed = parse_csv(tmp_path)
            elif st is SourceType.EXCEL:
                parsed = parse_excel(tmp_path)
            else:  # pragma: no cover — guarded by _resolve_source_type
                raise HTTPException(status_code=400, detail=f"unsupported {st!r}")

            with session_scope() as session:
                stats = ingest_messages(
                    parsed,
                    session=session,
                    source_type=st,
                    group_name=group_name,
                    source_path=file.filename,
                )
        finally:
            tmp_path.unlink(missing_ok=True)

        if run_matcher:
            background.add_task(_matcher_background_job)

        return IngestResponse(
            source_type=st,
            filename=file.filename,
            matcher_scheduled=run_matcher,
            stats=stats.as_dict(),
        )

    return router


# ---------------------------------------------------------------- messages


def _messages_router() -> APIRouter:
    router = APIRouter(prefix="/messages", tags=["messages"])

    @router.get("", response_model=list[MessageOut])
    def list_messages(
        intent: IntentType | None = Query(default=None),
        since: datetime | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_db),
    ) -> list[MessageOut]:
        stmt = select(Message).order_by(desc(Message.message_ts))
        if intent is not None:
            stmt = stmt.where(Message.intent == intent)
        if since is not None:
            stmt = stmt.where(Message.message_ts >= since)
        rows = list(db.scalars(stmt.limit(limit).offset(offset)).all())
        return [MessageOut.model_validate(r) for r in rows]

    @router.get("/{message_id}", response_model=MessageOut)
    def get_message(message_id: int, db: Session = Depends(get_db)) -> MessageOut:
        msg = db.get(Message, message_id)
        if msg is None:
            raise HTTPException(status_code=404, detail=f"message {message_id} not found")
        return MessageOut.model_validate(msg)

    return router


# ---------------------------------------------------------------- sellers


def _sellers_router() -> APIRouter:
    router = APIRouter(prefix="/sellers", tags=["sellers"])

    @router.get("", response_model=list[SellerOut])
    def list_sellers(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_db),
    ) -> list[SellerOut]:
        rows = list(
            db.scalars(
                select(Seller)
                .order_by(desc(Seller.post_count))
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return [SellerOut.model_validate(r) for r in rows]

    @router.get("/{seller_id}", response_model=SellerDetail)
    def get_seller(seller_id: int, db: Session = Depends(get_db)) -> SellerDetail:
        seller = db.scalar(
            select(Seller)
            .where(Seller.id == seller_id)
            .options(selectinload(Seller.inventory_items))
        )
        if seller is None:
            raise HTTPException(status_code=404, detail=f"seller {seller_id} not found")
        return SellerDetail.model_validate(seller)

    return router


# ---------------------------------------------------------------- buyers


def _buyers_router() -> APIRouter:
    router = APIRouter(prefix="/buyers", tags=["buyers"])

    @router.get("", response_model=list[BuyerOut])
    def list_buyers(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_db),
    ) -> list[BuyerOut]:
        rows = list(
            db.scalars(
                select(Buyer)
                .order_by(desc(Buyer.request_count))
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return [BuyerOut.model_validate(r) for r in rows]

    @router.get("/{buyer_id}", response_model=BuyerDetail)
    def get_buyer(buyer_id: int, db: Session = Depends(get_db)) -> BuyerDetail:
        buyer = db.scalar(
            select(Buyer)
            .where(Buyer.id == buyer_id)
            .options(selectinload(Buyer.demands))
        )
        if buyer is None:
            raise HTTPException(status_code=404, detail=f"buyer {buyer_id} not found")
        return BuyerDetail.model_validate(buyer)

    return router


# ---------------------------------------------------------------- match


def _match_router() -> APIRouter:
    router = APIRouter(prefix="/match", tags=["match"])

    @router.post("", response_model=MatchQueryResponse)
    def post_match(body: MatchQuery) -> MatchQueryResponse:
        embedder = HashEmbedder()
        hits: list[MatchHit] = []
        with session_scope() as session:
            if body.side == "seller":
                index, _ = build_inventory_index(session, embedder=embedder)
            else:
                index, _ = build_demand_index(session, embedder=embedder)
            if index.size == 0:
                return MatchQueryResponse(side=body.side, query=body.query, hits=[])
            vec = embedder.encode([body.query])[0]
            raw = index.search(vec, k=body.k)
            for entity_id, score in raw:
                if body.side == "seller":
                    inv = session.get(Inventory, entity_id)
                    if inv is None:
                        continue
                    hits.append(
                        MatchHit(id=entity_id, score=score, product=inv.normalized_product)
                    )
                else:
                    dem = session.get(Demand, entity_id)
                    if dem is None:
                        continue
                    hits.append(
                        MatchHit(id=entity_id, score=score, product=dem.normalized_product)
                    )
        return MatchQueryResponse(side=body.side, query=body.query, hits=hits)

    return router


# ---------------------------------------------------------------- leads


def _leads_router() -> APIRouter:
    router = APIRouter(prefix="/leads", tags=["leads"])

    @router.get("", response_model=list[LeadOut])
    def list_leads(
        stage: PipelineStage | None = Query(default=None),
        priority: Priority | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_db),
    ) -> list[LeadOut]:
        stmt = select(LeadPipeline).order_by(
            desc(LeadPipeline.close_probability),
            desc(LeadPipeline.last_action_at),
        )
        if stage is not None:
            stmt = stmt.where(LeadPipeline.stage == stage)
        if priority is not None:
            stmt = stmt.where(LeadPipeline.priority == priority)
        rows = list(db.scalars(stmt.limit(limit).offset(offset)).all())
        return [LeadOut.model_validate(r) for r in rows]

    @router.get("/{lead_id}", response_model=LeadOut)
    def get_lead(lead_id: int, db: Session = Depends(get_db)) -> LeadOut:
        lead = db.get(LeadPipeline, lead_id)
        if lead is None:
            raise HTTPException(status_code=404, detail=f"lead {lead_id} not found")
        return LeadOut.model_validate(lead)

    @router.post("/{lead_id}/advance", response_model=LeadOut)
    def advance_lead(
        lead_id: int,
        body: LeadAdvanceRequest,
        db: Session = Depends(get_db),
    ) -> LeadOut:
        lead = db.get(LeadPipeline, lead_id)
        if lead is None:
            raise HTTPException(status_code=404, detail=f"lead {lead_id} not found")
        lead.stage = body.to_stage
        lead.last_action = body.action
        lead.last_action_at = datetime.now(UTC).replace(tzinfo=None)
        if body.notes is not None:
            lead.notes = body.notes
        db.commit()
        db.refresh(lead)
        return LeadOut.model_validate(lead)

    return router


# ---------------------------------------------------------------- brief


def _brief_router() -> APIRouter:
    router = APIRouter(prefix="/brief", tags=["brief"])

    @router.get("", response_model=BriefResponse)
    def get_brief(db: Session = Depends(get_db)) -> BriefResponse:
        message_count = db.scalar(select(func.count(Message.id))) or 0
        seller_count = db.scalar(select(func.count(Seller.id))) or 0
        buyer_count = db.scalar(select(func.count(Buyer.id))) or 0
        inventory_count = (
            db.scalar(
                select(func.count(Inventory.id)).where(
                    Inventory.is_active.is_(True)
                )
            )
            or 0
        )
        demand_count = (
            db.scalar(
                select(func.count(Demand.id)).where(Demand.is_active.is_(True))
            )
            or 0
        )
        match_count = db.scalar(select(func.count(Match.id))) or 0
        new_pipeline = (
            db.scalar(
                select(func.count(LeadPipeline.id)).where(
                    LeadPipeline.stage == PipelineStage.NEW
                )
            )
            or 0
        )

        top_matches = list(
            db.scalars(
                select(Match).order_by(desc(Match.final_score)).limit(5)
            ).all()
        )
        urgent_demand = list(
            db.scalars(
                select(Demand)
                .where(
                    Demand.is_active.is_(True),
                    Demand.urgency == Urgency.HIGH,
                )
                .order_by(desc(Demand.posted_at))
                .limit(5)
            ).all()
        )
        fresh_inventory = list(
            db.scalars(
                select(Inventory)
                .where(Inventory.is_active.is_(True))
                .order_by(desc(Inventory.posted_at))
                .limit(5)
            ).all()
        )

        return BriefResponse(
            summary=BriefSummary(
                messages=message_count,
                sellers=seller_count,
                buyers=buyer_count,
                active_inventory=inventory_count,
                active_demand=demand_count,
                matches=match_count,
                new_pipeline=new_pipeline,
            ),
            top_matches=[MatchOut.model_validate(m) for m in top_matches],
            urgent_demand=[DemandOut.model_validate(d) for d in urgent_demand],
            fresh_inventory=[InventoryOut.model_validate(i) for i in fresh_inventory],
        )

    return router


# ---------------------------------------------------------------- module-level app


# Uvicorn entry point: `uvicorn trade_intel.api.main:app`.
app = create_app()


__all__ = ["app", "create_app", "require_api_key"]
