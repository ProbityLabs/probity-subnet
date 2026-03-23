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


def _fetch_clob_outcome(condition_id: str) -> Optional[int]:
    """Query CLOB for resolution outcome. Returns 1 (YES), 0 (NO), or None."""
    try:
        data = _clob_get(f"/markets/{condition_id}")
    except Exception as exc:
        logger.error("CLOB fetch failed for %s: %s", condition_id, exc)
        return None

    for token in data.get("tokens", []):
        if token.get("winner") is True:
            outcome_str = str(token.get("outcome", "")).lower()
            return 1 if outcome_str in ("yes", "1") else 0
    return None


def _fetch_gamma_outcome(condition_id: str) -> Optional[int]:
    """Query Gamma API for resolution outcome. Returns 1 (YES), 0 (NO), or None."""
    try:
        markets = _gamma_get(
            "/markets",
            params={"conditionId": condition_id, "closed": "true"},
        )
        market_list = markets if isinstance(markets, list) else markets.get("markets", [])
        for m in market_list:
            if (m.get("conditionId") or m.get("condition_id")) != condition_id:
                continue

            # Check explicit resolution field first
            resolution = str(m.get("resolution") or "").lower()
            if resolution in ("yes", "1"):
                return 1
            if resolution in ("no", "0"):
                return 0

            # Fallback: outcomePrices + umaResolutionStatus
            # outcomePrices like ["1","0"] means first outcome won;
            # only trust this when umaResolutionStatus confirms resolved.
            uma_status = str(m.get("umaResolutionStatus") or "").lower()
            if uma_status != "resolved":
                continue

            outcome_prices = m.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                import json as _json
                try:
                    outcome_prices = _json.loads(outcome_prices)
                except Exception:
                    outcome_prices = []

            outcomes = m.get("outcomes", [])
            if isinstance(outcomes, str):
                import json as _json
                try:
                    outcomes = _json.loads(outcomes)
                except Exception:
                    outcomes = []

            # Find which outcome has price "1" (the winner)
            for price, label in zip(outcome_prices, outcomes):
                try:
                    if float(price) == 1.0:
                        label_lower = str(label).lower()
                        if label_lower in ("yes", "1"):
                            return 1
                        if label_lower in ("no", "0"):
                            return 0
                        # Non-binary outcome (e.g. team name) — check position
                        idx = outcomes.index(label)
                        return 1 if idx == 0 else 0
                except (ValueError, IndexError):
                    pass
    except Exception as exc:
        logger.debug("Gamma resolution fetch failed for %s: %s", condition_id, exc)
    return None


def fetch_outcome(condition_id: str) -> Optional[ResolvedEvent]:
    """
    Fetch the resolution outcome of a Polymarket market.

    Returns a ResolvedEvent only when **both** CLOB and Gamma APIs agree on the
    outcome (dual-source verification per whitepaper §5.2). If either source
    is unavailable or they disagree, returns None to wait for consensus.
    """
    clob_outcome = _fetch_clob_outcome(condition_id)
    gamma_outcome = _fetch_gamma_outcome(condition_id)

    if clob_outcome is not None and gamma_outcome is not None:
        if clob_outcome == gamma_outcome:
            # Both sources agree — resolution confirmed
            question = ""
            try:
                data = _clob_get(f"/markets/{condition_id}")
                question = data.get("question", "")
                # Extract winning token id
                for token in data.get("tokens", []):
                    if token.get("winner") is True:
                        return ResolvedEvent(
                            event_id=condition_id,
                            outcome=clob_outcome,
                            winning_token_id=token.get("token_id", ""),
                            question=question,
                        )
            except Exception:
                pass
            # Fallback: no winning token from CLOB but both agree on outcome
            return ResolvedEvent(
                event_id=condition_id,
                outcome=clob_outcome,
                winning_token_id="",
                question=question,
            )
        else:
            logger.warning(
                "Resolution mismatch for %s: CLOB=%s, Gamma=%s — waiting for consensus",
                condition_id, clob_outcome, gamma_outcome,
            )
            return None

    # Only one source available — not enough for dual-source confirmation
    available = "CLOB" if clob_outcome is not None else ("Gamma" if gamma_outcome is not None else "none")
    logger.debug(
        "Dual-source not met for %s: only %s available", condition_id, available,
    )
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
