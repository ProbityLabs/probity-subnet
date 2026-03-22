"""
Event Pool — manages multiple Polymarket events at different lifecycle stages.

Lifecycle of a single event:
  OPEN               → accepting miner commitments
  AWAITING_REVEAL    → commit deadline passed, reveals pending
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
    OPEN = auto()                # Accepting miner commitments
    AWAITING_REVEAL = auto()     # Commit deadline passed, reveals pending
    AWAITING_RESOLUTION = auto() # Reveals verified, waiting for outcome
    SCORED = auto()


@dataclass
class PooledEvent:
    event_id: str
    question: str
    market_prob: float
    commit_deadline: int          # Unix ts; no new commitments accepted after this
    market_close_ts: int          # Unix ts; reveal phase opens 24h before this
    # While OPEN: hotkey → commitment_hash (miners push commitments to validator)
    pending_hashes: Dict[str, str] = field(default_factory=dict)
    stage: EventStage = EventStage.OPEN
    # Filled when closing commits (OPEN → AWAITING_REVEAL)
    miner_uids: Optional[np.ndarray] = None
    commit_hashes: Optional[List] = None   # parallel to miner_uids
    # Filled after reveals
    valid_probabilities: Optional[List] = None
    stored_at: int = field(default_factory=lambda: int(time.time()))


class EventPool:
    """Thread-unsafe pool (validator runs single-threaded per forward loop)."""

    REVEAL_BEFORE_CLOSE = 24 * 3600  # reveal phase opens 24h before market close

    def __init__(self, max_scored_keep: int = 50):
        self._events: Dict[str, PooledEvent] = {}
        self._max_scored_keep = max_scored_keep

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._events

    def add_event(
        self,
        event_id: str,
        question: str,
        market_prob: float,
        commit_deadline: int,
        market_close_ts: int,
    ) -> None:
        """Add a new event in OPEN stage (miners can now submit commitments)."""
        self._events[event_id] = PooledEvent(
            event_id=event_id,
            question=question,
            market_prob=market_prob,
            commit_deadline=commit_deadline,
            market_close_ts=market_close_ts,
        )

    def get_active_events(self) -> List[PooledEvent]:
        """Return OPEN events (served to miners via EventList synapse)."""
        return [e for e in self._events.values() if e.stage == EventStage.OPEN]

    def add_commitment(
        self, event_id: str, miner_hotkey: str, commitment_hash: str
    ) -> bool:
        """
        Accept a miner's commitment. Returns True if accepted.
        Rejected if: event not found, not OPEN, or past commit deadline.
        """
        if event_id not in self._events:
            return False
        e = self._events[event_id]
        if e.stage != EventStage.OPEN:
            return False
        if int(time.time()) > e.commit_deadline:
            return False
        e.pending_hashes[miner_hotkey] = commitment_hash
        return True

    def ready_for_reveal(self) -> List[PooledEvent]:
        """
        OPEN events whose commit deadline has passed AND we're within 24h of market close.
        """
        now = int(time.time())
        return [
            e for e in self._events.values()
            if e.stage == EventStage.OPEN
            and now >= e.commit_deadline
            and now >= e.market_close_ts - self.REVEAL_BEFORE_CLOSE
        ]

    def close_commits(self, event_id: str, metagraph) -> None:
        """
        Transition OPEN → AWAITING_REVEAL.
        Resolves committed miner hotkeys to UIDs via metagraph.
        """
        if event_id not in self._events:
            return
        e = self._events[event_id]
        if e.stage != EventStage.OPEN:
            return
        uids = []
        hashes = []
        for hotkey, h in e.pending_hashes.items():
            try:
                uid = metagraph.hotkeys.index(hotkey)
                uids.append(uid)
                hashes.append(h)
            except ValueError:
                pass  # hotkey no longer in metagraph
        e.miner_uids = np.array(uids, dtype=np.int64)
        e.commit_hashes = hashes
        e.stage = EventStage.AWAITING_REVEAL

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
            f"{counts[EventStage.OPEN]} open | "
            f"{counts[EventStage.AWAITING_REVEAL]} awaiting_reveal | "
            f"{counts[EventStage.AWAITING_RESOLUTION]} awaiting_resolution | "
            f"{counts[EventStage.SCORED]} scored"
        )
