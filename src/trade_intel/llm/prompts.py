"""Prompt construction for the broker assistant.

The golden rule (CLAUDE.md): **database facts always win, no hallucination.**
The matcher is the source of truth. The LLM only *reasons over* the candidate
facts we hand it — it must never invent suppliers, prices, or quantities.

The system prompt pins the persona + the no-hallucination contract. The user
prompt embeds a compact, machine-built CANDIDATES block (one line per scored
supplier) so the model's whole world is the verified DB rows.
"""

from __future__ import annotations

import re

SYSTEM_PROMPT = (
    "You are a sharp UAE wholesale electronics broker assistant for a Deira/"
    "Bur Dubai trader. You help close deals fast.\n\n"
    "ABSOLUTE RULES:\n"
    "1. The CANDIDATES list is the ONLY source of truth. Never invent a "
    "supplier, price, quantity, location, or grade that is not in it.\n"
    "2. If a fact is missing, say 'not stated' — never guess a number.\n"
    "3. If CANDIDATES is empty, say no suppliers were found and suggest the "
    "trader broaden the request. Do not fabricate.\n"
    "4. Confidence grades (A+/A/B/C) come from the engine — quote them, "
    "don't change them.\n"
    "5. Margins are SUGGESTIONS based on stated prices; label them clearly "
    "as suggestions, never as confirmed deals.\n\n"
    "STYLE: terse, practical, trader-to-trader. Use AED. Keep it scannable.\n\n"
    "Always answer in exactly this structure:\n"
    "Found: <N> suppliers.\n"
    "Best: <supplier name> (Confidence: <grade>)\n"
    "Why: <one line — recency, price, quantity fit>\n"
    "Suggested margin: AED <x>/unit (suggestion only)\n"
    "WhatsApp message:\n"
    '"<ready-to-send Salaam ... message asking LP / availability>"'
)


def _fmt(value: object, *, suffix: str = "") -> str:
    if value is None or value == "":
        return "not stated"
    return f"{value}{suffix}"


def build_candidate_block(candidates: list[dict]) -> str:
    """Render scored supplier dicts into a compact, grounded fact block.

    Each dict is expected to carry the keys produced by
    :func:`trade_intel.llm.broker.BrokerAssistant._candidate_facts`.
    """
    if not candidates:
        return "CANDIDATES: (none — engine found no matching inventory)"
    lines = ["CANDIDATES (verified from database — use only these):"]
    for i, c in enumerate(candidates, start=1):
        lines.append(
            f"{i}. {c['seller']} | {c['product']} | "
            f"storage={_fmt(c.get('storage'))} | "
            f"condition={_fmt(c.get('condition'))} | "
            f"qty={_fmt(c.get('quantity'))} | "
            f"price=AED {_fmt(c.get('price_aed'))} | "
            f"location={_fmt(c.get('location'))} | "
            f"freshness={c.get('freshness', '?')} | "
            f"grade={c.get('grade', '?')} | "
            f"match={c.get('match_type', '?')} | "
            f"score={c.get('score', 0):.2f} | "
            f"seller_posts={c.get('seller_post_count', 0)}"
        )
    return "\n".join(lines)


def build_broker_prompt(query: str, candidates: list[dict]) -> str:
    """Full user-turn prompt: the trader's request + the candidate facts."""
    block = build_candidate_block(candidates)
    return (
        f"Trader request: {query}\n\n"
        f"{block}\n\n"
        "Give your broker recommendation in the required structure. "
        "Rank by the engine score/grade and freshness. "
        "/no_think"
    )


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Remove qwen3-style ``<think>...</think>`` blocks from model output."""
    return _THINK_RE.sub("", text).strip()


__all__ = [
    "SYSTEM_PROMPT",
    "build_broker_prompt",
    "build_candidate_block",
    "strip_thinking",
]
