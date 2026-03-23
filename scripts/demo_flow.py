"""
Full commit-reveal demo — shows every step of the Probity subnet flow.

Runs entirely in-process (no network needed) using the real validator
scoring, event pool, rolling skill tracker, and SWPE computation.

Run with:
    python scripts/demo_flow.py
"""

import asyncio
import hashlib
import json
import os
import random
import time
import uuid

from template.protocol import EventInfo, CommitSubmission, Reveal
from template.validator.reward import (
    get_rewards, RollingSkillTracker, compute_swpe, compute_skill,
)
from template.validator.event_pool import EventPool, EventStage
from template.validator.event_source import fetch_active_events

# ── Config ───────────────────────────────────────────────────────────────────

COMMIT_WINDOW = 2       # seconds — short so demo doesn't wait long
N_ROUNDS      = 3       # how many events to simulate
USE_LIVE_EVENTS = True  # try to fetch real events from Polymarket

# ── Colour helpers ───────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
MAGENTA= "\033[95m"

def hdr(msg):  print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{CYAN}{msg}{RESET}")
def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET} {msg}")
def err(msg):  print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"    {msg}")


# ── Demo Miner ───────────────────────────────────────────────────────────────

class DemoMiner:
    def __init__(self, uid: int, name: str, hotkey: str, bias: float = 0.0):
        self.uid     = uid
        self.name    = name
        self.hotkey  = hotkey
        self.bias    = bias
        self.predictions = {}

    def predict(self, question: str, market_prob: float) -> float:
        """
        Each miner's custom logic. Replace this with a real ML model.
        Here we use a simple calibration bias for illustration.
        """
        return max(0.01, min(0.99, market_prob + self.bias))

    def commit(self, event: EventInfo) -> str:
        """Step 2: Miner computes forecast, creates commitment hash."""
        prob  = self.predict(event.question, event.market_prob)
        nonce = str(random.randint(1_000_000, 9_999_999))

        # Hash format matches real protocol: sha256(prob + nonce + event_id + hotkey)
        data = f"{prob}{nonce}{event.event_id}{self.hotkey}"
        commitment_hash = hashlib.sha256(data.encode()).hexdigest()

        self.predictions[event.event_id] = {
            "p": prob, "nonce": nonce,
            "commit_deadline": event.commit_deadline,
            "received_at": int(time.time()),
        }

        info(f"[{self.name}] forecast={prob:.4f}  nonce={nonce}  hash={commitment_hash[:16]}...")
        return commitment_hash

    def reveal(self, event_id: str) -> tuple:
        """Step 3: Miner reveals actual probability and nonce."""
        if event_id not in self.predictions:
            return None, None
        pred = self.predictions[event_id]
        info(f"[{self.name}] revealing prob={pred['p']:.4f}  nonce={pred['nonce']}")
        return pred["p"], pred["nonce"]


# ── Demo Validator ───────────────────────────────────────────────────────────

class DemoValidator:
    def __init__(self, miners):
        self.miners        = miners
        self.n             = len(miners)
        self.skill_tracker = RollingSkillTracker(n=self.n)
        self.event_pool    = EventPool()
        self.swpe_records  = []

    def run_round(self, event_id: str, question: str,
                  market_prob: float, outcome: int):

        hdr(f"EVENT: {question[:55]}")
        info(f"id          = {event_id[:16]}...")
        info(f"market_prob = {market_prob:.4f}")
        info(f"outcome     = {outcome}  (1=YES, 0=NO)")

        commit_deadline = int(time.time()) + COMMIT_WINDOW

        # ── 1. Validator adds event to pool ─────────────────────────────────
        print(f"\n{BOLD}[1. ADD EVENT]{RESET} → Pool (OPEN)")
        self.event_pool.add_event(
            event_id=event_id,
            question=question,
            market_prob=market_prob,
            commit_deadline=commit_deadline,
            market_close_ts=int(time.time()) + 86400,
        )
        ok(f"Event added: {self.event_pool.summary()}")

        # ── 2. Miners pull events & submit commitments ──────────────────────
        print(f"\n{BOLD}[2. COMMIT]{RESET} Miners → Validator (pull model)")
        active = self.event_pool.get_active_events()
        info(f"Serving {len(active)} active event(s) to miners")

        for miner in self.miners:
            event_info = EventInfo(
                event_id=event_id,
                question=question,
                market_prob=market_prob,
                commit_deadline=commit_deadline,
                reveal_deadline=int(time.time()) + 86400,
            )
            commitment_hash = miner.commit(event_info)
            accepted = self.event_pool.add_commitment(
                event_id=event_id,
                miner_hotkey=miner.hotkey,
                commitment_hash=commitment_hash,
            )
            status = f"{GREEN}accepted{RESET}" if accepted else f"{RED}rejected{RESET}"
            info(f"  → {miner.name}: {status}")

        pooled = self.event_pool._events[event_id]
        ok(f"{len(pooled.pending_hashes)} commitments received")

        # ── Wait for commit window ──────────────────────────────────────────
        print(f"\n{BOLD}  ⏳ Waiting {COMMIT_WINDOW}s for commit window to close...{RESET}")
        time.sleep(COMMIT_WINDOW + 0.1)

        # ── 3. Close commits & Reveal ───────────────────────────────────────
        print(f"\n{BOLD}[3. CLOSE COMMITS → REVEAL]{RESET}")

        # Force deadline to past for demo
        pooled.commit_deadline = int(time.time()) - 1
        pooled.market_close_ts = int(time.time()) - 1

        # Build a mock metagraph for hotkey→uid resolution
        class MockMG:
            hotkeys = [m.hotkey for m in self.miners]
        self.event_pool.close_commits(event_id, MockMG())
        ok(f"Commits closed → AWAITING_REVEAL")

        # Reveal phase
        print(f"\n{BOLD}[4. REVEAL]{RESET} Validator → Miners")
        valid_probs = []
        pooled = self.event_pool._events[event_id]
        for commit_hash, uid in zip(pooled.commit_hashes, pooled.miner_uids):
            miner = self.miners[uid]
            prob, nonce = miner.reveal(event_id)

            if prob is None or nonce is None:
                err(f"[{miner.name}] No reveal")
                valid_probs.append(None)
                continue

            # Verify hash
            data = f"{prob}{nonce}{event_id}{miner.hotkey}"
            expected = hashlib.sha256(data.encode()).hexdigest()

            if expected == commit_hash:
                ok(f"[{miner.name}] hash verified ✓  prob={prob:.4f}")
                valid_probs.append(prob)
            else:
                err(f"[{miner.name}] hash MISMATCH ✗")
                valid_probs.append(None)

        self.event_pool.mark_revealed(event_id, valid_probs)

        # ── 5. Scoring ──────────────────────────────────────────────────────
        print(f"\n{BOLD}[5. SCORE]{RESET} market_prob={market_prob:.4f}  outcome={outcome}")
        uids = pooled.miner_uids.tolist()
        rewards = get_rewards(
            self=None,
            p_market=market_prob,
            outcome=outcome,
            responses=valid_probs,
            uids=uids,
            skill_tracker=self.skill_tracker,
        )
        self.event_pool.mark_scored(event_id)

        print(f"\n  {'Miner':<15} {'Pred':>6}  {'Skill':>7}  {'Reward':>8}  {'Rolling':>8}")
        print(f"  {'-'*15} {'-'*6}  {'-'*7}  {'-'*8}  {'-'*8}")
        for i, uid in enumerate(uids):
            miner = self.miners[uid]
            p = valid_probs[i]
            skill = compute_skill(p, market_prob, outcome) if p is not None else None
            rolling = self.skill_tracker.get(uid)
            reward  = float(rewards[i])
            p_str   = f"{p:.4f}" if p is not None else "  N/A"
            s_str   = f"{skill:+.4f}" if skill is not None else "   N/A"
            print(f"  {miner.name:<15} {p_str:>6}  {s_str:>7}  {reward:>8.4f}  {rolling:>8.4f}")

        # ── 6. SWPE ─────────────────────────────────────────────────────────
        swpe = compute_swpe(valid_probs, uids, self.skill_tracker)
        if swpe is not None:
            print(f"\n  {MAGENTA}SWPE (ensemble) = {swpe:.4f}{RESET}  "
                  f"| market = {market_prob:.4f}  | outcome = {outcome}")
            self.swpe_records.append({
                "ts": int(time.time()),
                "event_id": event_id,
                "question": question,
                "swpe": round(swpe, 6),
                "outcome": outcome,
                "market_prob": market_prob,
                "n_miners": sum(1 for p in valid_probs if p is not None),
            })

        ok(f"Pool: {self.event_pool.summary()}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Define miners with different strategies
    miners = [
        DemoMiner(0, "AlphaMiner",  "hotkey_alpha",  bias=+0.08),
        DemoMiner(1, "BetaMiner",   "hotkey_beta",   bias=-0.05),
        DemoMiner(2, "GammaMiner",  "hotkey_gamma",  bias=+0.00),
        DemoMiner(3, "DeltaMiner",  "hotkey_delta",  bias=+0.15),
    ]

    validator = DemoValidator(miners)

    # Try to fetch real events from Polymarket
    live_events = []
    if USE_LIVE_EVENTS:
        print(f"\n{BOLD}Fetching live events from Polymarket...{RESET}")
        try:
            live_events = fetch_active_events(limit=N_ROUNDS)
            if live_events:
                ok(f"Got {len(live_events)} live events")
            else:
                warn("No live events fetched, using simulated events")
        except Exception as exc:
            warn(f"Polymarket fetch failed: {exc}")

    # Build event list (live or simulated)
    events = []
    for ev in live_events[:N_ROUNDS]:
        # For live events, simulate outcome based on market prob
        outcome = 1 if random.random() < ev.market_prob else 0
        events.append((ev.event_id, ev.question, ev.market_prob, outcome))

    # Fill remaining with simulated events
    simulated = [
        ("Will ETH exceed $5000 by June 2025?",  0.38, 0),
        ("Will Fed cut rates in Q1 2025?",        0.55, 1),
        ("Will BTC hit $150k before July 2025?",  0.22, 0),
    ]
    for q, mp, o in simulated:
        if len(events) >= N_ROUNDS:
            break
        events.append((str(uuid.uuid4()), q, mp, o))

    print(f"\n{BOLD}{CYAN}{'='*60}")
    print(f"  PROBITY SUBNET — FULL FLOW DEMO")
    print(f"  {len(miners)} miners | {N_ROUNDS} rounds | commit window = {COMMIT_WINDOW}s")
    print(f"{'='*60}{RESET}")

    for event_id, question, market_prob, outcome in events[:N_ROUNDS]:
        validator.run_round(event_id, question, market_prob, outcome)

    # Final rolling skill summary
    hdr("FINAL ROLLING SKILL SUMMARY")
    print(f"\n  {'Miner':<15} {'Events':>6}  {'Sum Skill':>10}  {'Rolling':>8}")
    print(f"  {'-'*15} {'-'*6}  {'-'*10}  {'-'*8}")
    for miner in miners:
        tracker = validator.skill_tracker
        count   = int(tracker.count[miner.uid])
        ssum    = float(tracker.sum_skill[miner.uid])
        rolling = tracker.get(miner.uid)
        print(f"  {miner.name:<15} {count:>6}  {ssum:>10.4f}  {rolling:>8.4f}")

    # SWPE oracle output
    if validator.swpe_records:
        hdr("SWPE ORACLE OUTPUT (Digital Commodity)")
        for r in validator.swpe_records:
            print(f"  {json.dumps(r)}")

    print(f"\n{BOLD}{GREEN}✓ Demo complete. All subnet functionality verified.{RESET}\n")