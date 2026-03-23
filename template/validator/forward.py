import hashlib
import time
from datetime import datetime

import bittensor as bt

from template.protocol import CommitSubmission, EventInfo, EventList, Reveal
from template.validator.reward import get_rewards, RollingSkillTracker, compute_swpe
from template.validator.event_source import fetch_active_events, _fetch_clob_price
from template.validator.event_resolver import fetch_outcome
from template.validator.event_pool import EventPool, EventStage

# How long (seconds) miners have to commit before reveals are sent.
# Override on the validator instance (self.commit_window_seconds) for tests.
COMMIT_WINDOW_SECONDS = 48 * 3600  # 48 hours

# How often (seconds) to fetch new events from Polymarket.
# Whitepaper §3.1: validators ingest new events every 6 hours.
EVENT_FETCH_INTERVAL = 6 * 3600  # 6 hours


def _parse_end_ts(end_date_iso: str) -> int:
    """Parse ISO-8601 end date to unix timestamp. Falls back to 7 days from now."""
    if end_date_iso:
        try:
            dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            pass
    return int(time.time()) + 7 * 24 * 3600


def _init_state(self) -> None:
    """Initialise persistent state on the first forward call."""
    if not hasattr(self, "_event_pool"):
        self._event_pool = EventPool()
    if not hasattr(self, "_skill_tracker"):
        self._skill_tracker = RollingSkillTracker(n=int(self.metagraph.n))
    else:
        self._skill_tracker.resize(int(self.metagraph.n))
    if not hasattr(self, "_last_event_fetch"):
        self._last_event_fetch = 0  # epoch — forces fetch on first call


async def forward(self):
    """
    Validator forward pass (periodic loop).

    1. Fetch new events from Polymarket → add to pool as OPEN
    2. Close commits for OPEN events past their deadline → AWAITING_REVEAL
    3. Send Reveal to committed miners → verify hashes → AWAITING_RESOLUTION
    4. Score AWAITING_RESOLUTION events whose outcome is known
    """
    _init_state(self)

    commit_window = getattr(self, "commit_window_seconds", COMMIT_WINDOW_SECONDS)

    # ── 1. Add new events (rate-limited to every 6 hours) ───────────────────
    fetch_interval = getattr(self, "event_fetch_interval", EVENT_FETCH_INTERVAL)
    now = int(time.time())
    if now - self._last_event_fetch < fetch_interval:
        bt.logging.debug(
            f"[Fetch] Skipping event fetch — next in "
            f"{fetch_interval - (now - self._last_event_fetch)}s"
        )
        events = []
    else:
        events = fetch_active_events(limit=5)
        self._last_event_fetch = now

    if not events:
        pass  # no new events to add this cycle
    else:
        for event in events:
            if event.event_id in self._event_pool:
                continue
            commit_deadline = int(time.time()) + commit_window
            self._event_pool.add_event(
                event_id=event.event_id,
                question=event.question,
                market_prob=event.market_prob,
                commit_deadline=commit_deadline,
                market_close_ts=_parse_end_ts(event.end_date_iso),
                resolution_criteria=getattr(event, "resolution_criteria", ""),
            )
            bt.logging.info(
                f"[Event] {event.question[:60]} | "
                f"id={event.event_id[:12]}... | deadline+{commit_window}s"
            )

    # ── 2. Close commits for past-deadline events ─────────────────────────────
    for pooled in self._event_pool.ready_for_reveal():
        # Snapshot a fresh market_prob at commit-close time (not ingestion time)
        # so the scoring baseline reflects the market at the moment miners locked in.
        fresh_prob = _fetch_clob_price(pooled.event_id)
        if fresh_prob is not None:
            self._event_pool.update_market_prob(pooled.event_id, fresh_prob)
            bt.logging.info(
                f"[Commits Closed] {pooled.event_id[:12]}... "
                f"market_prob updated {pooled.market_prob:.4f} → {fresh_prob:.4f}"
            )
        self._event_pool.close_commits(pooled.event_id, self.metagraph)
        bt.logging.info(
            f"[Commits Closed] {pooled.event_id[:12]}... "
            f"commitments={len(pooled.pending_hashes)}"
        )

    # ── 3. Reveal Phase ───────────────────────────────────────────────────────
    for pooled in list(self._event_pool._events.values()):
        if pooled.stage != EventStage.AWAITING_REVEAL:
            continue

        if pooled.miner_uids is None or len(pooled.miner_uids) == 0:
            self._event_pool.mark_revealed(pooled.event_id, [])
            bt.logging.info(f"[Reveal] {pooled.event_id[:12]}... no committed miners")
            continue

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
            f"[Reveal] {pooled.event_id[:12]}... valid={n_valid}/{len(valid_probs)}"
        )

    # ── 4. Score ─────────────────────────────────────────────────────────────
    for pooled in self._event_pool.ready_for_scoring():
        resolved = fetch_outcome(pooled.event_id)
        if resolved is None:
            bt.logging.debug(f"[Score] {pooled.event_id[:12]}... not yet resolved")
            continue

        bt.logging.info(f"[Score] {pooled.event_id[:12]}... outcome={resolved.outcome}")

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

        swpe = compute_swpe(
            pooled.valid_probabilities,
            pooled.miner_uids.tolist(),
            self._skill_tracker,
        )
        if swpe is not None:
            bt.logging.info(
                f"[SWPE] event={pooled.event_id[:12]}... "
                f"ensemble={swpe:.4f} | outcome={resolved.outcome}"
            )

    bt.logging.info(f"[Pool] {self._event_pool.summary()}")
    self._event_pool.prune()


async def forward_event_list(self, synapse: EventList) -> EventList:
    """
    Axon handler — pull protocol Step 1.
    Miners call this to get the list of active events they can predict on.
    """
    _init_state(self)
    active = self._event_pool.get_active_events()
    synapse.events = [
        EventInfo(
            event_id=e.event_id,
            question=e.question,
            market_prob=e.market_prob,
            commit_deadline=e.commit_deadline,
            reveal_deadline=e.market_close_ts,
            resolution_criteria=e.resolution_criteria,
        )
        for e in active
    ]
    bt.logging.debug(f"[EventList] serving {len(synapse.events)} active events")
    return synapse


async def forward_commit_submission(self, synapse: CommitSubmission) -> CommitSubmission:
    """
    Axon handler — pull protocol Step 2.
    Miners call this to submit a commitment hash for an event.
    """
    _init_state(self)
    miner_hotkey = synapse.dendrite.hotkey if synapse.dendrite else None
    if miner_hotkey is None:
        synapse.accepted = False
        return synapse
    accepted = self._event_pool.add_commitment(
        event_id=synapse.event_id,
        miner_hotkey=miner_hotkey,
        commitment_hash=synapse.commitment_hash,
    )
    synapse.accepted = accepted
    bt.logging.info(
        f"[CommitSubmission] hotkey={miner_hotkey[:8]}... "
        f"event={synapse.event_id[:12]}... accepted={accepted}"
    )
    return synapse


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

        data = f"{prob}{nonce}{pooled.event_id}{axon.hotkey}"
        expected = hashlib.sha256(data.encode()).hexdigest()

        if expected == commit_hash:
            bt.logging.info(f"  ✓ {axon.hotkey[:8]}... prob={prob:.4f}")
            valid_probs.append(prob)
        else:
            bt.logging.warning(f"  ✗ {axon.hotkey[:8]}... hash MISMATCH")
            valid_probs.append(None)

    return valid_probs
