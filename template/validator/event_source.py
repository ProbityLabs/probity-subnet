"""
Event Ingestion for Probity subnet.

Fetches active binary prediction markets from Polymarket's Gamma API
and returns them as EventRecord objects for use in the commit-reveal cycle.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class EventRecord:
    """A single binary prediction-market event."""
    event_id: str          # Polymarket condition_id (hex string)
    question: str          # Human-readable question
    market_prob: float     # Current best probability (0–1)
    end_date_iso: str      # ISO-8601 resolution date
    resolution_criteria: str = ""  # How the market resolves (from Polymarket description)
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Polymarket API helpers
# ---------------------------------------------------------------------------

_GAMMA_URL = "https://gamma-api.polymarket.com"
_CLOB_URL  = "https://clob.polymarket.com"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})


def _gamma_get(path: str, params: dict | None = None, timeout: int = 10) -> dict | list:
    url = f"{_GAMMA_URL}{path}"
    resp = _SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _clob_get(path: str, params: dict | None = None, timeout: int = 10) -> dict | list:
    url = f"{_CLOB_URL}{path}"
    resp = _SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _mid_price(token_prices: list[dict]) -> Optional[float]:
    """
    Extract YES-token price from the CLOB token list.
    Each token looks like {"token_id": "...", "outcome": "Yes", "price": "0.63"}.
    """
    for t in token_prices:
        if str(t.get("outcome", "")).lower() in ("yes", "1"):
            try:
                return float(t["price"])
            except (KeyError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_active_events(
    limit: int = 20,
    min_volume: float = 10_000,
    min_prob: float = 0.05,
    max_prob: float = 0.95,
) -> List[EventRecord]:
    """
    Return up to *limit* active binary events from Polymarket.

    Parameters
    ----------
    limit       : max number of events to return
    min_volume  : minimum USD volume to filter out illiquid markets
    min_prob    : minimum market probability (filters near-certain events)
    max_prob    : maximum market probability (filters near-certain events)
    """
    try:
        # Fetch active, binary, non-resolved markets sorted by volume
        data = _gamma_get(
            "/markets",
            params={
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": limit * 5,   # fetch extra so we can filter
                "order": "volume",
                "ascending": "false",
            },
        )
    except Exception as exc:
        logger.error("Polymarket Gamma API error: %s", exc)
        return []

    markets = data if isinstance(data, list) else data.get("markets", [])

    results: List[EventRecord] = []
    for m in markets:
        if len(results) >= limit:
            break

        # Only binary (Yes/No) markets
        outcomes = m.get("outcomes", [])
        if isinstance(outcomes, str):
            import json as _json
            try:
                outcomes = _json.loads(outcomes)
            except Exception:
                outcomes = []
        if len(outcomes) != 2:
            continue

        # Volume filter
        try:
            vol = float(m.get("volume", 0) or 0)
        except ValueError:
            vol = 0.0
        if vol < min_volume:
            continue

        # Resolve market probability via CLOB
        condition_id = m.get("conditionId") or m.get("condition_id")
        if not condition_id:
            continue

        market_prob = _fetch_clob_price(condition_id)
        if market_prob is None:
            # Fall back to outcomePrices from Gamma
            outcome_prices = m.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                import json as _json
                try:
                    outcome_prices = _json.loads(outcome_prices)
                except Exception:
                    outcome_prices = []
            if outcome_prices:
                try:
                    market_prob = float(outcome_prices[0])
                except (ValueError, IndexError):
                    pass

        if market_prob is None:
            continue
        if not (min_prob <= market_prob <= max_prob):
            continue

        results.append(EventRecord(
            event_id=condition_id,
            question=m.get("question", ""),
            market_prob=round(market_prob, 6),
            end_date_iso=m.get("endDate") or m.get("end_date_iso") or "",
            resolution_criteria=m.get("description") or m.get("resolution_source") or "",
            raw=m,
        ))

    return results


def _fetch_clob_price(condition_id: str) -> Optional[float]:
    """Query CLOB for the current YES mid-price of a market."""
    try:
        data = _clob_get(f"/markets/{condition_id}")
        tokens = data.get("tokens", [])
        return _mid_price(tokens)
    except Exception as exc:
        logger.debug("CLOB price fetch failed for %s: %s", condition_id, exc)
        return None


def fetch_single_event(condition_id: str) -> Optional[EventRecord]:
    """Fetch a single event by its Polymarket condition_id."""
    try:
        data = _clob_get(f"/markets/{condition_id}")
    except Exception as exc:
        logger.error("CLOB fetch failed for %s: %s", condition_id, exc)
        return None

    market_prob = _mid_price(data.get("tokens", []))
    if market_prob is None:
        return None

    return EventRecord(
        event_id=condition_id,
        question=data.get("question", ""),
        market_prob=round(market_prob, 6),
        end_date_iso=data.get("end_date_iso") or data.get("endDate") or "",
        raw=data,
    )
