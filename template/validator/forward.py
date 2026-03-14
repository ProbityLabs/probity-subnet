# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import time
import uuid
import random
import hashlib
import bittensor as bt

from template.protocol import Commit, Reveal
from template.validator.reward import get_rewards
from template.utils.uids import get_random_uids


async def forward(self):
    """
    The forward function is called by the validator every time step.
    In Probity, it consists of a Commit phase, a Reveal phase, and Scoring.
    """
    miner_uids = get_random_uids(self, k=self.config.neuron.sample_size, exclude=[self.uid])
    axons = [self.metagraph.axons[uid] for uid in miner_uids]

    if len(miner_uids) == 0:
        bt.logging.warning("No miners available to query.")
        return

    # --- 1. Event Setup ---
    # THIS IS JUST A SIMULATION. In a real implementation, the event details would come from an external source or oracle.
    event_id = str(uuid.uuid4())
    market_prob = random.uniform(0.1, 0.9)
    # Simulate a future outcome (0 or 1)
    outcome = 1 if random.random() < market_prob else 0

    bt.logging.info(f"Querying {len(miner_uids)} miners: {miner_uids.tolist()}")
    bt.logging.info(f"Started Event {event_id} | market_prob={market_prob:.4f} | outcome={outcome}")

    # --- 2. Commit Phase ---
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

    # Simulate waiting for the event to happen and the reveal target to arrive
    time.sleep(2) 

    # --- 3. Reveal Phase ---
    reveal_synapse = Reveal(
        event_id=event_id
    )
    
    reveal_responses = await self.dendrite(
        axons=axons,
        synapse=reveal_synapse,
        deserialize=True,
    )
    
    bt.logging.info(f"Received Reveal responses: {reveal_responses}")

    # --- 4. Validation and Scoring ---
    valid_probabilities = []
    
    for commit_hash, reveal_tuple, axon in zip(commit_responses, reveal_responses, axons):
        if not commit_hash or not reveal_tuple:
            valid_probabilities.append(None)
            continue
            
        prob, nonce = reveal_tuple
        if prob is None or nonce is None:
            valid_probabilities.append(None)
            continue
            
        # Verify hash
        data_to_hash = f"{prob}_{nonce}_{event_id}_{axon.hotkey}"
        expected_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()

        if expected_hash == commit_hash:
            bt.logging.info(f"  ✓ {axon.hotkey[:8]}... hash verified | prob={prob:.4f}")
            valid_probabilities.append(prob)
        else:
            bt.logging.warning(f"  ✗ {axon.hotkey[:8]}... hash MISMATCH!")
            valid_probabilities.append(None)

    # Record rewards
    rewards = get_rewards(self, p_market=market_prob, outcome=outcome, responses=valid_probabilities)
    bt.logging.info(f"Valid probs: {[round(p, 4) if p else None for p in valid_probabilities]}")
    bt.logging.info(f"Rewards:     {rewards}")
    
    # Update scores
    self.update_scores(rewards, miner_uids)
    time.sleep(5)
