"""
Event Resolution for Probity subnet.

Polls Polymarket's CLOB API to determine whether a market has resolved
and returns the outcome (1 = YES, 0 = NO).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CLOB_URL = "https://clob.polymarket.com"
_GAMMA_URL = "https://gamma-api.polymarket.com"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ResolvedEvent:
    event_id: str
    outcome: int           # 1 = YES resolved, 0 = NO resolved
    winning_token_id: str  # CLOB token id of the winning side
    question: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clob_get(path: str, timeout: int = 10) -> dict:
    resp = _SESSION.get(f"{_CLOB_URL}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _gamma_get(path: str, params: dict | None = None, timeout: int = 10) -> dict | list:
    resp = _SESSION.get(f"{_GAMMA_URL}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_resolved(condition_id: str) -> bool:
    """
    Return True if the Polymarket market has been resolved.
    """
    result = fetch_outcome(condition_id)
    return result is not None


def fetch_outcome(condition_id: str) -> Optional[ResolvedEvent]:
    """
    Fetch the resolution outcome of a Polymarket market.

    Returns a ResolvedEvent if the market is resolved, else None.

    The CLOB market object contains a ``tokens`` list; the resolved token
    will have ``"winner": true``.  If the CLOB doesn't expose that flag yet
    we fall back to the Gamma API ``resolution`` field.
    """
    # --- Try CLOB first ---
    try:
        data = _clob_get(f"/markets/{condition_id}")
    except Exception as exc:
        logger.error("CLOB fetch failed for %s: %s", condition_id, exc)
        return None

    tokens = data.get("tokens", [])

    # CLOB marks resolved tokens with {"winner": true}
    for token in tokens:
        if token.get("winner") is True:
            outcome_str = str(token.get("outcome", "")).lower()
            outcome = 1 if outcome_str in ("yes", "1") else 0
            return ResolvedEvent(
                event_id=condition_id,
                outcome=outcome,
                winning_token_id=token.get("token_id", ""),
                question=data.get("question", ""),
            )

    # --- Fallback: Gamma API ``resolution`` field ---
    try:
        markets = _gamma_get(
            "/markets",
            params={"conditionId": condition_id, "closed": "true"},
        )
        market_list = markets if isinstance(markets, list) else markets.get("markets", [])
        for m in market_list:
            if (m.get("conditionId") or m.get("condition_id")) != condition_id:
                continue
            resolution = str(m.get("resolution") or "").lower()
            if resolution in ("yes", "1"):
                return ResolvedEvent(
                    event_id=condition_id,
                    outcome=1,
                    winning_token_id="",
                    question=m.get("question", ""),
                )
            if resolution in ("no", "0"):
                return ResolvedEvent(
                    event_id=condition_id,
                    outcome=0,
                    winning_token_id="",
                    question=m.get("question", ""),
                )
    except Exception as exc:
        logger.debug("Gamma resolution fallback failed for %s: %s", condition_id, exc)

    return None


def wait_for_resolution(
    condition_id: str,
    poll_interval_seconds: int = 60,
    max_attempts: int = 1440,   # ~24 hours at 60s interval
) -> Optional[ResolvedEvent]:
    """
    Blocking poll until a market resolves or max_attempts is exhausted.

    Intended for use in testing or background tasks — not inside the main
    validator forward loop (use ``fetch_outcome`` there and skip unresolved).
    """
    import time

    for attempt in range(max_attempts):
        result = fetch_outcome(condition_id)
        if result is not None:
            return result
        logger.debug(
            "Market %s not yet resolved (attempt %d/%d), sleeping %ds",
            condition_id, attempt + 1, max_attempts, poll_interval_seconds,
        )
        time.sleep(poll_interval_seconds)

    logger.warning("Market %s did not resolve after %d attempts.", condition_id, max_attempts)
    return None
