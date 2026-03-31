"""
Generate realistic demo data for the Probity dashboard.

Produces:
  - swpe_oracle.jsonl  (SWPE oracle records)
  - skill_tracker.json (RollingSkillTracker state)

Usage:
    PYTHONPATH=. python scripts/generate_demo_data.py [--output-dir DIR]
"""

import argparse
import json
import os
import random
import time

from template.validator.reward import RollingSkillTracker, compute_skill, compute_swpe

EVENTS = [
    ("0xabc1def200001", "Will BTC exceed $150k before July 2026?", 0.22, 0),
    ("0xabc1def200002", "Will the Fed cut rates in Q2 2026?", 0.61, 1),
    ("0xabc1def200003", "Will ETH/BTC ratio exceed 0.06 by June 2026?", 0.18, 0),
    ("0xabc1def200004", "Will Solana TVL exceed $20B by May 2026?", 0.45, 1),
    ("0xabc1def200005", "Will SEC approve spot ETH ETF options by April 2026?", 0.72, 1),
    ("0xabc1def200006", "Will Polymarket monthly volume exceed $3B in March 2026?", 0.38, 0),
    ("0xabc1def200007", "Will Bitcoin dominance drop below 55% by June 2026?", 0.33, 1),
    ("0xabc1def200008", "Will Nvidia stock exceed $200 by May 2026?", 0.58, 1),
    ("0xabc1def200009", "Will US CPI fall below 2.5% in Q2 2026?", 0.41, 0),
    ("0xabc1def20000a", "Will Elon Musk post 400+ tweets in any week of April 2026?", 0.15, 0),
]

# accuracy = how strongly this miner pushes toward the correct outcome.
# Positive = skilled (beats market), negative = contrarian/noise, zero = random.
MINERS = [
    {"uid": 0, "name": "AlphaForecaster", "accuracy": 0.15},
    {"uid": 1, "name": "BetaQuant",       "accuracy": 0.10},
    {"uid": 2, "name": "GammaOracle",     "accuracy": -0.08},
    {"uid": 3, "name": "DeltaML",         "accuracy": 0.12},
    {"uid": 4, "name": "EpsilonLLM",      "accuracy": 0.06},
    {"uid": 5, "name": "ZetaStats",       "accuracy": -0.12},
    {"uid": 6, "name": "EtaSignal",       "accuracy": 0.04},
    {"uid": 7, "name": "ThetaBayes",      "accuracy": 0.18},
    {"uid": 8, "name": "IotaDeep",        "accuracy": -0.03},
    {"uid": 9, "name": "KappaHedge",      "accuracy": 0.08},
]


def clamp(p):
    return max(0.01, min(0.99, p))


def main():
    parser = argparse.ArgumentParser(description="Generate demo data for Probity dashboard")
    parser.add_argument("--output-dir", default=".", help="Output directory")
    args = parser.parse_args()

    random.seed(42)
    os.makedirs(args.output_dir, exist_ok=True)

    n_miners = len(MINERS)
    tracker = RollingSkillTracker(n=n_miners)
    swpe_records = []
    base_ts = int(time.time()) - len(EVENTS) * 3600

    for i, (eid, question, market_prob, outcome) in enumerate(EVENTS):
        ts = base_ts + i * 3600
        probs = []
        uids = []

        for miner in MINERS:
            noise = random.gauss(0, 0.03)
            # Skilled miners push toward the correct outcome
            direction = (outcome - market_prob)  # positive if outcome=1 and market < 1
            p = clamp(market_prob + miner["accuracy"] * direction + noise)
            probs.append(p)
            uids.append(miner["uid"])

        valid_skills = []
        valid_uids = []
        for uid, p in zip(uids, probs):
            s = compute_skill(p, market_prob, outcome)
            valid_skills.append(s)
            valid_uids.append(uid)
        tracker.update(valid_uids, valid_skills)

        swpe = compute_swpe(probs, uids, tracker)
        swpe_records.append({
            "ts": ts,
            "event_id": eid,
            "question": question,
            "swpe": round(swpe, 6) if swpe else 0.5,
            "outcome": outcome,
            "market_prob": round(market_prob, 4),
            "n_miners": n_miners,
        })

    oracle_path = os.path.join(args.output_dir, "swpe_oracle.jsonl")
    with open(oracle_path, "w") as f:
        for r in swpe_records:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(swpe_records)} records to {oracle_path}")

    skill_path = os.path.join(args.output_dir, "skill_tracker.json")
    state = tracker.save()
    state["miner_names"] = [m["name"] for m in MINERS]
    with open(skill_path, "w") as f:
        json.dump(state, f, indent=2)
    print(f"Wrote skill tracker to {skill_path}")


if __name__ == "__main__":
    main()
