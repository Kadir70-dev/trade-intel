"""``trade-intel`` Typer app (P5.1 + P5.2 + P5.3 + P5.4).

Commands::

    trade-intel parse <file> [--type whatsapp|csv|excel] [--group NAME]
    trade-intel match "<query>" [--side seller|buyer] [-k N]
    trade-intel who-has "<product>" [--limit N] [--location X]
    trade-intel who-needs "<product>" [--limit N]
    trade-intel hot [--limit N] [--intent seller|buyer|broker|unknown]
    trade-intel opportunities [--limit N] [--grade A+|A|B|C] [--priority P1..P4]
    trade-intel brief
    trade-intel ping <match-id> [--side seller|buyer]
    trade-intel chat        # simple REPL

All commands take ``--json`` to emit machine-readable JSON instead of
the default rich-table output. The CLI assumes ``make db-upgrade`` has
been run; commands that read the DB will error helpfully if tables are
missing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from sqlalchemy import desc, func, select

from trade_intel.cli.formatters import (
    demand_to_rows,
    inventory_to_rows,
    matches_to_rows,
    messages_to_rows,
    print_json,
    print_table,
)
from trade_intel.cli.messages import render_match_message
from trade_intel.db import session_scope
from trade_intel.db.models import (
    Buyer,
    Demand,
    Grade,
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
from trade_intel.llm import BrokerAssistant
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_csv, parse_excel, parse_file
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)

app = typer.Typer(
    name="trade-intel",
    help="UAE wholesale electronics sourcing intelligence CLI.",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


# ---------------------------------------------------------------- parse


_EXTENSION_TO_TYPE: dict[str, str] = {
    ".txt": "whatsapp",
    ".csv": "csv",
    ".xlsx": "excel",
    ".xls": "excel",
}


def _resolve_format(path: Path, hint: str | None) -> str:
    if hint:
        return hint.lower()
    fmt = _EXTENSION_TO_TYPE.get(path.suffix.lower())
    if fmt is None:
        raise typer.BadParameter(
            f"cannot infer format from extension {path.suffix!r}; "
            "use --type {whatsapp|csv|excel}"
        )
    return fmt


@app.command()
def parse(
    file: Path = typer.Argument(..., exists=True, readable=True),
    type_: str | None = typer.Option(
        None, "--type", "-t", help="whatsapp | csv | excel (default: by extension)"
    ),
    group: str | None = typer.Option(
        None, "--group", "-g", help="Override group name (default: file stem)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of table"),
) -> None:
    """Ingest a WhatsApp / CSV / Excel file into the database."""
    fmt = _resolve_format(file, type_)
    if fmt == "whatsapp":
        source_type = SourceType.WHATSAPP
        parsed = parse_file(file)
    elif fmt == "csv":
        source_type = SourceType.CSV
        parsed = parse_csv(file)
    elif fmt == "excel":
        source_type = SourceType.EXCEL
        parsed = parse_excel(file)
    else:
        raise typer.BadParameter(f"unknown format {fmt!r}")

    with session_scope() as session:
        stats = ingest_messages(
            parsed,
            session=session,
            source_type=source_type,
            group_name=group,
            source_path=str(file),
        )
    payload = stats.as_dict()
    # Inventory changed → invalidate the broker reply cache (best-effort).
    if payload.get("inventory_inserted") or payload.get("demand_inserted"):
        _invalidate_broker_cache()
    if as_json:
        print_json(console, payload)
        return
    console.print(
        f"[bold]{file.name}[/bold] · {source_type.value} · "
        f"[green]{payload['messages_inserted']}[/green] inserted "
        f"([yellow]{payload['messages_skipped_dup']}[/yellow] dup, "
        f"{payload['errors']} err)"
    )
    print_table(
        console=console,
        title="Ingest counters",
        columns=["metric", "value"],
        rows=[[k, v] for k, v in payload.items()],
    )


# ---------------------------------------------------------------- ocr


@app.command()
def ocr(
    file: Path = typer.Argument(..., exists=True, readable=True),
    seller: str | None = typer.Option(
        None, "--seller", "-s", help="Dealer the stock is attributed to"
    ),
    engine: str | None = typer.Option(
        None, "--engine", help="paddleocr | rapidocr | tesseract (default: auto)"
    ),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help="auto (insert High+Medium, hold Low) | review (insert none) | all",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """OCR a stock image / PDF (or parse Excel / CSV) into inventory rows.

    Local OCR only (PaddleOCR → RapidOCR → Tesseract). High/Medium-confidence
    rows are auto-inserted; Low-confidence rows are held for human review.
    """
    from trade_intel.ocr import import_stock

    with session_scope() as session:
        result = import_stock(
            file, session=session, seller=seller, prefer_engine=engine, mode=mode
        )
    payload = result.as_dict()
    if result.inserted:
        _invalidate_broker_cache()  # inventory changed → drop stale broker cache

    if as_json:
        print_json(console, payload)
        return

    console.print(
        f"[bold]{file.name}[/bold] · {result.file_type} · engine="
        f"[cyan]{result.engine}[/cyan] · {result.lines_total} lines → "
        f"{result.rows_extracted} rows "
        f"([green]{result.counts.get('high', 0)} High[/green] / "
        f"[yellow]{result.counts.get('medium', 0)} Medium[/yellow] / "
        f"[red]{result.counts.get('low', 0)} Low[/red]) · "
        f"[bold green]{result.inserted} inserted[/bold green]"
    )
    if result.rows:
        print_table(
            console=console,
            title="Extracted inventory",
            columns=["conf", "product", "qty", "price", "storage", "loc", "moq", "raw"],
            rows=[
                [
                    r.confidence,
                    r.product or "—",
                    str(r.quantity) if r.quantity is not None else "—",
                    f"{r.price_aed:g}" if r.price_aed is not None else "—",
                    r.storage or "—",
                    r.location or "—",
                    str(r.moq) if r.moq is not None else "—",
                    (r.raw_text[:32] + "…") if len(r.raw_text) > 33 else r.raw_text,
                ]
                for r in result.rows
            ],
        )
    if result.pending_review:
        console.print(
            f"[yellow]⚠ {len(result.pending_review)} low-confidence row(s) "
            f"held for review — re-run with --mode all to import.[/yellow]"
        )


# ---------------------------------------------------------------- trust


@app.command()
def trust(
    recompute: bool = typer.Option(
        True, "--recompute/--no-recompute", help="Recompute + persist trust scores"
    ),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Supplier trust leaderboard (0-100, A+/A/B/C/Risky).

    Deterministic reputation from real behaviour — freshness, activity, LP
    availability, quantity reliability, repeat listings, successful matches,
    duplicate-spam + ghost penalties. Persisted to ``sellers.trust_score`` and
    used (heavily) by the broker ranking. Cron-friendly like ``run-matcher``.
    """
    from datetime import UTC, datetime

    from trade_intel.matching.trust import compute_trust, recompute_all

    now = datetime.now(UTC).replace(tzinfo=None)
    with session_scope() as session:
        if recompute:
            scores = recompute_all(session, now=now)
        else:
            from trade_intel.db.models import Seller

            sellers = (
                session.query(Seller)
                .order_by(Seller.trust_score.desc().nullslast())
                .all()
            )
            scores = [compute_trust(s, session=session, now=now) for s in sellers]
            scores.sort(key=lambda t: t.score, reverse=True)

        spread = {g: sum(1 for t in scores if t.grade == g) for g in ("A+", "A", "B", "C", "Risky")}
        top = scores[:limit]
        if as_json:
            print_json(
                console,
                {"recomputed": recompute, "total": len(scores), "spread": spread,
                 "leaderboard": [t.as_dict() for t in top]},
            )
            return
        console.print(
            f"[bold]Supplier trust[/bold] · {len(scores)} sellers · "
            + " ".join(f"{g}={spread[g]}" for g in ("A+", "A", "B", "C", "Risky"))
            + ("  [dim](recomputed)[/dim]" if recompute else "")
        )
        print_table(
            console=console,
            title="Trust leaderboard",
            columns=["grade", "score", "seller", "why"],
            rows=[
                [t.grade, f"{t.score:.0f}", t.seller[:30], ", ".join(t.reasons[:3])]
                for t in top
            ],
        )


# ---------------------------------------------------------------- match


@app.command()
def match(
    query: str = typer.Argument(..., help="Free-text query (e.g. '12pm 256gb')"),
    side: str = typer.Option(
        "seller",
        "--side",
        "-s",
        help="seller: search inventory; buyer: search demand",
    ),
    k: int = typer.Option(10, "-k", help="Top-K candidates to surface"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Semantic lookup against the inventory or demand vector index."""
    if side not in ("seller", "buyer"):
        raise typer.BadParameter("--side must be 'seller' or 'buyer'")
    embedder = HashEmbedder()
    with session_scope() as session:
        if side == "seller":
            index, _ = build_inventory_index(session, embedder=embedder)
        else:
            from trade_intel.matching.candidates import build_demand_index

            index, _ = build_demand_index(session, embedder=embedder)
        if index.size == 0:
            console.print("[dim]Index is empty — ingest some data first.[/dim]")
            return
        vec = embedder.encode([query])[0]
        hits = index.search(vec, k=k)

        rows: list = []
        json_payload: list[dict] = []
        for entity_id, score in hits:
            product: str | None = None
            if side == "seller":
                inv = session.get(Inventory, entity_id)
                if inv is None:
                    continue
                product = inv.normalized_product
            else:
                dem = session.get(Demand, entity_id)
                if dem is None:
                    continue
                product = dem.normalized_product
            rows.append([entity_id, _fmt_score(score), product])
            json_payload.append(
                {"id": entity_id, "score": round(score, 4), "product": product}
            )
    if as_json:
        print_json(console, {"side": side, "query": query, "hits": json_payload})
        return
    print_table(
        console=console,
        title=f"Top {k} {side} matches for {query!r}",
        columns=["id", "score", "product"],
        rows=rows,
        empty_message="No matches — try a different query.",
    )


def _fmt_score(s: float) -> str:
    return f"{s:.3f}"


# ---------------------------------------------------------------- who-has / who-needs


@app.command(name="who-has")
def who_has(
    product: str = typer.Argument(..., help="Canonical product name"),
    limit: int = typer.Option(20, "--limit", "-n"),
    location: str | None = typer.Option(None, "--location", "-l"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List active Inventory rows matching ``product`` (case-insensitive)."""
    with session_scope() as session:
        stmt = (
            select(Inventory)
            .where(
                Inventory.is_active.is_(True),
                func.lower(Inventory.normalized_product) == product.lower(),
            )
            .order_by(desc(Inventory.posted_at))
            .limit(limit)
        )
        if location:
            stmt = stmt.where(
                func.lower(Inventory.location) == location.lower()
            )
        items = list(session.scalars(stmt).all())
        rows = inventory_to_rows(items)
    if as_json:
        print_json(
            console,
            [
                {
                    "id": r[0],
                    "product": r[1],
                    "qty": r[2],
                    "storage": r[3],
                    "condition": r[4],
                    "location": r[5],
                    "seller": r[6],
                    "posted_at": str(r[7]),
                }
                for r in rows
            ],
        )
        return
    print_table(
        console=console,
        title=f"Inventory for {product!r}",
        columns=["id", "product", "qty", "storage", "cond", "loc", "seller", "posted"],
        rows=rows,
        empty_message=f"No inventory found for {product!r}.",
    )


@app.command(name="who-needs")
def who_needs(
    product: str = typer.Argument(..., help="Canonical product name"),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List active Demand rows for ``product`` (case-insensitive)."""
    with session_scope() as session:
        stmt = (
            select(Demand)
            .where(
                Demand.is_active.is_(True),
                func.lower(Demand.normalized_product) == product.lower(),
            )
            .order_by(desc(Demand.posted_at))
            .limit(limit)
        )
        items = list(session.scalars(stmt).all())
        rows = demand_to_rows(items)
    if as_json:
        print_json(
            console,
            [
                {
                    "id": r[0],
                    "product": r[1],
                    "qty": r[2],
                    "storage": r[3],
                    "urgency": r[4],
                    "location": r[5],
                    "buyer": r[6],
                    "posted_at": str(r[7]),
                }
                for r in rows
            ],
        )
        return
    print_table(
        console=console,
        title=f"Demand for {product!r}",
        columns=["id", "product", "qty", "storage", "urg", "loc", "buyer", "posted"],
        rows=rows,
        empty_message=f"No demand found for {product!r}.",
    )


# ---------------------------------------------------------------- hot


_INTENT_BY_NAME: dict[str, IntentType] = {
    "seller": IntentType.SELLER,
    "buyer": IntentType.BUYER,
    "broker": IntentType.BROKER,
    "unknown": IntentType.UNKNOWN,
}


@app.command()
def hot(
    limit: int = typer.Option(20, "--limit", "-n"),
    intent: str | None = typer.Option(
        None, "--intent", "-i", help="seller | buyer | broker | unknown"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Most recent messages (HOT freshness tier)."""
    with session_scope() as session:
        stmt = select(Message).order_by(desc(Message.message_ts)).limit(limit)
        if intent is not None:
            ekey = intent.lower()
            if ekey not in _INTENT_BY_NAME:
                raise typer.BadParameter(
                    "intent must be seller / buyer / broker / unknown"
                )
            stmt = stmt.where(Message.intent == _INTENT_BY_NAME[ekey])
        msgs = list(session.scalars(stmt).all())
        rows = messages_to_rows(msgs)
    if as_json:
        print_json(
            console,
            [
                {
                    "id": r[0],
                    "ts": r[1],
                    "sender": r[2],
                    "intent": r[3],
                    "confidence": r[4],
                    "body": r[5],
                }
                for r in rows
            ],
        )
        return
    print_table(
        console=console,
        title="Hot messages",
        columns=["id", "ts", "sender", "intent", "conf", "body"],
        rows=rows,
        empty_message="No messages yet — try `trade-intel parse <file>`.",
    )


# ---------------------------------------------------------------- opportunities


_GRADE_BY_NAME: dict[str, Grade] = {
    "A+": Grade.A_PLUS,
    "A": Grade.A,
    "B": Grade.B,
    "C": Grade.C,
}

_PRIORITY_BY_NAME: dict[str, Priority] = {
    "P1": Priority.P1,
    "P2": Priority.P2,
    "P3": Priority.P3,
    "P4": Priority.P4,
}


@app.command()
def opportunities(
    limit: int = typer.Option(20, "--limit", "-n"),
    grade: str | None = typer.Option(None, "--grade", "-g"),
    priority: str | None = typer.Option(None, "--priority", "-p"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Top match opportunities, ranked by final score."""
    with session_scope() as session:
        stmt = select(Match).order_by(desc(Match.final_score)).limit(limit)
        if grade is not None:
            if grade not in _GRADE_BY_NAME:
                raise typer.BadParameter("grade must be one of A+ / A / B / C")
            stmt = stmt.where(Match.grade == _GRADE_BY_NAME[grade])
        if priority is not None:
            if priority not in _PRIORITY_BY_NAME:
                raise typer.BadParameter("priority must be one of P1..P4")
            stmt = (
                stmt.join(LeadPipeline, LeadPipeline.match_id == Match.id)
                .where(LeadPipeline.priority == _PRIORITY_BY_NAME[priority])
            )
        matches = list(session.scalars(stmt).all())
        rows = matches_to_rows(matches)
    if as_json:
        print_json(
            console,
            [
                {
                    "id": r[0],
                    "product": r[1],
                    "grade": r[2],
                    "type": r[3],
                    "final": r[4],
                    "semantic": r[5],
                    "spec_fit": r[6],
                    "recency": r[7],
                }
                for r in rows
            ],
        )
        return
    print_table(
        console=console,
        title="Top opportunities",
        columns=["id", "product", "grade", "type", "final", "sem", "spec", "rec"],
        rows=rows,
        empty_message=(
            "No matches yet — try `trade-intel parse` then re-run the matcher."
        ),
    )


# ---------------------------------------------------------------- brief


@app.command()
def brief(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Daily Deal Board — counts + top matches + urgent buyers + fresh inventory."""
    with session_scope() as session:
        message_count = session.scalar(select(func.count(Message.id))) or 0
        seller_count = session.scalar(select(func.count(Seller.id))) or 0
        buyer_count = session.scalar(select(func.count(Buyer.id))) or 0
        inventory_count = (
            session.scalar(
                select(func.count(Inventory.id)).where(
                    Inventory.is_active.is_(True)
                )
            )
            or 0
        )
        demand_count = (
            session.scalar(
                select(func.count(Demand.id)).where(Demand.is_active.is_(True))
            )
            or 0
        )
        match_count = session.scalar(select(func.count(Match.id))) or 0

        top_matches = list(
            session.scalars(
                select(Match)
                .order_by(desc(Match.final_score))
                .limit(5)
            ).all()
        )
        urgent_demands = list(
            session.scalars(
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
            session.scalars(
                select(Inventory)
                .where(Inventory.is_active.is_(True))
                .order_by(desc(Inventory.posted_at))
                .limit(5)
            ).all()
        )
        new_pipeline = (
            session.scalar(
                select(func.count(LeadPipeline.id)).where(
                    LeadPipeline.stage == PipelineStage.NEW
                )
            )
            or 0
        )

        # Materialize all formatted rows INSIDE the session — relationships
        # (e.g. inv.seller.canonical_name) lazy-load on attribute access and
        # would raise DetachedInstanceError once session_scope closes.
        summary = {
            "messages": message_count,
            "sellers": seller_count,
            "buyers": buyer_count,
            "active_inventory": inventory_count,
            "active_demand": demand_count,
            "matches": match_count,
            "new_pipeline": new_pipeline,
        }
        top_match_rows = matches_to_rows(top_matches)
        urgent_demand_rows = demand_to_rows(urgent_demands)
        fresh_inventory_rows = inventory_to_rows(fresh_inventory)

    if as_json:
        print_json(
            console,
            {
                "summary": summary,
                "top_matches": top_match_rows,
                "urgent_demand": urgent_demand_rows,
                "fresh_inventory": fresh_inventory_rows,
            },
        )
        return

    console.rule("[bold]Daily Deal Board[/bold]")
    print_table(
        console=console,
        title="Summary",
        columns=["metric", "value"],
        rows=[[k, v] for k, v in summary.items()],
    )
    print_table(
        console=console,
        title="Top matches",
        columns=["id", "product", "grade", "type", "final", "sem", "spec", "rec"],
        rows=top_match_rows,
        empty_message="No matches scored yet.",
    )
    print_table(
        console=console,
        title="Urgent demand",
        columns=["id", "product", "qty", "storage", "urg", "loc", "buyer", "posted"],
        rows=urgent_demand_rows,
        empty_message="No HIGH-urgency demand at this time.",
    )
    print_table(
        console=console,
        title="Fresh inventory",
        columns=["id", "product", "qty", "storage", "cond", "loc", "seller", "posted"],
        rows=fresh_inventory_rows,
        empty_message="No active inventory.",
    )


# ---------------------------------------------------------------- ping


@app.command()
def ping(
    match_id: int = typer.Argument(..., help="Match ID to generate a message for"),
    side: str = typer.Option(
        "seller",
        "--side",
        "-s",
        help="seller (ping the inventory holder) | buyer (pitch to the demand)",
    ),
) -> None:
    """Generate a WhatsApp-ready ping for a Match row (P5.4)."""
    if side not in ("seller", "buyer"):
        raise typer.BadParameter("--side must be 'seller' or 'buyer'")
    with session_scope() as session:
        match = session.get(Match, match_id)
        if match is None:
            console.print(f"[red]Match {match_id} not found.[/red]")
            raise typer.Exit(code=1)
        text = render_match_message(match, side=side)
    console.print(text)


# ---------------------------------------------------------------- chat


def _render_broker(resp, *, as_json: bool) -> None:
    """Print one :class:`BrokerResponse` to the console."""
    if as_json:
        print_json(console, resp.as_dict())
        return
    if resp.note:
        console.print(f"[dim]{resp.note}[/dim]")
    console.print(resp.answer_text)
    if resp.llm is not None:
        meta = resp.llm
        console.print(
            f"\n[dim]· {meta.model} · {meta.tokens_per_second:.1f} tok/s · "
            f"{meta.total_duration_s:.1f}s · {meta.completion_tokens} tokens[/dim]"
        )


@app.command()
def ask(
    query: str = typer.Argument(..., help="Free-text trader request"),
    k: int = typer.Option(5, "-k", help="Top-K suppliers to consider"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One-shot AI broker: buyer request -> ranked suppliers -> WhatsApp draft.

    The matcher is the source of truth; the local LLM (Ollama) only reasons
    over the verified candidate facts. Falls back to a deterministic template
    reply if the model is unreachable.
    """
    embedder = HashEmbedder()
    with session_scope() as session:
        assistant = BrokerAssistant(session=session, embedder=embedder, k=k)
        resp = assistant.answer(query)
    _render_broker(resp, as_json=as_json)


def _go_broker_bin() -> Path | None:
    """Locate the Go broker binary (the primary backend).

    Resolution order: ``TRADE_INTEL_BROKER_BIN`` env var, then ``bin/broker``
    at the project root. Returns None when not built so the CLI can fall back
    to the in-process Python REPL.
    """
    import os

    from trade_intel.config import PROJECT_ROOT

    override = os.environ.get("TRADE_INTEL_BROKER_BIN")
    candidates = [Path(override)] if override else []
    candidates.append(PROJECT_ROOT / "bin" / "broker")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    return None


_DEFAULT_BROKER_URL = "http://localhost:8088"


def _invalidate_broker_cache() -> None:
    """Best-effort: tell a running Go broker server its cache is stale after an
    inventory change. Silent no-op if no server is reachable."""
    import os
    import urllib.request

    url = (os.environ.get("BROKER_URL") or _DEFAULT_BROKER_URL).rstrip("/")
    try:
        req = urllib.request.Request(
            url + "/cache/invalidate", data=b"", method="POST"
        )
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception:
        pass  # broker not running / cache off — ingestion still succeeded


def _broker_health(url: str, *, timeout: float = 1.5) -> dict | None:
    """Probe ``GET {url}/healthz``. Returns the parsed payload, or None if the
    broker server isn't reachable."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _sse_chat_turn(
    url: str, session_id: str, query: str, model: str | None, *, timeout: float
) -> dict | None:
    """Stream one turn from the broker server's SSE endpoint.

    Prints token chunks live as they arrive (interactive feel) and returns the
    final ``done`` payload (full Response) for the footer, or None on error.
    """
    import json
    import urllib.request

    payload: dict = {"session": session_id, "query": query}
    if model:
        payload["model"] = model
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/stream",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )

    event: str | None = None
    data_lines: list[str] = []
    final: dict | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line == "":  # blank line = dispatch the accumulated event
                    data = "\n".join(data_lines)
                    if event == "status":
                        # connection established; model is evaluating (CPU TTFT)
                        console.print("[dim]…generating[/dim]")
                    elif event == "token":
                        print(data, end="", flush=True)
                    elif event == "error":
                        console.print(f"\n[red]broker error:[/red] {data}")
                    elif event == "done":
                        try:
                            final = json.loads(data)
                        except Exception:
                            final = None
                    event, data_lines = None, []
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    d = line[5:]
                    if d[:1] == " ":  # SSE: strip exactly one leading space
                        d = d[1:]
                    data_lines.append(d)
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as e:  # network / server failure mid-stream
        console.print(f"\n[red]stream failed:[/red] {e}")
        return None
    print()  # newline after the streamed answer
    return final


def _run_http_chat(url: str, model: str | None, health: dict, k: int) -> None:
    """Interactive REPL that talks to the running Go broker server over HTTP."""
    import os

    eff = health.get("effective") or model or health.get("model") or "?"
    status = health.get("status", "ok")
    console.print(
        f"[bold green]trade-intel chat[/bold green] — UAE wholesale AI broker "
        f"[dim](Go backend @ {url} · model={eff})[/dim]"
    )
    if status == "using_fallback":
        console.print(
            f"[yellow]Primary model not pulled — auto-using fallback "
            f"'{health.get('fallback')}'.[/yellow]"
        )
    elif status == "no_model_available":
        console.print(
            f"[red]Neither '{health.get('model')}' nor '{health.get('fallback')}' "
            f"is pulled. Run e.g. `ollama pull {health.get('model')}`. "
            f"Replies will use the deterministic template until then.[/red]"
        )
    elif status == "ollama_unreachable":
        console.print("[red]Ollama is unreachable — replies use the template fallback.[/red]")
    console.print("[dim]exit[/dim] to quit, [dim]/reset[/dim] to clear memory.\n")

    # Generous client timeout: CPU inference is slow and time-to-first-token can
    # be minutes on a contended laptop. Match the server default (240s) plus
    # headroom so the client never gives up before the server does.
    try:
        llm_timeout = float(os.environ.get("LLM_TIMEOUT", "240"))
    except ValueError:
        llm_timeout = 240.0
    timeout = llm_timeout + 60.0

    session_id, reset_n = "cli", 0
    while True:
        try:
            line = typer.prompt(">", default="", show_default=False)
        except (EOFError, KeyboardInterrupt, typer.Abort):
            console.print()
            break
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("exit", "quit", "/quit", "/exit"):
            break
        if line.lower() == "/reset":
            reset_n += 1
            session_id = f"cli-{reset_n}"  # fresh server-side history
            console.print("[dim][memory cleared][/dim]")
            continue
        final = _sse_chat_turn(url, session_id, line, model, timeout=timeout)
        if final:
            note = final.get("note")
            if note:
                console.print(f"[dim]{note}[/dim]")
            if final.get("llm_used"):
                console.print(
                    f"[dim]· {final.get('model')} · "
                    f"{final.get('tokens_per_sec', 0):.1f} tok/s[/dim]"
                )
        console.print()


@app.command()
def chat(
    k: int = typer.Option(5, "-k", help="Top-K suppliers per query"),
) -> None:
    """Interactive AI broker REPL.

    Resolution order:

    1. **HTTP client** to a running Go broker server (``make broker-serve``) at
       ``BROKER_URL`` (default ``http://localhost:8088``) — streams over SSE.
    2. **Local Go binary** (``bin/broker``) if no server is reachable.
    3. **In-process Python REPL** if the Go binary isn't built.

    The model is chosen via ``OLLAMA_MODEL`` (or ``LLM_MODEL``); the server
    default is ``llama3.2:3b`` with ``qwen3:4b`` as fallback.

    Each line is a trader request. Type ``exit`` (or Ctrl-D) to quit.
    """
    import os

    broker_url_env = os.environ.get("BROKER_URL")
    broker_url = broker_url_env or _DEFAULT_BROKER_URL
    model = os.environ.get("OLLAMA_MODEL") or os.environ.get("LLM_MODEL")

    # 1. Prefer a running broker server (HTTP + SSE streaming).
    health = _broker_health(broker_url)
    if health is not None:
        _run_http_chat(broker_url, model, health, k)
        return
    if broker_url_env:
        console.print(
            f"[yellow]Broker server at {broker_url} not reachable — "
            f"falling back to local backend.[/yellow]"
        )

    # 2. Local Go binary: hand off the whole REPL to it.
    go_bin = _go_broker_bin()
    if go_bin is not None:
        env = dict(os.environ)
        env.setdefault("TRADE_INTEL_TOPK", str(k))
        os.execve(str(go_bin), [str(go_bin), "chat"], env)  # replaces process

    # 3. In-process Python REPL (no Go backend built).
    embedder = HashEmbedder()
    console.print(
        "[dim]No broker server or Go binary found (try `make broker-serve`); "
        "using in-process Python REPL.[/dim]\n"
        "[bold green]trade-intel chat[/bold green] — UAE wholesale AI broker. "
        "[dim]exit[/dim] to quit.\n"
    )
    with session_scope() as session:
        assistant = BrokerAssistant(session=session, embedder=embedder, k=k)
        while True:
            try:
                line = typer.prompt(">", default="", show_default=False)
            except (EOFError, KeyboardInterrupt, typer.Abort):
                console.print()
                break
            line = line.strip()
            if not line or line.lower() in ("exit", "quit", "/quit", "/exit"):
                break
            resp = assistant.answer(line)
            _render_broker(resp, as_json=False)
            console.print()


# ---------------------------------------------------------------- run-matcher


@app.command(name="run-matcher")
def run_matcher(
    k: int = typer.Option(10, "-k"),
    min_grade: str = typer.Option("C", "--min-grade"),
    now_str: str | None = typer.Option(
        None,
        "--now",
        help=(
            "Anchor recency to this ISO timestamp instead of wall-clock now. "
            "Use 'auto' to anchor to max(Inventory.posted_at) — handy when "
            "scoring a historical export so recency doesn't STALE every row."
        ),
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Score every active Demand against the current inventory index.

    Convenience wrapper around :func:`match_demand_against_inventory`
    for the whole demand table. Suitable for cron / nightly recompute.
    """
    if min_grade not in _GRADE_BY_NAME:
        raise typer.BadParameter("min-grade must be one of A+ / A / B / C")
    embedder = HashEmbedder()
    if now_str is None:
        now = datetime.now(UTC).replace(tzinfo=None)
    elif now_str == "auto":
        now = None  # resolved per-session below
    else:
        try:
            now = datetime.fromisoformat(now_str).replace(tzinfo=None)
        except ValueError as e:
            raise typer.BadParameter(
                f"--now must be 'auto' or an ISO-8601 timestamp ({e})"
            ) from e
    total = {
        "demands_scored": 0,
        "matches_created": 0,
        "matches_updated": 0,
        "lead_pipelines_created": 0,
        "lead_pipelines_touched": 0,
        "matches_below_threshold": 0,
    }
    with session_scope() as session:
        index, _ = build_inventory_index(session, embedder=embedder)
        if index.size == 0:
            console.print("[yellow]Inventory index is empty.[/yellow]")
            return
        if now is None:  # --now=auto
            anchor = session.scalar(select(func.max(Inventory.posted_at)))
            now = anchor or datetime.now(UTC).replace(tzinfo=None)
        demands = list(
            session.scalars(
                select(Demand).where(Demand.is_active.is_(True))
            ).all()
        )
        for demand in demands:
            stats = match_demand_against_inventory(
                demand,
                session=session,
                embedder=embedder,
                inventory_index=index,
                k=k,
                min_grade=_GRADE_BY_NAME[min_grade],
                now=now,
            )
            total["demands_scored"] += 1
            total["matches_created"] += stats.matches_created
            total["matches_updated"] += stats.matches_updated
            total["lead_pipelines_created"] += stats.lead_pipelines_created
            total["lead_pipelines_touched"] += stats.lead_pipelines_touched
            total["matches_below_threshold"] += stats.matches_below_threshold
    if as_json:
        print_json(console, total)
        return
    print_table(
        console=console,
        title="Matcher run",
        columns=["metric", "value"],
        rows=[[k, v] for k, v in total.items()],
    )


__all__ = ["app"]
