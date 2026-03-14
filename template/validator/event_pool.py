"""
Event Pool — manages multiple Polymarket events at different lifecycle stages.

Lifecycle of a single event:
  COMMITTED          → commits sent to miners, waiting for commit_deadline
  AWAITING_RESOLUTION → reveals verified, waiting for Polymarket outcome
  SCORED             → rewards computed and applied
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional

import numpy as np


class EventStage(Enum):
    COMMITTED = auto()
    AWAITING_RESOLUTION = auto()
    SCORED = auto()


@dataclass
class PooledEvent:
    event_id: str
    question: str
    market_prob: float
    commit_deadline: int          # Unix ts; reveals sent only after this passes
    miner_uids: np.ndarray
    commit_hashes: List           # one hash (or None) per miner
    stage: EventStage = EventStage.COMMITTED
    valid_probabilities: List = field(default_factory=list)
    stored_at: int = field(default_factory=lambda: int(time.time()))


class EventPool:
    """Thread-unsafe pool (validator runs single-threaded per forward loop)."""

    def __init__(self, max_scored_keep: int = 50):
        self._events: Dict[str, PooledEvent] = {}
        self._max_scored_keep = max_scored_keep

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._events

    def add(
        self,
        event_id: str,
        question: str,
        market_prob: float,
        commit_deadline: int,
        miner_uids: np.ndarray,
        commit_hashes: List,
    ) -> None:
        self._events[event_id] = PooledEvent(
            event_id=event_id,
            question=question,
            market_prob=market_prob,
            commit_deadline=commit_deadline,
            miner_uids=miner_uids,
            commit_hashes=commit_hashes,
        )

    def ready_for_reveal(self) -> List[PooledEvent]:
        """Events whose commit_deadline has passed but haven't been revealed yet."""
        now = int(time.time())
        return [
            e for e in self._events.values()
            if e.stage == EventStage.COMMITTED and now >= e.commit_deadline
        ]

    def mark_revealed(self, event_id: str, valid_probabilities: List) -> None:
        if event_id in self._events:
            self._events[event_id].valid_probabilities = valid_probabilities
            self._events[event_id].stage = EventStage.AWAITING_RESOLUTION

    def ready_for_scoring(self) -> List[PooledEvent]:
        return [
            e for e in self._events.values()
            if e.stage == EventStage.AWAITING_RESOLUTION
        ]

    def mark_scored(self, event_id: str) -> None:
        if event_id in self._events:
            self._events[event_id].stage = EventStage.SCORED

    def prune(self) -> None:
        """Keep only the most recent SCORED events to avoid unbounded growth."""
        scored = [e for e in self._events.values() if e.stage == EventStage.SCORED]
        scored.sort(key=lambda e: e.stored_at, reverse=True)
        for e in scored[self._max_scored_keep:]:
            del self._events[e.event_id]

    def summary(self) -> str:
        counts = {s: 0 for s in EventStage}
        for e in self._events.values():
            counts[e.stage] += 1
        return (
            f"{counts[EventStage.COMMITTED]} committed | "
            f"{counts[EventStage.AWAITING_RESOLUTION]} awaiting_resolution | "
            f"{counts[EventStage.SCORED]} scored"
        )
