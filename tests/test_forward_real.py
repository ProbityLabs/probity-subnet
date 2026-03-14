"""
Test forward() with mocked Polymarket API but real commit-reveal flow.

Run with:
    pytest tests/test_forward_real.py -v -s
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from template.mock import MockDendrite, MockMetagraph, MockSubtensor, MockWallet
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


def test_forward_event_resolves_immediately(validator):
    """Normal case: event resolves in the same round."""
    from template.validator.forward import forward

    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=FAKE_RESOLVED):
        asyncio.run(forward(validator))

    assert len(validator._score_calls) == 1, "update_scores should be called once"
    rewards, uids = validator._score_calls[0]
    assert len(rewards) == len(uids)
    assert all(r >= 0 for r in rewards)
    print("\n[PASS] Scores updated immediately after resolution.")


def test_forward_event_pending_then_resolved(validator):
    """Deferred case: event is unresolved on first pass, resolves on second pass."""
    from template.validator.forward import forward

    # First pass: no resolution
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=None):
        asyncio.run(forward(validator))

    assert len(validator._score_calls) == 0, "Should not score yet"
    assert len(validator._pending_rounds) == 1, "Round should be stored as pending"
    print("\n[PASS] Round stored as pending.")

    # Second pass: event resolves; _flush_pending_rounds fires
    with patch("template.validator.forward.fetch_active_events", return_value=[FAKE_EVENT]), \
         patch("template.validator.forward.fetch_outcome", return_value=FAKE_RESOLVED):
        asyncio.run(forward(validator))

    assert len(validator._score_calls) >= 1, "Deferred round should now be scored"
    assert len(getattr(validator, "_pending_rounds", [])) == 0, "Pending queue should be empty"
    print("[PASS] Deferred round scored on second pass.")


def test_forward_no_events_skips_round(validator):
    """If Polymarket returns nothing, round is skipped gracefully."""
    from template.validator.forward import forward

    with patch("template.validator.forward.fetch_active_events", return_value=[]):
        asyncio.run(forward(validator))

    assert len(validator._score_calls) == 0
    print("\n[PASS] Round skipped cleanly when no events available.")
