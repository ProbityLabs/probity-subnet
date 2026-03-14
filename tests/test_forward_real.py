"""
Test forward() with event pool, rolling skill, deadline enforcement, and SWPE.

Run with:
    pytest tests/test_forward_real.py -v -s
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import template.validator.forward as fwd_module
from template.mock import MockDendrite, MockMetagraph, MockSubtensor, MockWallet
from template.validator.event_pool import EventStage
from template.validator.event_source import EventRecord
from template.validator.event_resolver import ResolvedEvent

FAKE_EVENT = EventRecord(
    event_id="0xabc123deadbeef0000000000000000000000000000000000000000000000000",
    question="Will BTC exceed $100k by end of 2025?",
    market_prob=0.62,
    end_date_iso="2025-12-31",
)

FAKE_RESOLVED = ResolvedEvent(
    event_id=FAKE_EVENT.event_id,
    outcome=1,
    winning_token_id="yes-token",
    question=FAKE_EVENT.question,
)


class FakeValidator:
    uid = 0
    commit_window_seconds = -5  # deadline already in the past → reveals happen next pass

    def __init__(self, metagraph, dendrite):
        self.metagraph = metagraph
        self.dendrite = dendrite
        self.config = MagicMock()
        self.config.neuron.sample_size = 4
        self.config.neuron.vpermit_tao_limit = 4096
        self._score_calls = []

    def update_scores(self, rewards, uids):
        self._score_calls.append((rewards, uids))
        print(f"\n  update_scores → rewards={[round(float(r),4) for r in rewards]}, uids={uids.tolist()}")


@pytest.fixture
def validator():
    wallet = MockWallet()
    subtensor = MockSubtensor(netuid=1, n=4, wallet=wallet)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    dendrite = MockDendrite(wallet=wallet)
    return FakeValidator(metagraph, dendrite)


def run(coro):
    return asyncio.run(coro)


# ── helpers ──────────────────────────────────────────────────────────────────

def pass1_commit(validator):
    """Pass 1: commit to FAKE_EVENT, add to pool."""
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=None):
        run(fwd_module.forward(validator))


def pass2_reveal_and_score(validator, resolved=FAKE_RESOLVED):
    """Pass 2: reveal (deadline passed) + score if resolved."""
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=resolved):
        run(fwd_module.forward(validator))


# ── tests ────────────────────────────────────────────────────────────────────

def test_commit_adds_to_pool(validator):
    """Pass 1 should add the event to the pool as COMMITTED."""
    # Use a far-future window so the event stays COMMITTED after the first pass
    validator.commit_window_seconds = 9999
    pass1_commit(validator)

    assert FAKE_EVENT.event_id in validator._event_pool
    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    assert pooled.stage == EventStage.COMMITTED
    assert len(pooled.commit_hashes) == 4  # one per miner
    assert all(h is not None for h in pooled.commit_hashes)
    print("\n[PASS] Event committed and added to pool.")


def test_reveal_and_score_on_second_pass(validator):
    """Pass 1 commits; pass 2 reveals + scores (deadline already past)."""
    pass1_commit(validator)
    pass2_reveal_and_score(validator)

    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    assert pooled.stage == EventStage.SCORED
    assert len(validator._score_calls) == 1

    rewards, uids = validator._score_calls[0]
    assert len(rewards) == len(uids)
    assert all(r >= 0 for r in rewards)
    print("[PASS] Revealed, verified, and scored on second pass.")


def test_rolling_skill_updated(validator):
    """RollingSkillTracker should be updated after scoring."""
    pass1_commit(validator)
    pass2_reveal_and_score(validator)

    tracker = validator._skill_tracker
    # At least one miner should have non-zero rolling skill
    rolling = tracker.all()
    assert any(abs(s) > 0 for s in rolling), "Expected at least one non-zero rolling skill"
    print(f"[PASS] Rolling skills: {[round(float(s), 4) for s in rolling]}")


def test_event_not_recommitted_on_second_pass(validator):
    """Same event should not be added to pool twice."""
    pass1_commit(validator)
    pass2_reveal_and_score(validator)

    # Third pass: pool has the event SCORED, should not re-add
    pass1_commit(validator)

    scored_count = sum(
        1 for e in validator._event_pool._events.values()
        if e.stage == EventStage.SCORED
    )
    assert scored_count == 1
    print("[PASS] Event not recommitted after scoring.")


def test_no_events_skips_commit(validator):
    """If Polymarket returns nothing, commit phase is skipped but pool still processed."""
    with patch("template.validator.forward.fetch_active_events", return_value=[]), \
         patch("template.validator.forward.fetch_outcome", return_value=None):
        run(fwd_module.forward(validator))

    assert len(validator._event_pool._events) == 0
    assert len(validator._score_calls) == 0
    print("\n[PASS] Commit skipped gracefully when no events available.")


def test_deadline_enforcement(validator):
    """Miners should refuse to reveal before commit_deadline passes."""
    # Use a far-future deadline so reveal is blocked
    validator.commit_window_seconds = 9999

    pass1_commit(validator)
    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    assert pooled.stage == EventStage.COMMITTED, "Should still be COMMITTED"

    # Second pass: deadline not yet passed → still COMMITTED
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=FAKE_RESOLVED):
        run(fwd_module.forward(validator))

    assert pooled.stage == EventStage.COMMITTED, "Reveal should be blocked by deadline"
    assert len(validator._score_calls) == 0
    print("\n[PASS] Reveal blocked while commit deadline has not passed.")
