"""
Quick script to test Polymarket CLOB and Gamma APIs.
Fetches resolved markets and compares resolution fields side-by-side.

Usage:
    python scripts/test_apis.py
"""

import json
import requests

CLOB_URL = "https://clob.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"

session = requests.Session()
session.headers.update({"Accept": "application/json"})


# ── Step 1: Find resolved markets via Gamma ──────────────────────────────
print("Fetching resolved markets from Gamma API...\n")
resp = session.get(
    f"{GAMMA_URL}/markets",
    params={
        "closed": "true",
        "limit": 10,
        "order": "volume",
        "ascending": "false",
    },
    timeout=15,
)
resp.raise_for_status()
gamma_markets = resp.json()
markets = gamma_markets if isinstance(gamma_markets, list) else gamma_markets.get("markets", [])

if not markets:
    print("No resolved markets found.")
    exit(1)

# ── Step 2: For each market, fetch CLOB and compare ─────────────────────
for i, m in enumerate(markets[:5]):
    condition_id = m.get("conditionId") or m.get("condition_id")
    if not condition_id:
        continue

    question = m.get("question", "???")

    # Parse Gamma fields
    gamma_resolution = m.get("resolution", "N/A")
    gamma_uma_status = m.get("umaResolutionStatus", "N/A")
    gamma_outcome_prices = m.get("outcomePrices", "[]")
    if isinstance(gamma_outcome_prices, str):
        try:
            gamma_outcome_prices = json.loads(gamma_outcome_prices)
        except Exception:
            gamma_outcome_prices = []
    gamma_outcomes = m.get("outcomes", "[]")
    if isinstance(gamma_outcomes, str):
        try:
            gamma_outcomes = json.loads(gamma_outcomes)
        except Exception:
            gamma_outcomes = []

    # Determine Gamma winner
    gamma_winner = "???"
    for price, label in zip(gamma_outcome_prices, gamma_outcomes):
        try:
            if float(price) == 1.0:
                gamma_winner = label
        except ValueError:
            pass

    # Fetch CLOB
    clob_winner = "???"
    clob_winner_outcome = "???"
    try:
        clob_resp = session.get(f"{CLOB_URL}/markets/{condition_id}", timeout=15)
        clob_resp.raise_for_status()
        clob_data = clob_resp.json()
        clob_tokens = clob_data.get("tokens", [])
        for t in clob_tokens:
            if t.get("winner") is True:
                clob_winner = t.get("outcome", "???")
                clob_winner_outcome = t.get("outcome", "???")
    except Exception as e:
        clob_winner = f"ERROR: {e}"
        clob_data = {}

    # Compare
    match = "MATCH" if gamma_winner == clob_winner else "MISMATCH"

    print(f"{'='*70}")
    print(f"  [{i+1}] {question[:65]}")
    print(f"      condition_id: {condition_id[:20]}...")
    print(f"{'─'*70}")
    print(f"  GAMMA:")
    print(f"    resolution field : {gamma_resolution}")
    print(f"    umaResStatus     : {gamma_uma_status}")
    print(f"    outcomes          : {gamma_outcomes}")
    print(f"    outcomePrices     : {gamma_outcome_prices}")
    print(f"    → winner          : {gamma_winner}")
    print(f"  CLOB:")
    print(f"    tokens winner     : {clob_winner}")
    print(f"{'─'*70}")
    print(f"  ▶ {match}")
    print()

print("Done.")
