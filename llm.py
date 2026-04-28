"""Shared Anthropic LLM access for LLM-backed features.

Used by the brand-voice refresh script, the pipeline digest, and the optional
LLM signature parser. Single source of truth so all three call sites use the
same client, the same model defaults, and the same error semantics.

Behavior:
    - The Anthropic API key is read from the `ANTHROPIC_API_KEY` env var.
    - The MCP server captures env at startup, so if the user adds the key to
      ~/.zshrc after launching Cowork, they need to restart Cowork. The cron
      scripts (refresh_brand_voice, pipeline_digest) fork fresh shells and
      always see the latest env.
    - All LLM features are OPTIONAL — every site that uses this module checks
      `is_available()` first and falls back to a no-LLM path if the key is
      missing or invalid.

Cost reference (claude-haiku-4-5, our default for cheap calls):
    - Input: ~$1 / 1M tokens
    - Output: ~$5 / 1M tokens
    - A typical brand-voice refresh: ~$0.05 per run
    - A typical signature parse: ~$0.001 per call
"""

from __future__ import annotations

import os
from typing import Any


# Default model for cheap, frequent calls (signature parsing, key checks).
# For richer brand-voice synthesis or pipeline summaries we may bump up.
DEFAULT_MODEL = "claude-haiku-4-5"


def get_api_key() -> str | None:
    """Return the Anthropic API key, checked in priority order:

        1. ANTHROPIC_API_KEY env var (most secure — preferred for cron scripts)
        2. `anthropic_api_key` in config.json (fallback for macOS GUI Cowork
           which doesn't reliably inherit shell env vars from ~/.zshrc).

    config.json is gitignored AND excluded from `make handoff` tarballs, so
    it's safe to put a key there for personal use. Each coworker still uses
    their own key — keys do not travel in handoffs.
    """
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return key
    # Fallback to config.json — handles the macOS sandboxed-Electron case where
    # ~/.zshrc env doesn't propagate to GUI-launched Cowork.
    try:
        import config as _config  # local import to avoid cycle at import time
        cfg_key = (_config.get("anthropic_api_key") or "").strip()
        return cfg_key or None
    except Exception:
        return None


def is_available() -> tuple[bool, str]:
    """Quick feasibility check.

    Returns (True, "ok") if the key is present and the SDK is importable.
    Returns (False, reason) otherwise. Does NOT make a network call.
    """
    key = get_api_key()
    if not key:
        return False, "ANTHROPIC_API_KEY is not set in the environment."
    if not key.startswith("sk-ant-"):
        return False, f"Key does not look like an Anthropic key (got prefix '{key[:8]}...')."
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic SDK is not installed in this venv. Run: pip install anthropic"
    return True, "ok"


_client = None


def get_client():
    """Return a cached Anthropic client. Raises RuntimeError if unavailable."""
    global _client
    ok, reason = is_available()
    if not ok:
        raise RuntimeError(f"LLM unavailable: {reason}")
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=get_api_key())
    return _client


def call_with_web_search(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    system: str | None = None,
    max_searches: int = 3,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """One-shot LLM call with Anthropic's server-side web_search tool.

    Used by the receipt enrichment ladder when Maps lookup didn't resolve a
    low-confidence receipt and we need to search the web to identify what
    kind of business a merchant is.

    The web search runs server-side on Anthropic's infrastructure — we pay
    a per-search fee (~$0.01) PLUS standard token cost. `max_searches` caps
    how many searches Claude can issue in service of one prompt. Default 3.

    Returns dict matching `call_simple`'s shape, with the final synthesized
    text answer in `text`. Raises RuntimeError if LLM unavailable; raises
    on tool errors so callers can fall back gracefully.
    """
    client = get_client()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_searches,
        }],
    }
    if system:
        kwargs["system"] = system

    msg = client.messages.create(**kwargs)
    # The model may invoke web_search several times before producing a final
    # answer. The SDK handles the tool roundtrip server-side; the response's
    # final text content blocks are what we want.
    text = "".join(
        getattr(b, "text", "") for b in (msg.content or [])
        if getattr(b, "type", "") == "text"
    ).strip()
    in_tok = msg.usage.input_tokens
    out_tok = msg.usage.output_tokens
    # Note: search tool fees are billed separately and not reflected here.
    cost_usd = (in_tok / 1_000_000) + (out_tok * 5 / 1_000_000) if "haiku" in model.lower() else None
    return {
        "text": text,
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "estimated_cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
        "search_count": getattr(msg.usage, "server_tool_use", {}).get("web_search_requests", None) if hasattr(msg.usage, "server_tool_use") else None,
    }


def call_simple(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    system: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Make a one-shot LLM call. Returns dict with text + usage + cost estimate.

    Raises RuntimeError if the LLM is unavailable. Callers should call
    `is_available()` first if they want a graceful fallback.
    """
    client = get_client()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    msg = client.messages.create(**kwargs)
    text = "".join(getattr(b, "text", "") for b in (msg.content or [])).strip()

    # Rough cost estimate. Pricing is per million tokens.
    # Haiku-4-5: $1/M input, $5/M output (approximate; real billing may differ).
    in_tok = msg.usage.input_tokens
    out_tok = msg.usage.output_tokens
    if "haiku" in model.lower():
        cost_usd = (in_tok / 1_000_000) + (out_tok * 5 / 1_000_000)
    elif "sonnet" in model.lower():
        cost_usd = (in_tok * 3 / 1_000_000) + (out_tok * 15 / 1_000_000)
    elif "opus" in model.lower():
        cost_usd = (in_tok * 15 / 1_000_000) + (out_tok * 75 / 1_000_000)
    else:
        cost_usd = None

    return {
        "text": text,
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "estimated_cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
    }
