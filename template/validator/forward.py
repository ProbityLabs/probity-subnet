import hashlib
import time

import bittensor as bt

from template.protocol import Commit, Reveal
from template.validator.reward import get_rewards, RollingSkillTracker, compute_swpe
from template.validator.event_source import fetch_active_events
from template.validator.event_resolver import fetch_outcome
from template.validator.event_pool import EventPool
from template.utils.uids import get_random_uids

# How long (seconds) miners have to commit before reveals are sent.
# Override on the validator instance (self.commit_window_seconds) for tests.
COMMIT_WINDOW_SECONDS = 30


async def forward(self):
    """
    Validator forward pass.

    One pass handles all three event lifecycle stages in parallel:
      1. Commit   — pick a new event, send Commit synapse to miners
      2. Reveal   — for events past commit deadline, send Reveal + verify hashes
      3. Score    — for revealed events whose outcome is now known, score + SWPE
    """
    # ── Initialise persistent state ─────────────────────────────────────────
    if not hasattr(self, "_event_pool"):
        self._event_pool = EventPool()
    if not hasattr(self, "_skill_tracker"):
        self._skill_tracker = RollingSkillTracker(n=int(self.metagraph.n))
    else:
        # Resize if metagraph has grown or shrunk since last save
        self._skill_tracker.resize(int(self.metagraph.n))

    commit_window = getattr(self, "commit_window_seconds", COMMIT_WINDOW_SECONDS)

    miner_uids = get_random_uids(self, k=self.config.neuron.sample_size, exclude=[self.uid])
    axons = [self.metagraph.axons[uid] for uid in miner_uids]

    # ── 1. Commit Phase ──────────────────────────────────────────────────────
    if len(miner_uids) == 0:
        bt.logging.warning("No miners available to query.")
    else:
        events = fetch_active_events(limit=5)
        if not events:
            bt.logging.warning("Could not fetch live events from Polymarket.")
        else:
            # Add at most one new event per forward pass
            for event in events:
                if event.event_id in self._event_pool:
                    continue

                commit_deadline = int(time.time()) + commit_window
                commit_synapse = Commit(
                    event_id=event.event_id,
                    market_prob=event.market_prob,
                    commit_deadline=commit_deadline,
                    question=event.question,
                )
                commit_responses = await self.dendrite(
                    axons=axons,
                    synapse=commit_synapse,
                    deserialize=True,
                )
                self._event_pool.add(
                    event_id=event.event_id,
                    question=event.question,
                    market_prob=event.market_prob,
                    commit_deadline=commit_deadline,
                    miner_uids=miner_uids.copy(),
                    commit_hashes=list(commit_responses),
                )
                bt.logging.info(
                    f"[Commit] {event.question[:60]} | "
                    f"id={event.event_id[:12]}... | "
                    f"miners={len(miner_uids)} | deadline+{commit_window}s"
                )
                break  # one new event per pass is enough

    # ── 2. Reveal Phase ──────────────────────────────────────────────────────
    for pooled in self._event_pool.ready_for_reveal():
        reveal_axons = [self.metagraph.axons[uid] for uid in pooled.miner_uids]
        reveal_synapse = Reveal(event_id=pooled.event_id)

        reveal_responses = await self.dendrite(
            axons=reveal_axons,
            synapse=reveal_synapse,
            deserialize=True,
        )

        valid_probs = _verify_hashes(pooled, reveal_responses, reveal_axons)
        self._event_pool.mark_revealed(pooled.event_id, valid_probs)

        n_valid = sum(1 for p in valid_probs if p is not None)
        bt.logging.info(
            f"[Reveal] {pooled.event_id[:12]}... "
            f"valid={n_valid}/{len(valid_probs)}"
        )

    # ── 3. Score + SWPE ──────────────────────────────────────────────────────
    for pooled in self._event_pool.ready_for_scoring():
        resolved = fetch_outcome(pooled.event_id)
        if resolved is None:
            bt.logging.debug(f"[Score] {pooled.event_id[:12]}... not yet resolved")
            continue

        bt.logging.info(
            f"[Score] {pooled.event_id[:12]}... outcome={resolved.outcome}"
        )

        rewards = get_rewards(
            self,
            p_market=pooled.market_prob,
            outcome=resolved.outcome,
            responses=pooled.valid_probabilities,
            uids=pooled.miner_uids.tolist(),
            skill_tracker=self._skill_tracker,
        )
        bt.logging.info(
            f"  probs  : {[round(p, 4) if p else None for p in pooled.valid_probabilities]}"
        )
        bt.logging.info(f"  rewards: {[round(float(r), 4) for r in rewards]}")

        self.update_scores(rewards, pooled.miner_uids)
        self._event_pool.mark_scored(pooled.event_id)

        # SWPE — ensemble probability from skill-weighted miners
        swpe = compute_swpe(
            pooled.valid_probabilities,
            pooled.miner_uids.tolist(),
            self._skill_tracker,
        )
        if swpe is not None:
            bt.logging.info(
                f"[SWPE]  event={pooled.event_id[:12]}... "
                f"ensemble={swpe:.4f} | outcome={resolved.outcome}"
            )

    bt.logging.info(f"[Pool] {self._event_pool.summary()}")
    self._event_pool.prune()


def _verify_hashes(pooled, reveal_responses, axons) -> list:
    """Verify each miner's revealed (prob, nonce) against their committed hash."""
    valid_probs = []
    for commit_hash, reveal_tuple, axon in zip(
        pooled.commit_hashes, reveal_responses, axons
    ):
        if not commit_hash or not reveal_tuple:
            valid_probs.append(None)
            continue

        prob, nonce = reveal_tuple
        if prob is None or nonce is None:
            valid_probs.append(None)
            continue

        data = f"{prob}_{nonce}_{pooled.event_id}_{axon.hotkey}"
        expected = hashlib.sha256(data.encode()).hexdigest()

        if expected == commit_hash:
            bt.logging.info(f"  ✓ {axon.hotkey[:8]}... prob={prob:.4f}")
            valid_probs.append(prob)
        else:
            bt.logging.warning(f"  ✗ {axon.hotkey[:8]}... hash MISMATCH")
            valid_probs.append(None)

    return valid_probs
