# Probity — The Skill-Weighted Probability Subnet

A benchmark-driven, skill-weighted probability intelligence layer built on Bittensor.

Probity creates a decentralized superforecaster network where emission flows exclusively to persistent probabilistic outperformance versus market consensus.

---

## Quick Start

### Prerequisites

- Python 3.10+
- Bittensor CLI (`btcli`)

### Installation

```bash
git clone https://github.com/your-org/probity-subnet.git
cd probity-subnet
python3 -m venv venv && source venv/bin/activate
pip install -e .
```

### Running the Validator

```bash
python3 neurons/validator.py \
  --netuid <NETUID> \
  --subtensor.network test \
  --wallet.name <WALLET> \
  --wallet.hotkey <HOTKEY> \
  --probity.commit_window 172800
```

### Running the Miner

```bash
python3 neurons/miner.py \
  --netuid <NETUID> \
  --subtensor.network test \
  --wallet.name <WALLET> \
  --wallet.hotkey <HOTKEY>
```

Use `--local` flag when running miner and validator on the same machine.

### Demo (no network required)

```bash
python3 scripts/demo_flow.py
```

Runs the full commit-reveal-score-SWPE flow in-process with live Polymarket events.

### Running Tests

```bash
pytest tests/test_flow.py tests/test_forward_real.py -v
```

### Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--probity.beta` | 5.0 | Softmax temperature for exponential skill weighting |
| `--probity.N0` | 10.0 | Bayesian prior count for rolling skill smoothing |
| `--probity.commit_window` | 172800 (48h) | Commit window in seconds |

---

## Architecture

### Pull-Based Protocol

1. **Validator** fetches events from Polymarket and adds them to the event pool
2. **Miner** queries validator for active events (`EventList` synapse)
3. **Miner** computes forecast and submits commitment hash (`CommitSubmission` synapse)
4. After commit window closes, **Validator** sends `Reveal` request to miners
5. **Validator** verifies hashes, scores miners, computes SWPE, and sets weights on-chain

### Event Lifecycle

```
OPEN → AWAITING_REVEAL → AWAITING_RESOLUTION → SCORED
```

### Key Components

| File | Description |
|------|-------------|
| `neurons/validator.py` | Validator neuron with axon handlers and state persistence |
| `neurons/miner.py` | Miner neuron with pull-based commit-reveal |
| `template/validator/forward.py` | Core forward loop (fetch, close, reveal, score) |
| `template/validator/reward.py` | Log-loss, rolling skill tracker, SWPE computation |
| `template/validator/event_pool.py` | Event lifecycle management with persistence |
| `template/validator/event_source.py` | Polymarket Gamma + CLOB API integration |
| `template/validator/event_resolver.py` | Market resolution detection |
| `template/protocol.py` | Synapse definitions (EventList, CommitSubmission, Reveal) |
| `scripts/demo_flow.py` | Full flow demo with live Polymarket events |

### Digital Commodity: SWPE Oracle

After scoring, the validator produces a **Skill-Weighted Probability Ensemble** (SWPE) — a calibration-weighted consensus probability. These are persisted to `swpe_oracle.jsonl` as the subnet's digital commodity output.

---

# Design

Prediction markets are capital-weighted.

They reflect financial stake — not long-term calibration performance.

There is currently no decentralized system that:
- Identifies consistently well-calibrated forecasters
- Rewards probabilistic intelligence
- Produces a programmable skill-weighted probability layer

Market prices ≠ calibrated probabilities.

Probity fills this gap.

---

# 🧠 Core Mechanism

The only path to emission is persistent calibrated intelligence.

## 1. Forecast Submission

Miners submit a probability forecast:

p ∈ (0,1)

Each forecast corresponds to a binary event with verifiable resolution.

---

## 2. Log-Loss Scoring

After event resolution (y ∈ {0,1}):

LLᵢ = −(y log pᵢ + (1 − y) log(1 − pᵢ))

Log-loss is strictly proper.
Truthful reporting maximizes expected reward.

---

## 3. Relative Skill vs Market Baseline

Skillᵢ = LL_market − LLᵢ

Where:
- LL_market = log-loss of market probability at commit close
- LLᵢ = log-loss of miner forecast

Properties:
- Mirroring the market yields zero expected skill
- Random guessing yields zero expected skill
- Only genuine informational advantage produces positive skill

---

## 4. Rolling Aggregation

RollingSkillᵢ = rolling average over evaluation window W

This:
- Smooths short-term variance
- Prevents lucky streak dominance
- Requires persistent performance
- Stabilizes early participation

---

## 5. Exponential Weight Routing

wᵢ = exp(β × RollingSkillᵢ)

Weights are normalized:

wᵢ_normalized = wᵢ / Σ w

Emission ∝ normalized weight.

There are:
- No heuristic penalties
- No categorical reward tiers
- No manual adjustments

The system is deterministic and auditable.

---

# 🔐 Commit–Reveal Integrity

To prevent copying:

Commit Phase:
Miner submits hash of (probability + nonce + event_id + miner_hotkey)

Reveal Phase:
Miner reveals probability and nonce.

Validators verify hash consistency.

Late or invalid reveals are ignored.

---

# 🧑‍💻 Miner Specification

## Input

- Event metadata
- Resolution criteria
- Commit & reveal deadlines
- Market baseline probability at commit close

## Output

Single probability:

p ∈ (0,1)

## Example Submission

```json
{
  "event_id": "btc_100k_march_2026",
  "probability_yes": 0.73,
  "commitment_hash": "sha256(...)",
  "timestamp": 1708900000,
  "miner_hotkey": "5FHneW46..."
}
```
## 🎯 Miner Objective

Maximize long-term calibration.

Miners are incentivized to report truthful, well-calibrated probabilities that persistently outperform market consensus over time.

---

## 🛠 Validator Responsibilities

Validators are responsible for deterministic evaluation and emission routing.

### Core Duties

- Ingest events
- Verify commit–reveal integrity
- Compute log-loss
- Compute relative skill vs market baseline
- Update rolling skill
- Compute exponential weights
- Submit normalized weights to the Bittensor metagraph

### Properties

- Fully deterministic  
- Fully reproducible  
- No discretionary scoring  

---

## 🧮 Example Scoring Logic (Pseudocode)

```python
import math

def log_loss(p, y):
    eps = 1e-9
    p = max(min(p, 1 - eps), eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))

def compute_skill(p_miner, p_market, outcome):
    ll_miner = log_loss(p_miner, outcome)
    ll_market = log_loss(p_market, outcome)
    return ll_market - ll_miner

def compute_weight(rolling_skill, beta=5):
    return math.exp(beta * rolling_skill)
```

## 🏗 Architecture Overview
Event
→ Commit → Reveal → Resolution → Log-Loss → Skill → Rolling Update → Weight → Emission


- **Miners forecast.**
- **Validators evaluate.**
- **Emission routes to measurable skill.**

---

## 🛡 Anti-Gaming Properties

- Commit–reveal prevents real-time copying  
- Relative benchmarking neutralizes market mirroring  
- Rolling evaluation mitigates short-term luck  
- Exponential weighting creates a continuous incentive surface  
- Splitting identity does not increase aggregate influence  

**Manipulation without informational advantage is mathematically unprofitable.**

---

## 📈 Use Cases

- DeFi derivatives  
- Parametric insurance  
- DAO governance  
- Risk analytics  
- Probability API for prediction platforms  

Probity does not compete with prediction markets.  
It measures who is consistently better than them.

---

## 🚀 Roadmap

### Phase 1 — Bootstrap

- Crypto macro event pool  
- Public calibration leaderboard  
- Recruit Metaculus & quantitative forecasters  
- Onboard early Bittensor validators  

### Phase 2 — API & Pilots

- Tiered Probability API (free + paid)  
- 1–2 DeFi pilot integrations  

### Phase 3 — Oracle & Institutional

- EVM-compatible probability oracle  
- Institutional analytics & historical skill datasets  

**Flywheel:**

> More miners → better calibration → stronger integrations → higher emission value.

---

## 🌍 Vision

### The Global Probability Index

A continuously updated, decentralized, skill-weighted probability layer  
for every significant binary event on Earth.

Probity transforms forecasting into a digital commodity.

- Skill becomes measurable.  
- Truth becomes economically dominant.  

> Where markets aggregate capital,  
> Probity aggregates measurable intelligence.
