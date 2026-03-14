# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Probity Subnet

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import time
import hashlib
import bittensor as bt

from template.protocol import Commit, Reveal
from template.validator.reward import get_rewards
from template.validator.event_source import fetch_active_events
from template.validator.event_resolver import fetch_outcome
from template.utils.uids import get_random_uids


async def forward(self):
    """
    The forward function is called by the validator every time step.
    In Probity, it consists of:
      1. Event Ingestion  — pick a live market from Polymarket
      2. Commit Phase     — miners commit hashed predictions
      3. Reveal Phase     — miners reveal predictions + nonces
      4. Validation       — verify hashes
      5. Scoring          — compute rewards and update scores
      6. Event Resolution — (async) score past events when they resolve
    """
    miner_uids = get_random_uids(self, k=self.config.neuron.sample_size, exclude=[self.uid])
    axons = [self.metagraph.axons[uid] for uid in miner_uids]

    if len(miner_uids) == 0:
        bt.logging.warning("No miners available to query.")
        return

    # ── 1. Event Ingestion ──────────────────────────────────────────────────
    events = fetch_active_events(limit=5)

    if events:
        event = events[0]
        event_id   = event.event_id
        market_prob = event.market_prob
        bt.logging.info(
            f"[Event] {event.question[:80]} | "
            f"id={event_id[:12]}... | market_prob={market_prob:.4f}"
        )
    else:
        # Fallback: skip this round if Polymarket is unreachable
        bt.logging.warning(
            "Could not fetch live events from Polymarket. Skipping round."
        )
        return

    bt.logging.info(f"Querying {len(miner_uids)} miners: {miner_uids.tolist()}")

    # ── 2. Commit Phase ─────────────────────────────────────────────────────
    commit_synapse = Commit(
        event_id=event_id,
        market_prob=market_prob,
        commit_deadline=int(time.time()) + 10,
    )

    commit_responses = await self.dendrite(
        axons=axons,
        synapse=commit_synapse,
        deserialize=True,
    )

    bt.logging.info(f"Received Commit hashes: {commit_responses}")

    # Give miners a moment before the reveal window opens
    time.sleep(2)

    # ── 3. Reveal Phase ─────────────────────────────────────────────────────
    reveal_synapse = Reveal(event_id=event_id)

    reveal_responses = await self.dendrite(
        axons=axons,
        synapse=reveal_synapse,
        deserialize=True,
    )

    bt.logging.info(f"Received Reveal responses: {reveal_responses}")

    # ── 4. Hash Verification ────────────────────────────────────────────────
    valid_probabilities = []

    for commit_hash, reveal_tuple, axon in zip(commit_responses, reveal_responses, axons):
        if not commit_hash or not reveal_tuple:
            valid_probabilities.append(None)
            continue

        prob, nonce = reveal_tuple
        if prob is None or nonce is None:
            valid_probabilities.append(None)
            continue

        data_to_hash = f"{prob}_{nonce}_{event_id}_{axon.hotkey}"
        expected_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()

        if expected_hash == commit_hash:
            bt.logging.info(f"  ✓ {axon.hotkey[:8]}... hash verified | prob={prob:.4f}")
            valid_probabilities.append(prob)
        else:
            bt.logging.warning(f"  ✗ {axon.hotkey[:8]}... hash MISMATCH!")
            valid_probabilities.append(None)

    # ── 5. Event Resolution & Scoring ───────────────────────────────────────
    # Try to resolve the event immediately; Polymarket markets usually resolve
    # within seconds of the end date, so this may return None for futures.
    resolved = fetch_outcome(event_id)

    if resolved is not None:
        outcome = resolved.outcome
        bt.logging.info(
            f"[Resolution] event={event_id[:12]}... resolved → outcome={outcome}"
        )
        rewards = get_rewards(
            self,
            p_market=market_prob,
            outcome=outcome,
            responses=valid_probabilities,
        )
        bt.logging.info(
            f"Valid probs: {[round(p, 4) if p else None for p in valid_probabilities]}"
        )
        bt.logging.info(f"Rewards:     {rewards}")
        self.update_scores(rewards, miner_uids)
    else:
        bt.logging.info(
            f"[Resolution] event={event_id[:12]}... not yet resolved — "
            "storing for deferred scoring."
        )
        # Store the round so it can be scored when the event resolves.
        # The deferred scoring loop (run separately) will call
        # fetch_outcome() again and call update_scores() once resolved.
        if not hasattr(self, "_pending_rounds"):
            self._pending_rounds = []
        self._pending_rounds.append({
            "event_id": event_id,
            "market_prob": market_prob,
            "valid_probabilities": valid_probabilities,
            "miner_uids": miner_uids,
            "stored_at": int(time.time()),
        })
        bt.logging.info(
            f"  {len(self._pending_rounds)} round(s) awaiting resolution."
        )

    # ── 6. Flush pending rounds that have since resolved ────────────────────
    _flush_pending_rounds(self)

    time.sleep(5)


def _flush_pending_rounds(self):
    """
    Iterate over stored pending rounds and score any that have now resolved.
    Called at the end of every forward pass so deferred scoring is
    best-effort without requiring a separate process.
    """
    if not hasattr(self, "_pending_rounds") or not self._pending_rounds:
        return

    still_pending = []
    for round_data in self._pending_rounds:
        resolved = fetch_outcome(round_data["event_id"])
        if resolved is None:
            still_pending.append(round_data)
            continue

        bt.logging.info(
            f"[Deferred] Scoring round for event={round_data['event_id'][:12]}... "
            f"outcome={resolved.outcome}"
        )
        rewards = get_rewards(
            self,
            p_market=round_data["market_prob"],
            outcome=resolved.outcome,
            responses=round_data["valid_probabilities"],
        )
        bt.logging.info(f"  Deferred rewards: {rewards}")
        self.update_scores(rewards, round_data["miner_uids"])

    self._pending_rounds = still_pending
