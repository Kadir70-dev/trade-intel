"""Rich-table + plain-JSON formatters for the trader CLI (P5.3)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table


def _shorten(value: Any, width: int = 40) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_ts(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    return ts.strftime("%Y-%m-%d %H:%M")


def _fmt_score(score: float | None) -> str:
    if score is None:
        return "-"
    return f"{score:.2f}"


def print_table(
    *,
    console: Console,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    empty_message: str = "No results.",
) -> None:
    """Render a Rich table; print ``empty_message`` if no rows."""
    if not rows:
        console.print(f"[dim]{empty_message}[/dim]")
        return
    table = Table(title=title, show_lines=False, header_style="bold")
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[str(cell) if cell is not None else "-" for cell in row])
    console.print(table)


def print_json(console: Console, payload: Any) -> None:
    """Print a JSON payload as plain text (no ANSI colors).

    Using stdlib ``print`` so the output is byte-for-byte machine-parseable
    by callers (jq, CliRunner tests, downstream pipelines) — Rich's
    ``console.print_json`` would syntax-highlight via ANSI escapes.
    """
    print(json.dumps(payload, default=str, indent=2))


def messages_to_rows(messages: Iterable, *, max_body: int = 60) -> list[list[Any]]:
    """Map Message objects to row tuples for the default `hot` table."""
    rows: list[list[Any]] = []
    for m in messages:
        intent = m.intent.value if m.intent is not None else "-"
        rows.append(
            [
                m.id,
                _fmt_ts(m.message_ts),
                _shorten(m.sender_name, 20),
                intent,
                _fmt_score(m.intent_confidence),
                _shorten(m.raw_text, max_body),
            ]
        )
    return rows


def inventory_to_rows(items: Iterable) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for inv in items:
        seller_name = inv.seller.canonical_name if inv.seller is not None else "-"
        rows.append(
            [
                inv.id,
                inv.normalized_product,
                inv.quantity if inv.quantity is not None else "-",
                inv.storage or "-",
                inv.condition.value if inv.condition is not None else "-",
                inv.location or "-",
                seller_name,
                _fmt_ts(inv.posted_at),
            ]
        )
    return rows


def demand_to_rows(items: Iterable) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for dem in items:
        buyer_name = dem.buyer.canonical_name if dem.buyer is not None else "-"
        urgency = dem.urgency.value if dem.urgency is not None else "-"
        rows.append(
            [
                dem.id,
                dem.normalized_product,
                dem.quantity if dem.quantity is not None else "-",
                dem.storage or "-",
                urgency,
                dem.location or "-",
                buyer_name,
                _fmt_ts(dem.posted_at),
            ]
        )
    return rows


def matches_to_rows(matches: Iterable) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for m in matches:
        product = m.demand.normalized_product if m.demand is not None else "-"
        rows.append(
            [
                m.id,
                product,
                m.grade.value if m.grade is not None else "-",
                m.match_type.value if m.match_type is not None else "-",
                _fmt_score(m.final_score),
                _fmt_score(m.semantic_score),
                _fmt_score(m.spec_fit_score),
                _fmt_score(m.recency_multiplier),
            ]
        )
    return rows


__all__ = [
    "demand_to_rows",
    "inventory_to_rows",
    "matches_to_rows",
    "messages_to_rows",
    "print_json",
    "print_table",
]
