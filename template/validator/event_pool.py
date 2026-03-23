"""
Event Pool — manages multiple Polymarket events at different lifecycle stages.

Lifecycle of a single event:
  OPEN               → accepting miner commitments
  AWAITING_REVEAL    → commit deadline passed, reveals pending
  AWAITING_RESOLUTION → reveals verified, waiting for Polymarket outcome
  SCORED             → rewards computed and applied
"""

from __future__ import annotations

import json
import os
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

    def update_market_prob(self, event_id: str, market_prob: float) -> None:
        """Overwrite market_prob with a fresh snapshot (called at commit-window close)."""
        if event_id in self._events:
            self._events[event_id].market_prob = market_prob

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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_to_file(self, path: str) -> None:
        """Persist non-SCORED events to a JSON file so they survive restarts."""
        serializable = []
        for e in self._events.values():
            if e.stage == EventStage.SCORED:
                continue  # no need to persist already-scored events
            entry = {
                "event_id": e.event_id,
                "question": e.question,
                "market_prob": e.market_prob,
                "commit_deadline": e.commit_deadline,
                "market_close_ts": e.market_close_ts,
                "pending_hashes": e.pending_hashes,
                "stage": e.stage.name,
                "miner_uids": e.miner_uids.tolist() if e.miner_uids is not None else None,
                "commit_hashes": e.commit_hashes,
                "valid_probabilities": e.valid_probabilities,
                "stored_at": e.stored_at,
            }
            serializable.append(entry)
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(serializable, f)
            os.replace(tmp, path)
        except Exception:
            pass  # gracefully skip if path is not writable (e.g. in tests)

    @classmethod
    def load_from_file(cls, path: str, max_scored_keep: int = 50) -> "EventPool":
        """Restore an EventPool from a JSON file."""
        pool = cls(max_scored_keep=max_scored_keep)
        if not os.path.exists(path):
            return pool
        try:
            with open(path) as f:
                entries = json.load(f)
        except Exception:
            return pool
        for entry in entries:
            e = PooledEvent(
                event_id=entry["event_id"],
                question=entry.get("question", ""),
                market_prob=entry["market_prob"],
                commit_deadline=entry["commit_deadline"],
                market_close_ts=entry.get("market_close_ts", 0),
                pending_hashes=entry.get("pending_hashes", {}),
                stage=EventStage[entry["stage"]],
                miner_uids=(
                    np.array(entry["miner_uids"], dtype=np.int64)
                    if entry.get("miner_uids") is not None
                    else None
                ),
                commit_hashes=entry.get("commit_hashes"),
                valid_probabilities=entry.get("valid_probabilities"),
                stored_at=entry.get("stored_at", int(time.time())),
            )
            pool._events[e.event_id] = e
        return pool
