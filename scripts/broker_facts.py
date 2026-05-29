#!/usr/bin/env python3
"""ML/NLP bridge for the Go broker backend — emits verified candidate facts.

This is the *only* job Python does in the chat path: the ML/NLP source of
truth. The Go backend shells out to this script with a free-text trader
request and gets back a compact JSON document of scored supplier candidates.
Go then handles all orchestration, LLM reasoning (Ollama), memory, streaming,
and response formatting.

Contract (CLAUDE.md: "database facts always win, no hallucination"):
- The matcher (semantic index + ``score_match``) is the source of truth.
- This script NEVER calls the LLM and NEVER invents data — it only reads the
  DB, scores candidates, and prints them.

Usage::

    python scripts/broker_facts.py "need 20 iphone 16pm urgent" [--k N]

Output (stdout, one JSON object)::

    {
      "ok": true,
      "product": "iPhone 16 Pro Max" | null,
      "quantity": 20 | null,
      "storage": "256GB" | null,
      "urgency": "HIGH" | null,
      "candidates": [ { ... per-supplier facts ... }, ... ]
    }

On a recognized-but-empty result, ``candidates`` is ``[]`` and ``ok`` stays
true. On a hard failure, prints ``{"ok": false, "error": "..."}`` to stdout
and exits non-zero so the Go side can degrade gracefully.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Emit verified broker candidate facts as JSON.")
    parser.add_argument("query", help="Free-text trader request")
    parser.add_argument("--k", type=int, default=5, help="Top-K suppliers to consider")
    args = parser.parse_args(argv)

    # Imported lazily so --help stays fast and import errors surface as JSON.
    try:
        from trade_intel.db import session_scope
        from trade_intel.llm.broker import query_to_demand
        from trade_intel.llm.ranking import rank_suppliers
    except Exception as exc:  # pragma: no cover - environment guard
        json.dump({"ok": False, "error": f"import failed: {exc}"}, sys.stdout)
        return 2

    now = datetime.now(UTC).replace(tzinfo=None)

    try:
        demand = query_to_demand(args.query, now=now)
        if demand is None:
            json.dump(
                {
                    "ok": True,
                    "product": None,
                    "quantity": None,
                    "storage": None,
                    "urgency": None,
                    "candidates": [],
                },
                sys.stdout,
            )
            return 0

        with session_scope() as session:
            # Product-aware retrieval + criteria-based broker ranking (no LLM).
            ranked = rank_suppliers(session, demand, now=now, limit=args.k)
            candidates = [r.as_dict() for r in ranked]

        json.dump(
            {
                "ok": True,
                "product": demand.normalized_product,
                "quantity": demand.quantity,
                "storage": demand.storage,
                "urgency": demand.urgency.value if demand.urgency else None,
                "candidates": candidates,
            },
            sys.stdout,
        )
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        json.dump({"ok": False, "error": str(exc)}, sys.stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
