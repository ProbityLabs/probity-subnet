import math
import numpy as np
from typing import List, Optional

import bittensor as bt


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def log_loss(p: float, y: int) -> float:
    eps = 1e-9
    p = max(min(p, 1 - eps), eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def compute_skill(p_miner: float, p_market: float, outcome: int) -> float:
    """Positive = miner beat the market; negative = miner was worse."""
    return log_loss(p_market, outcome) - log_loss(p_miner, outcome)


# ---------------------------------------------------------------------------
# Rolling Skill Tracker
# ---------------------------------------------------------------------------

N0_DEFAULT = 10.0  # Bayesian prior count


class RollingSkillTracker:
    """
    Tracks accumulated skill per miner uid across events.

    rolling_skill[uid] = sum_skill[uid] / (N0 + count[uid])

    N0 is a Bayesian smoothing prior: new miners start near zero and converge
    to their true average as they participate in more events.
    """

    def __init__(self, n: int, N0: float = N0_DEFAULT):
        self.N0 = N0
        self.sum_skill = np.zeros(n, dtype=np.float64)
        self.count = np.zeros(n, dtype=np.int64)

    def update(self, uids: List[int], skills: List[float]) -> None:
        for uid, skill in zip(uids, skills):
            self.sum_skill[uid] += skill
            self.count[uid] += 1

    def get(self, uid: int) -> float:
        return float(self.sum_skill[uid] / (self.N0 + self.count[uid]))

    def all(self) -> np.ndarray:
        return self.sum_skill / (self.N0 + self.count)

    def resize(self, n: int) -> None:
        """Resize when metagraph grows or shrinks."""
        old_n = len(self.sum_skill)
        if n == old_n:
            return
        new_sum = np.zeros(n, dtype=np.float64)
        new_count = np.zeros(n, dtype=np.int64)
        copy_len = min(old_n, n)
        new_sum[:copy_len] = self.sum_skill[:copy_len]
        new_count[:copy_len] = self.count[:copy_len]
        self.sum_skill = new_sum
        self.count = new_count

    def save(self) -> dict:
        return {
            "sum_skill": self.sum_skill.tolist(),
            "count": self.count.tolist(),
            "N0": self.N0,
        }

    @classmethod
    def load(cls, data: dict) -> "RollingSkillTracker":
        n = len(data["sum_skill"])
        tracker = cls(n=n, N0=data.get("N0", N0_DEFAULT))
        tracker.sum_skill = np.array(data["sum_skill"], dtype=np.float64)
        tracker.count = np.array(data["count"], dtype=np.int64)
        return tracker


# ---------------------------------------------------------------------------
# SWPE
# ---------------------------------------------------------------------------

def compute_swpe(
    valid_probs: List[Optional[float]],
    uids: List[int],
    skill_tracker: RollingSkillTracker,
) -> Optional[float]:
    """
    Skill-Weighted Probability Ensemble.

    SWPE = Σ_i [w_i * p_i] / Σ_i w_i
    where w_i = max(0, rolling_skill[i])

    Returns None if no miner has positive skill.
    """
    weighted_sum = 0.0
    weight_total = 0.0
    for prob, uid in zip(valid_probs, uids):
        if prob is None:
            continue
        w = max(0.0, skill_tracker.get(uid))
        weighted_sum += w * prob
        weight_total += w
    if weight_total == 0:
        return None
    return weighted_sum / weight_total


# ---------------------------------------------------------------------------
# Reward function
# ---------------------------------------------------------------------------

def get_rewards(
    self,
    p_market: float,
    outcome: int,
    responses: List[Optional[float]],
    uids: Optional[List[int]] = None,
    skill_tracker: Optional[RollingSkillTracker] = None,
) -> np.ndarray:
    """
    Compute per-miner rewards for a single scored event.

    If skill_tracker + uids are provided:
      1. Update the tracker with per-event skills
      2. Return weights based on accumulated rolling skill (preferred)
    Otherwise, fall back to per-event skill (original behaviour, for tests
    that don't pass a tracker).
    """
    beta = 5

    # Per-event skills
    per_event_skills: List[Optional[float]] = []
    for p in responses:
        if p is None:
            per_event_skills.append(None)
        else:
            per_event_skills.append(compute_skill(p, p_market, outcome))

    # Update rolling skill tracker
    if skill_tracker is not None and uids is not None:
        valid_uids = [uid for uid, s in zip(uids, per_event_skills) if s is not None]
        valid_skills = [s for s in per_event_skills if s is not None]
        skill_tracker.update(valid_uids, valid_skills)

    # Compute weights
    weights = []
    for i, skill in enumerate(per_event_skills):
        if skill is None:
            weights.append(0.0)
            continue
        if skill_tracker is not None and uids is not None:
            rs = skill_tracker.get(uids[i])
            weights.append(math.exp(beta * rs) if rs > 0 else 0.0)
        else:
            weights.append(math.exp(beta * skill) if skill > 0 else 0.0)

    return np.array(weights, dtype=np.float32)
