"""
End-to-end pull model flow test for the Probity commit-reveal subnet.

Pull model flow:
  1. Validator adds event to pool (OPEN stage)
  2. Miner queries validator's EventList handler → gets active events
  3. Miner computes prediction, hashes it, submits CommitSubmission to validator
  4. Commit deadline passes → validator closes commits, sends Reveal to miners
  5. Miners reveal (prob, nonce) → validator verifies hashes, scores

Run with:
    pytest tests/test_flow.py -v -s
"""

import asyncio
import hashlib
import time
import uuid
import random

import bittensor as bt
import pytest
from unittest.mock import MagicMock

from template.mock import MockDendrite, MockMetagraph, MockSubtensor, MockWallet
from template.protocol import CommitSubmission, EventList, Reveal
from template.validator.event_pool import EventPool, EventStage
from template.validator.forward import forward_event_list, forward_commit_submission
from template.validator.reward import get_rewards


N_MINERS = 4


@pytest.fixture
def setup():
    wallet = MockWallet()
    subtensor = MockSubtensor(netuid=1, n=N_MINERS, wallet=wallet)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    dendrite = MockDendrite(wallet=wallet)
    return wallet, metagraph, dendrite


class FakeValidator:
    uid = 0

    def __init__(self, metagraph, dendrite):
        self.metagraph = metagraph
        self.dendrite = dendrite
        self.config = MagicMock()
        self._event_pool = EventPool()


def test_pull_model_flow(setup):
    wallet, metagraph, dendrite = setup
    axons = metagraph.axons
    validator = FakeValidator(metagraph, dendrite)

    event_id = str(uuid.uuid4())
    market_prob = round(random.uniform(0.2, 0.8), 4)
    outcome = 1 if random.random() < market_prob else 0
    commit_deadline = int(time.time()) + 9999  # far future so miners can commit

    print(f"\n{'='*60}")
    print(f"EVENT      : {event_id}")
    print(f"market_prob: {market_prob}")
    print(f"outcome    : {outcome}  (1=YES, 0=NO)")
    print(f"miners     : {[a.hotkey for a in axons]}")
    print(f"{'='*60}")

    # ── 1. Validator adds event to pool ──────────────────────────────────────
    print("\n[VALIDATOR] Adding event to pool...")
    validator._event_pool.add_event(
        event_id=event_id,
        question="Will BTC exceed $100k?",
        market_prob=market_prob,
        commit_deadline=commit_deadline,
        market_close_ts=commit_deadline + 3600,
    )
    assert validator._event_pool._events[event_id].stage == EventStage.OPEN

    # ── 2. Miner queries EventList ────────────────────────────────────────────
    print("\n[MINER] Querying validator for active events...")
    event_list_synapse = EventList()
    result = asyncio.run(forward_event_list(validator, event_list_synapse))
    events = result.events
    assert len(events) == 1
    assert events[0].event_id == event_id
    print(f"  Got {len(events)} event(s): {events[0].question}")

    # ── 3. Miners submit commitments ──────────────────────────────────────────
    print("\n[MINER] Submitting commitments to validator...")
    miner_predictions = {}

    print(f"\n{'Miner':<20} {'Prediction':>10}  {'Commitment Hash'}")
    print(f"{'-'*20} {'-'*10}  {'-'*64}")

    for axon in axons[1:]:  # skip uid=0 (validator)
        prob = max(0.01, min(0.99, market_prob + random.uniform(-0.1, 0.1)))
        nonce = str(random.randint(1_000_000, 9_999_999))
        data = f"{prob}{nonce}{event_id}{axon.hotkey}"
        commitment_hash = hashlib.sha256(data.encode()).hexdigest()
        miner_predictions[axon.hotkey] = {"p": prob, "nonce": nonce}

        # Miner calls validator's CommitSubmission handler
        commit_syn = CommitSubmission(
            event_id=event_id,
            commitment_hash=commitment_hash,
            timestamp=int(time.time()),
        )
        commit_syn.dendrite = bt.TerminalInfo(hotkey=axon.hotkey)

        resp = asyncio.run(forward_commit_submission(validator, commit_syn))
        assert resp.accepted, f"Commitment rejected for {axon.hotkey}"

        # Seed dendrite so it can reveal later
        dendrite._predictions[(axon.hotkey, event_id)] = {
            "p": prob,
            "nonce": nonce,
            "commit_deadline": 0,  # already past for reveal
        }

        print(f"{axon.hotkey:<20} {prob:>10.4f}  {commitment_hash}")

    committed = validator._event_pool._events[event_id].pending_hashes
    assert len(committed) == N_MINERS, f"Expected {N_MINERS} commitments, got {len(committed)}"

    # ── 4. Commit deadline passes → close commits ─────────────────────────────
    print("\n[VALIDATOR] Commit deadline passed, closing commits...")
    validator._event_pool._events[event_id].commit_deadline = int(time.time()) - 1
    validator._event_pool.close_commits(event_id, metagraph)

    pooled = validator._event_pool._events[event_id]
    assert pooled.stage == EventStage.AWAITING_REVEAL
    assert len(pooled.miner_uids) == N_MINERS

    # ── 5. Validator sends Reveal to miners ───────────────────────────────────
    print("\n[VALIDATOR] Sending Reveal synapses to miners...")
    reveal_axons = [metagraph.axons[uid] for uid in pooled.miner_uids]

    reveal_responses = asyncio.run(
        dendrite(
            reveal_axons,
            synapse=Reveal(event_id=event_id),
            timeout=5,
            deserialize=True,
        )
    )

    print(f"\n{'Miner':<20} {'Prob':>6}  {'Nonce'}")
    print(f"{'-'*20} {'-'*6}  {'-'*10}")
    for axon, (prob, nonce) in zip(reveal_axons, reveal_responses):
        print(f"{axon.hotkey:<20} {prob:>6.4f}  {nonce}")

    # ── 6. Hash verification ──────────────────────────────────────────────────
    print(f"\n[VERIFY] Checking revealed values against committed hashes...")
    valid_probs = []
    print(f"\n{'Miner':<20} {'Status':<10} {'Revealed Prob':>13}")
    print(f"{'-'*20} {'-'*10} {'-'*13}")

    for commit_hash, (prob, nonce), axon in zip(
        pooled.commit_hashes, reveal_responses, reveal_axons
    ):
        data = f"{prob}{nonce}{event_id}{axon.hotkey}"
        expected = hashlib.sha256(data.encode()).hexdigest()

        if expected == commit_hash:
            valid_probs.append(prob)
            status = "VALID"
        else:
            valid_probs.append(None)
            status = "MISMATCH"

        print(f"{axon.hotkey:<20} {status:<10} {str(prob) if prob is not None else 'N/A':>13}")

    assert all(p is not None for p in valid_probs), "Some hashes didn't verify!"

    # ── 7. Scoring ────────────────────────────────────────────────────────────
    print(f"\n[SCORE] Computing rewards (market_prob={market_prob}, outcome={outcome})...")

    rewards = get_rewards(None, p_market=market_prob, outcome=outcome, responses=valid_probs)

    print(f"\n{'Miner':<20} {'Pred Prob':>9}  {'Reward':>8}  Note")
    print(f"{'-'*20} {'-'*9}  {'-'*8}  {'-'*30}")
    for axon, prob, reward in zip(reveal_axons, valid_probs, rewards):
        diff = prob - market_prob
        note = f"{'above' if diff > 0 else 'below'} market by {abs(diff):.4f}"
        print(f"{axon.hotkey:<20} {prob:>9.4f}  {reward:>8.4f}  {note}")

    print(f"\n  market_prob = {market_prob}  |  outcome = {outcome}")
    print(f"  best miner  = {reveal_axons[rewards.argmax()].hotkey}  (reward={rewards.max():.4f})")
    print(f"{'='*60}\n")

    assert len(rewards) == N_MINERS
    assert all(r >= 0 for r in rewards)


def test_commitment_rejected_after_deadline(setup):
    """Commitments should be rejected once the commit deadline has passed."""
    _, metagraph, _ = setup
    validator = FakeValidator(metagraph, None)
    event_id = str(uuid.uuid4())

    validator._event_pool.add_event(
        event_id=event_id,
        question="Test event",
        market_prob=0.5,
        commit_deadline=int(time.time()) - 1,  # already past
        market_close_ts=int(time.time()) + 3600,
    )

    accepted = validator._event_pool.add_commitment(
        event_id=event_id,
        miner_hotkey="miner-hotkey-1",
        commitment_hash="abc" * 21 + "d",
    )
    assert not accepted, "Commitment should be rejected after deadline"
    print("\n[PASS] Commitment correctly rejected after deadline.")


def test_event_list_only_returns_open_events(setup):
    """EventList handler should only return OPEN events."""
    _, metagraph, dendrite = setup
    validator = FakeValidator(metagraph, dendrite)
    event_id = str(uuid.uuid4())

    validator._event_pool.add_event(
        event_id=event_id,
        question="Open event",
        market_prob=0.5,
        commit_deadline=int(time.time()) + 9999,
        market_close_ts=int(time.time()) + 99999,
    )
    # Close commits so it's no longer OPEN
    validator._event_pool.close_commits(event_id, metagraph)

    synapse = EventList()
    result = asyncio.run(forward_event_list(validator, synapse))
    assert len(result.events) == 0, "Closed event should not appear in EventList"
    print("\n[PASS] EventList correctly excludes non-OPEN events.")
