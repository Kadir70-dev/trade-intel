"""Local-LLM broker layer (no paid APIs).

- :mod:`client` — stdlib Ollama HTTP client (qwen3:4b primary, hermes3 fallback).
- :mod:`prompts` — grounded, no-hallucination prompt construction.
- :mod:`broker` — orchestrates the matcher (truth) + LLM (reasoning) into a
  ChatGPT-style broker turn: buyer -> seller -> reasoning -> WhatsApp message.
"""

from __future__ import annotations

from trade_intel.llm.broker import BrokerAssistant, BrokerResponse, query_to_demand
from trade_intel.llm.client import LlmResult, OllamaClient, OllamaError
from trade_intel.llm.prompts import SYSTEM_PROMPT, build_broker_prompt

__all__ = [
    "SYSTEM_PROMPT",
    "BrokerAssistant",
    "BrokerResponse",
    "LlmResult",
    "OllamaClient",
    "OllamaError",
    "build_broker_prompt",
    "query_to_demand",
]
