"""
Full commit-reveal demo — shows every step between validator and miners.

Uses the real miner logic from neurons/miner.py (forward_commit / forward_reveal)
and the real validator scoring, but wired together in-process so you can see
exactly what happens without needing a running localnet.

Run with:
    python scripts/demo_flow.py
"""

import asyncio
import hashlib
import random
import time
import uuid

import bittensor as bt

from template.protocol import Commit, Reveal
from template.validator.reward import get_rewards, RollingSkillTracker, compute_swpe

# ── Config ───────────────────────────────────────────────────────────────────

COMMIT_WINDOW = 1   # seconds — short so demo doesn't wait long
N_ROUNDS      = 3   # how many events to simulate

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

# ── Demo Miner ────────────────────────────────────────────────────────────────
# Mirrors neurons/miner.py forward_commit / forward_reveal exactly.
# The only thing each miner customises is their predict() function.

class DemoMiner:
    def __init__(self, name: str, bias: float = 0.0):
        """
        bias > 0  → miner thinks YES is more likely than market
        bias < 0  → miner thinks YES is less likely than market
        """
        self.name   = name
        self.bias   = bias
        self.predictions = {}

    def predict(self, market_prob: float) -> float:
        """
        Each miner's custom logic. Replace this with a real ML model.
        Here we use a simple calibration bias for illustration.
        """
        return max(0.01, min(0.99, market_prob + self.bias))

    # ---------- mirrors neurons/miner.py forward_commit ----------
    def forward_commit(self, synapse: Commit) -> str:
        prob  = self.predict(synapse.market_prob)
        nonce = str(random.randint(1_000_000, 9_999_999))

        self.predictions[synapse.event_id] = {
            "p":               prob,
            "nonce":           nonce,
            "commit_deadline": synapse.commit_deadline,
        }

        data_to_hash = f"{prob}_{nonce}_{synapse.event_id}_{self.name}"
        commitment_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()

        info(f"[{self.name}] predict={prob:.4f}  nonce={nonce}  hash={commitment_hash[:16]}...")
        return commitment_hash

    # ---------- mirrors neurons/miner.py forward_reveal ----------
    def forward_reveal(self, synapse: Reveal):
        if synapse.event_id not in self.predictions:
            warn(f"[{self.name}] No prediction found for event {synapse.event_id[:8]}...")
            return None, None

        pred = self.predictions[synapse.event_id]

        if time.time() < pred["commit_deadline"]:
            warn(f"[{self.name}] Refusing reveal — commit deadline not yet passed!")
            return None, None

        prob  = pred["p"]
        nonce = pred["nonce"]
        info(f"[{self.name}] revealing prob={prob:.4f}  nonce={nonce}")
        return prob, nonce


# ── Demo Validator ────────────────────────────────────────────────────────────

class DemoValidator:
    def __init__(self, miners):
        self.miners        = miners
        self.n             = len(miners)
        self.skill_tracker = RollingSkillTracker(n=self.n)

    def run_round(self, event_id: str, question: str,
                  market_prob: float, outcome: int):

        hdr(f"EVENT: {question[:55]}")
        info(f"id          = {event_id[:16]}...")
        info(f"market_prob = {market_prob:.4f}")
        info(f"outcome     = {outcome}  (1=YES, 0=NO)")

        commit_deadline = int(time.time()) + COMMIT_WINDOW

        # ── 1. Commit Phase ──────────────────────────────────────────────────
        print(f"\n{BOLD}[COMMIT]{RESET} Validator → all miners")
        commit_synapse = Commit(
            event_id=event_id,
            market_prob=market_prob,
            commit_deadline=commit_deadline,
        )
        commit_hashes = []
        for miner in self.miners:
            h = miner.forward_commit(commit_synapse)
            commit_hashes.append(h)

        print(f"\n{BOLD}  Waiting {COMMIT_WINDOW}s for commit window to close...{RESET}")
        time.sleep(COMMIT_WINDOW + 0.1)

        # ── 2. Reveal Phase ──────────────────────────────────────────────────
        print(f"\n{BOLD}[REVEAL]{RESET} Validator → all miners")
        reveal_synapse = Reveal(event_id=event_id)
        reveal_responses = []
        for miner in self.miners:
            prob, nonce = miner.forward_reveal(reveal_synapse)
            reveal_responses.append((prob, nonce))

        # ── 3. Hash Verification ─────────────────────────────────────────────
        print(f"\n{BOLD}[VERIFY]{RESET} Checking hashes")
        valid_probs = []
        for i, (miner, commit_hash, (prob, nonce)) in enumerate(
            zip(self.miners, commit_hashes, reveal_responses)
        ):
            if prob is None or nonce is None:
                err(f"[{miner.name}] No reveal received")
                valid_probs.append(None)
                continue

            data     = f"{prob}_{nonce}_{event_id}_{miner.name}"
            expected = hashlib.sha256(data.encode()).hexdigest()

            if expected == commit_hash:
                ok(f"[{miner.name}] hash verified  prob={prob:.4f}")
                valid_probs.append(prob)
            else:
                err(f"[{miner.name}] hash MISMATCH — discarded")
                valid_probs.append(None)

        # ── 4. Scoring ───────────────────────────────────────────────────────
        print(f"\n{BOLD}[SCORE]{RESET} market_prob={market_prob:.4f}  outcome={outcome}")
        uids = list(range(self.n))
        rewards = get_rewards(
            self=None,
            p_market=market_prob,
            outcome=outcome,
            responses=valid_probs,
            uids=uids,
            skill_tracker=self.skill_tracker,
        )

        print(f"\n  {'Miner':<15} {'Pred':>6}  {'Skill':>7}  {'Reward':>8}  {'Rolling':>8}")
        print(f"  {'-'*15} {'-'*6}  {'-'*7}  {'-'*8}  {'-'*8}")
        for i, miner in enumerate(self.miners):
            p = valid_probs[i]
            if p is not None:
                from template.validator.reward import compute_skill
                skill = compute_skill(p, market_prob, outcome)
            else:
                skill = None
            rolling = self.skill_tracker.get(i)
            reward  = float(rewards[i])
            p_str   = f"{p:.4f}" if p is not None else "  N/A"
            s_str   = f"{skill:+.4f}" if skill is not None else "   N/A"
            print(f"  {miner.name:<15} {p_str:>6}  {s_str:>7}  {reward:>8.4f}  {rolling:>8.4f}")

        # ── 5. SWPE ──────────────────────────────────────────────────────────
        swpe = compute_swpe(valid_probs, uids, self.skill_tracker)
        if swpe is not None:
            print(f"\n  {MAGENTA}SWPE (ensemble) = {swpe:.4f}{RESET}  "
                  f"| market = {market_prob:.4f}  | outcome = {outcome}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Define miners with different strategies
    miners = [
        DemoMiner("AlphaMiner",  bias=+0.08),   # consistently bullish
        DemoMiner("BetaMiner",   bias=-0.05),   # consistently bearish
        DemoMiner("GammaMiner",  bias=+0.00),   # copies market exactly
        DemoMiner("DeltaMiner",  bias=+0.15),   # very aggressive bullish
    ]

    validator = DemoValidator(miners)

    # Simulate N_ROUNDS events
    events = [
        ("Will ETH exceed $5000 by June 2025?",  0.38, 0),
        ("Will Fed cut rates in Q1 2025?",        0.55, 1),
        ("Will BTC hit $150k before July 2025?",  0.22, 0),
    ]

    print(f"\n{BOLD}{CYAN}{'='*60}")
    print(f"  PROBITY SUBNET — COMMIT-REVEAL DEMO")
    print(f"  {len(miners)} miners | {N_ROUNDS} rounds | commit window = {COMMIT_WINDOW}s")
    print(f"{'='*60}{RESET}")

    for i, (question, market_prob, outcome) in enumerate(events[:N_ROUNDS]):
        event_id = str(uuid.uuid4())
        validator.run_round(event_id, question, market_prob, outcome)

    # Final rolling skill summary
    hdr("FINAL ROLLING SKILL SUMMARY")
    print(f"\n  {'Miner':<15} {'Events':>6}  {'Sum Skill':>10}  {'Rolling':>8}")
    print(f"  {'-'*15} {'-'*6}  {'-'*10}  {'-'*8}")
    for i, miner in enumerate(miners):
        tracker = validator.skill_tracker
        count   = int(tracker.count[i])
        ssum    = float(tracker.sum_skill[i])
        rolling = tracker.get(i)
        print(f"  {miner.name:<15} {count:>6}  {ssum:>10.4f}  {rolling:>8.4f}")

    print(f"\n{BOLD}{GREEN}Demo complete.{RESET}\n")
