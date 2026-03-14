"""
End-to-end flow test for the Probity commit-reveal subnet.

Run with:
    pytest tests/test_flow.py -v -s

The -s flag keeps stdout visible so you can follow each step.
"""

import asyncio
import hashlib
import math
import time
import uuid
import random

import pytest

from template.mock import MockDendrite, MockMetagraph, MockSubtensor, MockWallet
from template.protocol import Commit, Reveal
from template.validator.reward import get_rewards


N_MINERS = 4  # number of simulated miners


@pytest.fixture
def setup():
    wallet = MockWallet()
    subtensor = MockSubtensor(netuid=1, n=N_MINERS, wallet=wallet)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    dendrite = MockDendrite(wallet=wallet)
    return wallet, metagraph, dendrite


def test_commit_reveal_flow(setup):
    wallet, metagraph, dendrite = setup
    axons = metagraph.axons

    # ── 1. Event Setup (simulated) ───────────────────────────────────────────
    event_id = str(uuid.uuid4())
    market_prob = round(random.uniform(0.2, 0.8), 4)
    outcome = 1 if random.random() < market_prob else 0

    print(f"\n{'='*60}")
    print(f"EVENT      : {event_id}")
    print(f"market_prob: {market_prob}")
    print(f"outcome    : {outcome}  (1=YES, 0=NO)")
    print(f"miners     : {[a.hotkey for a in axons]}")
    print(f"{'='*60}")

    # ── 2. Commit Phase ──────────────────────────────────────────────────────
    print("\n[COMMIT] Sending Commit synapses to miners...")

    commit_responses = asyncio.run(
        dendrite(
            axons,
            synapse=Commit(event_id=event_id, market_prob=market_prob, commit_deadline=int(time.time()) - 1),
            timeout=5,
            deserialize=True,
        )
    )

    print(f"\n{'Miner':<20} {'Commitment Hash'}")
    print(f"{'-'*20} {'-'*64}")
    for axon, h in zip(axons, commit_responses):
        print(f"{axon.hotkey:<20} {h}")

    assert len(commit_responses) == len(axons)
    assert all(isinstance(h, str) and len(h) == 64 for h in commit_responses)

    # ── 3. Reveal Phase ──────────────────────────────────────────────────────
    print(f"\n[REVEAL] Sending Reveal synapses to miners...")

    reveal_responses = asyncio.run(
        dendrite(
            axons,
            synapse=Reveal(event_id=event_id),
            timeout=5,
            deserialize=True,
        )
    )

    print(f"\n{'Miner':<20} {'Prob':>6}  {'Nonce'}")
    print(f"{'-'*20} {'-'*6}  {'-'*10}")
    for axon, (prob, nonce) in zip(axons, reveal_responses):
        print(f"{axon.hotkey:<20} {prob:>6.4f}  {nonce}")

    # ── 4. Hash Verification ─────────────────────────────────────────────────
    print(f"\n[VERIFY] Checking that revealed values match committed hashes...")

    valid_probs = []
    print(f"\n{'Miner':<20} {'Status':<10} {'Revealed Prob':>13}")
    print(f"{'-'*20} {'-'*10} {'-'*13}")
    for commit_hash, (prob, nonce), axon in zip(commit_responses, reveal_responses, axons):
        data = f"{prob}_{nonce}_{event_id}_{axon.hotkey}"
        expected = hashlib.sha256(data.encode()).hexdigest()

        if expected == commit_hash:
            valid_probs.append(prob)
            status = "✓ VALID"
        else:
            valid_probs.append(None)
            status = "✗ MISMATCH"

        print(f"{axon.hotkey:<20} {status:<10} {str(prob) if prob is not None else 'N/A':>13}")

    assert all(p is not None for p in valid_probs), "Some hashes didn't verify!"

    # ── 5. Scoring ───────────────────────────────────────────────────────────
    print(f"\n[SCORE] Computing rewards (market_prob={market_prob}, outcome={outcome})...")

    rewards = get_rewards(None, p_market=market_prob, outcome=outcome, responses=valid_probs)

    print(f"\n{'Miner':<20} {'Pred Prob':>9}  {'Reward':>8}  Note")
    print(f"{'-'*20} {'-'*9}  {'-'*8}  {'-'*30}")
    for axon, prob, reward in zip(axons, valid_probs, rewards):
        diff = prob - market_prob
        note = f"{'above' if diff > 0 else 'below'} market by {abs(diff):.4f}"
        print(f"{axon.hotkey:<20} {prob:>9.4f}  {reward:>8.4f}  {note}")

    print(f"\n  market_prob = {market_prob}  |  outcome = {outcome}")
    print(f"  best miner  = {axons[rewards.argmax()].hotkey}  (reward={rewards.max():.4f})")
    print(f"{'='*60}\n")

    assert len(rewards) == len(axons)
    assert all(r >= 0 for r in rewards)
