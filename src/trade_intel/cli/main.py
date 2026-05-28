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


@app.command()
def chat(
    k: int = typer.Option(5, "-k", help="Top-K results per query"),
) -> None:
    """Simple interactive REPL — type a query, get semantic matches.

    v1 is intentionally tiny: a while-loop reading lines, running each
    through the inventory matcher. Type ``exit`` (or Ctrl-D) to quit.
    """
    embedder = HashEmbedder()
    console.print(
        "[bold green]trade-intel chat[/bold green] — "
        "type a query, [dim]exit[/dim] to quit.\n"
    )
    with session_scope() as session:
        index, _ = build_inventory_index(session, embedder=embedder)
        if index.size == 0:
            console.print(
                "[yellow]Inventory index is empty.[/yellow] "
                "Run `trade-intel parse <file>` first.\n"
            )
            return
        while True:
            try:
                line = typer.prompt(">", default="", show_default=False)
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            line = line.strip()
            if not line or line.lower() in ("exit", "quit", "/quit", "/exit"):
                break
            vec = embedder.encode([line])[0]
            hits = index.search(vec, k=k)
            rows: list = []
            for inv_id, score in hits:
                inv = session.get(Inventory, inv_id)
                if inv is None:
                    continue
                rows.append([inv_id, _fmt_score(score), inv.normalized_product])
            print_table(
                console=console,
                title=f"Top {k} for {line!r}",
                columns=["id", "score", "product"],
                rows=rows,
                empty_message="No hits.",
            )


# ---------------------------------------------------------------- run-matcher


@app.command(name="run-matcher")
def run_matcher(
    k: int = typer.Option(10, "-k"),
    min_grade: str = typer.Option("C", "--min-grade"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Score every active Demand against the current inventory index.

    Convenience wrapper around :func:`match_demand_against_inventory`
    for the whole demand table. Suitable for cron / nightly recompute.
    """
    if min_grade not in _GRADE_BY_NAME:
        raise typer.BadParameter("min-grade must be one of A+ / A / B / C")
    embedder = HashEmbedder()
    now = datetime.now(UTC).replace(tzinfo=None)
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
