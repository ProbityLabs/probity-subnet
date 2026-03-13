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
import numpy as np
from typing import List, Optional
import bittensor as bt
import math


def log_loss(p: float, y: int) -> float:
    eps = 1e-9
    p = max(min(p, 1 - eps), eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))

def compute_skill(p_miner: float, p_market: float, outcome: int) -> float:
    ll_miner = log_loss(p_miner, outcome)
    ll_market = log_loss(p_market, outcome)
    return ll_market - ll_miner

def reward(p_miner: Optional[float], p_market: float, outcome: int) -> float:
    """
    Reward the miner response using relative skill vs market baseline.
    """
    if p_miner is None:
        return 0.0 # No answer or invalid
    
    skill = compute_skill(p_miner, p_market, outcome)
    return skill

def get_rewards(
    self,
    p_market: float,
    outcome: int,
    responses: List[Optional[float]],
) -> np.ndarray:
    """
    Returns an array of rewards for the given query and responses.
    """
    # Calculate rewards for all responses.
    skills = [reward(resp, p_market, outcome) for resp in responses]
    
    # We can smooth it using some beta coefficient as per the whitepaper
    beta = 5
    weights = []
    for skill in skills:
        if skill <= 0:
            weights.append(0.0) # We only reward positive skill vs market. Or we could track rolling skill.
        else:
            weights.append(math.exp(beta * skill))
            
    # return the scores
    return np.array(weights, dtype=np.float32)
