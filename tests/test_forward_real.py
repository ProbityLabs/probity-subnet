"""
Test forward() with event pool, rolling skill, deadline enforcement, and SWPE.

Run with:
    pytest tests/test_forward_real.py -v -s
"""

import asyncio
import hashlib
import random
import time
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
    commit_window_seconds = 9999  # far-future deadline by default

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


# ── helpers ───────────────────────────────────────────────────────────────────

def pass1_add_event(validator):
    """Pass 1: fetch event, add to pool as OPEN."""
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=None):
        run(fwd_module.forward(validator))


def inject_commitments(validator, axons):
    """Inject mock commitments directly into the pool (simulating miners having committed)."""
    for axon in axons:
        prob = max(0.01, min(0.99, FAKE_EVENT.market_prob + random.uniform(-0.1, 0.1)))
        nonce = str(random.randint(1_000_000, 9_999_999))
        data = f"{prob}{nonce}{FAKE_EVENT.event_id}{axon.hotkey}"
        h = hashlib.sha256(data.encode()).hexdigest()

        validator._event_pool.add_commitment(
            event_id=FAKE_EVENT.event_id,
            miner_hotkey=axon.hotkey,
            commitment_hash=h,
        )
        # Seed dendrite so it can answer the Reveal request
        validator.dendrite._predictions[(axon.hotkey, FAKE_EVENT.event_id)] = {
            "p": prob,
            "nonce": nonce,
            "commit_deadline": 0,  # already past
        }


def expire_deadline(validator):
    """Force the event's commit deadline to the past so ready_for_reveal() triggers."""
    validator._event_pool._events[FAKE_EVENT.event_id].commit_deadline = int(time.time()) - 1


def pass2_reveal_and_score(validator, resolved=FAKE_RESOLVED):
    """Pass 2: close commits (deadline past) + reveal + score if resolved."""
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=resolved):
        run(fwd_module.forward(validator))


# ── tests ─────────────────────────────────────────────────────────────────────

def test_event_added_to_pool(validator):
    """Pass 1 should add the event to the pool as OPEN."""
    pass1_add_event(validator)

    assert FAKE_EVENT.event_id in validator._event_pool
    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    assert pooled.stage == EventStage.OPEN
    print("\n[PASS] Event added to pool as OPEN.")


def test_reveal_and_score_on_second_pass(validator):
    """Inject commitments, expire deadline; pass 2 closes commits, reveals, and scores."""
    pass1_add_event(validator)

    # Miners commit while deadline is in the future
    inject_commitments(validator, validator.metagraph.axons[1:5])
    assert len(validator._event_pool._events[FAKE_EVENT.event_id].pending_hashes) == 4

    expire_deadline(validator)
    pass2_reveal_and_score(validator)

    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    assert pooled.stage == EventStage.SCORED
    assert len(validator._score_calls) == 1

    rewards, uids = validator._score_calls[0]
    assert len(rewards) == len(uids)
    assert all(r >= 0 for r in rewards)
    print("[PASS] Closed commits, revealed, verified, and scored on second pass.")


def test_rolling_skill_updated(validator):
    """RollingSkillTracker should be updated after scoring."""
    pass1_add_event(validator)
    inject_commitments(validator, validator.metagraph.axons[1:5])
    expire_deadline(validator)
    pass2_reveal_and_score(validator)

    tracker = validator._skill_tracker
    rolling = tracker.all()
    assert any(abs(s) > 0 for s in rolling), "Expected at least one non-zero rolling skill"
    print(f"[PASS] Rolling skills: {[round(float(s), 4) for s in rolling]}")


def test_event_not_recommitted_on_second_pass(validator):
    """Same event should not be re-added to the pool after scoring."""
    pass1_add_event(validator)
    inject_commitments(validator, validator.metagraph.axons[1:5])
    expire_deadline(validator)
    pass2_reveal_and_score(validator)

    # Third pass: event is SCORED — should not re-add
    pass1_add_event(validator)

    scored_count = sum(
        1 for e in validator._event_pool._events.values()
        if e.stage == EventStage.SCORED
    )
    assert scored_count == 1
    print("[PASS] Event not re-added after scoring.")


def test_no_events_skips_commit(validator):
    """If Polymarket returns nothing, pool stays empty."""
    with patch("template.validator.forward.fetch_active_events", return_value=[]), \
         patch("template.validator.forward.fetch_outcome", return_value=None):
        run(fwd_module.forward(validator))

    assert len(validator._event_pool._events) == 0
    assert len(validator._score_calls) == 0
    print("\n[PASS] Commit skipped gracefully when no events available.")


def test_deadline_enforcement(validator):
    """With a far-future deadline, commits stay OPEN; no reveals happen."""
    # commit_window_seconds=9999 is the default → deadline is far future
    pass1_add_event(validator)
    inject_commitments(validator, validator.metagraph.axons[1:5])

    # Pass 2: deadline not yet passed → still OPEN, nothing scored
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=FAKE_RESOLVED):
        run(fwd_module.forward(validator))

    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    assert pooled.stage == EventStage.OPEN, "Reveal should be blocked while deadline is in the future"
    assert len(validator._score_calls) == 0
    print("\n[PASS] Reveal blocked while commit deadline has not passed.")


def test_no_miners_committed(validator):
    """If no miners committed, reveal produces empty results and scores nothing."""
    pass1_add_event(validator)
    # Don't inject any commitments
    expire_deadline(validator)
    pass2_reveal_and_score(validator)

    pooled = validator._event_pool._events[FAKE_EVENT.event_id]
    # Event skips straight to AWAITING_RESOLUTION with empty valid_probs, then scores (0 miners)
    assert pooled.stage in (EventStage.AWAITING_RESOLUTION, EventStage.SCORED)
    print("[PASS] Handled gracefully when no miners committed.")
