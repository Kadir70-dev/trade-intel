#!/usr/bin/env python3
"""P8.4 benchmark harness — parse / ingest / match throughput at scale.

Runs deterministic, seed-based synthetic-data workloads through the
real pipeline (parser → persistence → matcher) and reports throughput
plus peak memory.

Usage::

    python scripts/bench_p8_4.py                         # default: ingest 10k
    python scripts/bench_p8_4.py --component all --n 10000
    python scripts/bench_p8_4.py --component parse --n 100000
    python scripts/bench_p8_4.py --component match --n 5000
    python scripts/bench_p8_4.py --component dedup-heavy --n 10000
    python scripts/bench_p8_4.py --component multilingual --n 10000

Components:
    parse          parser only, no DB
    ingest         parse + ingest_messages (in-memory SQLite)
    match          ingest + score every active demand
    csv            synthesize CSV + ingest
    excel          synthesize Excel + ingest
    dedup-heavy    50% duplicate rate
    multilingual   Arabic + English + emoji mix
    all            run every component at --n

All workloads use a fixed PRNG seed (42 by default) so re-runs are
exactly reproducible. Output is one line per (component, n) sample to
stdout, plus a final summary table.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import tracemalloc
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trade_intel.db.base import Base
from trade_intel.db.models import Demand, Grade, Inventory, Message, SourceType
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_csv, parse_excel, parse_lines
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)

# ----- synthetic data -----

_PRODUCTS_EN = [
    "12pm 256gb",
    "15pm 1tb",
    "iphone 14 pro 256gb",
    "iphone 13 mini 128gb",
    "iphone 16 plus 512gb",
    "ipad mini 6 64gb",
    "ipad mini 6 256gb",
    "ipad air 5 64gb wifi",
    "ipad pro m2 256gb",
    "iphone 11 64gb",
]
_PRODUCTS_AR = [
    "iPhone 15 pm 256gb",
    "iPad Mini 6 wifi",
    "iPhone 14 pro",
    "iPad Air 5 64gb",
]
_SENDERS = [
    "Ali Mobiles DXB",
    "Khalid Trading LLC",
    "Sara DXB",
    "Ahmed (Sharjah)",
    "Mobile Hub",
    "Rashid Mobiles",
    "Bilal Electronics",
    "Yusuf Trading",
    "Ajman Wholesale",
    "Sharjah Hub",
]
_ARABIC_SENDERS = [
    "محمد عبدالله",
    "Ali Mobiles",  # mixed roster
]
_EMOJI_SENDERS = [
    "Sara DXB 🚀",
    "Mobile Hub ⚡",
    "📱 Quick Sales",
]
_PHONE_SENDERS = [
    "+971 50 555 1234",
    "+971 56 999 8877",
    "+971501234567",
]
_LOCATIONS = ["dxb", "shj", "auh", "ajman", "deira"]
_SELLER_BODIES = [
    "avail fresh seal qty {q} LP {p} AED {loc}",
    "stock ready qty {q} sealed {p} AED {loc}",
    "for sale qty {q} {p} AED {loc} pickup",
    "available {q} pcs fresh in stock {p} AED {loc}",
]
_BUYER_BODIES = [
    "need urgent {q} today asap {loc}",
    "req {q} pcs today urgent {loc}",
    "wtb {q} pcs sealed {loc}",
    "looking for {q} ready buyer here {loc}",
]
_BROKER_BODIES = [
    "looking buyer for {q} pcs commission ok {loc}",
    "bridge available - have seller for {q} pcs",
    "have buyer for {q} pcs intro fee ok",
]


def _whatsapp_lines(
    n: int,
    *,
    seed: int = 42,
    dup_rate: float = 0.0,
    multilingual: bool = False,
) -> list[str]:
    """Deterministic synthetic WhatsApp line list of length n.

    dup_rate ∈ [0, 1] — fraction of lines that are exact repeats of a
    prior line (same sender, same ts, same body → same D-007 hash).
    """
    rnd = random.Random(seed)
    base = datetime(2026, 1, 1, 9, 0)
    products = _PRODUCTS_EN + (_PRODUCTS_AR if multilingual else [])
    sender_pool = list(_SENDERS)
    if multilingual:
        sender_pool += _ARABIC_SENDERS + _EMOJI_SENDERS
    sender_pool += _PHONE_SENDERS
    lines: list[str] = []
    for i in range(n):
        if dup_rate > 0 and i > 0 and rnd.random() < dup_rate:
            lines.append(lines[rnd.randrange(len(lines))])
            continue
        ts = base + timedelta(minutes=i)
        sender = rnd.choice(sender_pool)
        prod = rnd.choice(products)
        # mix intents 60/25/15 SELLER/BUYER/BROKER.
        r = rnd.random()
        if r < 0.60:
            body = rnd.choice(_SELLER_BODIES)
        elif r < 0.85:
            body = rnd.choice(_BUYER_BODIES)
        else:
            body = rnd.choice(_BROKER_BODIES)
        body = body.format(
            q=rnd.randint(1, 80),
            p=rnd.randint(1500, 6500),
            loc=rnd.choice(_LOCATIONS),
        )
        lines.append(
            f"{ts.day:02d}/{ts.month:02d}/26, {ts.hour:02d}:{ts.minute:02d} "
            f"- {sender}: {prod} {body}"
        )
    return lines


def _csv_text(n: int, *, seed: int = 42) -> str:
    rnd = random.Random(seed)
    rows = [
        "Seller,Phone,Product,Qty,Storage,Color,Condition,Price,Location,Date"
    ]
    base = datetime(2026, 1, 1)
    storages = ["64GB", "128GB", "256GB", "512GB", "1TB"]
    conds = ["Sealed", "Fresh", "New", "Used"]
    products = [
        "iPhone 12 Pro Max",
        "iPhone 13 Pro",
        "iPhone 14 Pro",
        "iPhone 15 Pro Max",
        "iPad Mini 6",
        "iPad Air 5",
        "iPad Pro M2",
    ]
    for i in range(n):
        ts = base + timedelta(days=i // 200)
        seller = rnd.choice(_SENDERS)
        phone = f"+9715{rnd.randint(10000000, 99999999)}"
        if rnd.random() < 0.10:
            phone = ""  # 10% phone-less rows
        rows.append(
            f"{seller},{phone},{rnd.choice(products)},{rnd.randint(1, 80)},"
            f"{rnd.choice(storages)},Black,{rnd.choice(conds)},"
            f"{rnd.randint(1500, 6500)},{rnd.choice(_LOCATIONS).upper()},"
            f"{ts.strftime('%Y-%m-%d')}"
        )
    return "\n".join(rows) + "\n"


def _excel_bytes(n: int, *, seed: int = 42) -> bytes:
    """In-memory xlsx workbook with n product rows."""
    rnd = random.Random(seed)
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"
    ws.append(
        [
            "Seller",
            "Phone",
            "Product",
            "Qty",
            "Storage",
            "Condition",
            "Price",
            "Location",
            "Date",
        ]
    )
    base = datetime(2026, 1, 1)
    storages = ["64GB", "128GB", "256GB", "512GB", "1TB"]
    conds = ["Sealed", "Fresh", "New", "Used"]
    products = [
        "iPhone 12 Pro Max",
        "iPhone 14 Pro",
        "iPad Mini 6",
        "iPad Air 5",
    ]
    for i in range(n):
        ts = base + timedelta(days=i // 200)
        ws.append(
            [
                rnd.choice(_SENDERS),
                f"+9715{rnd.randint(10000000, 99999999)}",
                rnd.choice(products),
                rnd.randint(1, 80),
                rnd.choice(storages),
                rnd.choice(conds),
                rnd.randint(1500, 6500),
                rnd.choice(_LOCATIONS).upper(),
                ts.strftime("%Y-%m-%d"),
            ]
        )
    buf = BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


# ----- harness primitives -----


@dataclass(slots=True)
class BenchResult:
    component: str
    n: int
    seconds: float
    throughput: float
    peak_mb: float
    extra: dict[str, Any]

    def line(self) -> str:
        extras = " ".join(f"{k}={v}" for k, v in self.extra.items())
        return (
            f"{self.component:<14s} n={self.n:>6}  "
            f"{self.seconds:>7.2f}s  {self.throughput:>9.0f}/s  "
            f"peak={self.peak_mb:>6.1f}MB  {extras}"
        )


def _new_session() -> tuple[Any, Session]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    sm = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return eng, sm()


def _timed(component: str, n: int, fn) -> BenchResult:
    tracemalloc.start()
    t0 = time.perf_counter()
    extras = fn() or {}
    seconds = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return BenchResult(
        component=component,
        n=n,
        seconds=seconds,
        throughput=(n / seconds) if seconds > 0 else 0.0,
        peak_mb=peak / (1024 * 1024),
        extra=extras,
    )


# ----- benchmark components -----


def bench_parse(n: int, *, multilingual: bool = False) -> BenchResult:
    lines = _whatsapp_lines(n, multilingual=multilingual)

    def run():
        out = list(parse_lines(lines, source_file="bench.txt", group_name="g"))
        return {"emitted": len(out)}

    return _timed("parse", n, run)


def bench_ingest(
    n: int, *, multilingual: bool = False, dup_rate: float = 0.0
) -> BenchResult:
    lines = _whatsapp_lines(
        n, multilingual=multilingual, dup_rate=dup_rate
    )
    msgs = list(parse_lines(lines, source_file="bench.txt", group_name="g"))

    def run():
        eng, s = _new_session()
        try:
            stats = ingest_messages(
                msgs,
                session=s,
                source_type=SourceType.WHATSAPP,
                source_path="bench.txt",
            )
            s.commit()
            return {
                "inserted": stats.messages_inserted,
                "skipped_dup": stats.messages_skipped_dup,
                "inv": stats.inventory_inserted,
                "dem": stats.demand_inserted,
                "errors": stats.errors,
            }
        finally:
            s.close()
            eng.dispose()

    return _timed("ingest", n, run)


def bench_csv(n: int) -> BenchResult:
    text = _csv_text(n)
    tmp = Path("/tmp/bench_p8_4_csv.csv")
    tmp.write_text(text, encoding="utf-8")

    def run():
        eng, s = _new_session()
        try:
            stats = ingest_messages(
                parse_csv(tmp),
                session=s,
                source_type=SourceType.CSV,
                source_path=str(tmp),
            )
            s.commit()
            return {"inserted": stats.messages_inserted}
        finally:
            s.close()
            eng.dispose()

    return _timed("csv", n, run)


def bench_excel(n: int) -> BenchResult:
    data = _excel_bytes(n)
    tmp = Path("/tmp/bench_p8_4_excel.xlsx")
    tmp.write_bytes(data)

    def run():
        eng, s = _new_session()
        try:
            stats = ingest_messages(
                parse_excel(tmp),
                session=s,
                source_type=SourceType.EXCEL,
                source_path=str(tmp),
            )
            s.commit()
            return {"inserted": stats.messages_inserted}
        finally:
            s.close()
            eng.dispose()

    return _timed("excel", n, run)


def bench_match(n: int) -> BenchResult:
    """Ingest n messages, then score every active demand. n applies to
    both ingest size and demand count — actual demand count depends on
    intent classifier output (~25% of n)."""
    lines = _whatsapp_lines(n)
    msgs = list(parse_lines(lines, source_file="bench.txt", group_name="g"))

    def run():
        eng, s = _new_session()
        try:
            ingest_messages(
                msgs,
                session=s,
                source_type=SourceType.WHATSAPP,
                source_path="bench.txt",
            )
            s.commit()
            emb = HashEmbedder(dim=64)
            idx, _ = build_inventory_index(s, embedder=emb)
            demands = list(
                s.scalars(
                    select(Demand).where(Demand.is_active.is_(True))
                ).all()
            )
            t0 = time.perf_counter()
            for dem in demands:
                match_demand_against_inventory(
                    dem,
                    session=s,
                    embedder=emb,
                    inventory_index=idx,
                    k=10,
                    min_grade=Grade.C,
                    now=datetime(2026, 6, 1, 12, 0),
                )
            s.commit()
            match_only_s = time.perf_counter() - t0
            return {
                "inv_indexed": idx.size,
                "demands": len(demands),
                "match_s": f"{match_only_s:.2f}",
                "dem_per_s": (
                    f"{len(demands) / match_only_s:.0f}"
                    if match_only_s > 0
                    else "inf"
                ),
            }
        finally:
            s.close()
            eng.dispose()

    return _timed("match", n, run)


def bench_dedup_heavy(n: int) -> BenchResult:
    return _replace_component(bench_ingest(n, dup_rate=0.50), "dedup-heavy")


def bench_multilingual(n: int) -> BenchResult:
    return _replace_component(
        bench_ingest(n, multilingual=True), "multilingual"
    )


def _replace_component(r: BenchResult, name: str) -> BenchResult:
    return BenchResult(
        component=name,
        n=r.n,
        seconds=r.seconds,
        throughput=r.throughput,
        peak_mb=r.peak_mb,
        extra=r.extra,
    )


# ----- CLI -----


_COMPONENTS = {
    "parse": lambda n: bench_parse(n),
    "ingest": lambda n: bench_ingest(n),
    "csv": lambda n: bench_csv(n),
    "excel": lambda n: bench_excel(n),
    "match": lambda n: bench_match(n),
    "dedup-heavy": lambda n: bench_dedup_heavy(n),
    "multilingual": lambda n: bench_multilingual(n),
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--component",
        default="ingest",
        choices=[*_COMPONENTS.keys(), "all"],
    )
    p.add_argument("--n", type=int, default=10_000)
    p.add_argument(
        "--sizes",
        type=str,
        default=None,
        help="comma-separated list of n values (overrides --n)",
    )
    args = p.parse_args(argv)

    sizes = (
        [int(x) for x in args.sizes.split(",")]
        if args.sizes
        else [args.n]
    )
    components = (
        list(_COMPONENTS.keys()) if args.component == "all" else [args.component]
    )

    print(
        f"{'component':<14s}{'n':>10s} {'time':>10s} "
        f"{'throughput':>12s} {'memory':>11s}  extras"
    )
    results: list[BenchResult] = []
    for n in sizes:
        for c in components:
            r = _COMPONENTS[c](n)
            print(r.line())
            results.append(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
