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

import hashlib
import random
import time
import typing
import bittensor as bt

# Minimum seconds between receiving a Commit and allowing a Reveal,
# regardless of what commit_deadline the validator sends.
# Prevents a malicious validator from sending a backdated deadline.
MIN_COMMIT_WINDOW = 20

# Bittensor Miner Template:
import template

# import base miner class which takes care of most of the boilerplate
from template.base.miner import BaseMinerNeuron


class Miner(BaseMinerNeuron):
    """
    Your miner neuron class. You should use this class to define your miner's behavior. In particular, you should replace the forward function with your own logic. You may also want to override the blacklist and priority functions according to your needs.

    This class inherits from the BaseMinerNeuron class, which in turn inherits from BaseNeuron. The BaseNeuron class takes care of routine tasks such as setting up wallet, subtensor, metagraph, logging directory, parsing config, etc. You can override any of the methods in BaseNeuron if you need to customize the behavior.

    This class provides reasonable default behavior for a miner such as blacklisting unrecognized hotkeys, prioritizing requests based on stake, and forwarding requests to the forward function. If you need to define custom
    """

    def __init__(self, config=None):
        super(Miner, self).__init__(config=config)
        self.predictions = {} # Store our predictions temporarily.

        # Re-attach multiple endpoints for Probity
        # First we clear the default ones
        self.axon.non_blocking_fns = []
        self.axon.forward_fns = {}
        self.axon.blacklist_fns = {}
        self.axon.priority_fns = {}
        self.axon.verify_fns = {}

        self.axon.attach(
            forward_fn=self.forward_commit,
            blacklist_fn=self.blacklist_commit,
            priority_fn=self.priority_commit,
        ).attach(
            forward_fn=self.forward_reveal,
            blacklist_fn=self.blacklist_reveal,
            priority_fn=self.priority_reveal,
        )

    async def forward(self, synapse: bt.Synapse) -> bt.Synapse:
        """Required by ABC. Routing is handled by forward_commit and forward_reveal."""
        return synapse

    async def forward_commit(
        self, synapse: template.protocol.Commit
    ) -> template.protocol.Commit:
        """
        Receives an event commit request, computes the probability,
        hashes it with a nonce, and returns the hash.
        """
        # Dummy predictive logic: picking random probability based on market.
        # In a real miner, you'd use models.
        prob = max(0.01, min(0.99, synapse.market_prob + random.uniform(-0.1, 0.1)))
        nonce = str(random.randint(1000000, 9999999))

        # Save securely so we can reveal it later.
        # received_at is recorded locally — used to enforce MIN_COMMIT_WINDOW
        # even if the validator sends a backdated commit_deadline.
        self.predictions[synapse.event_id] = {
            "p":               prob,
            "nonce":           nonce,
            "commit_deadline": synapse.commit_deadline,
            "received_at":     int(time.time()),
        }
        
        # Create hash
        data_to_hash = f"{prob}_{nonce}_{synapse.event_id}_{self.wallet.hotkey.ss58_address}"
        commitment_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
        
        synapse.commitment_hash = commitment_hash
        bt.logging.info(f"Committed prediction for {synapse.event_id}: hash={commitment_hash}")
        return synapse

    async def forward_reveal(
        self, synapse: template.protocol.Reveal
    ) -> template.protocol.Reveal:
        """
        Reveals the actual probability and nonce for a requested event.
        """
        if synapse.event_id not in self.predictions:
            synapse.probability = None
            synapse.nonce = None
            bt.logging.warning(f"No prediction found to reveal for {synapse.event_id}")
        else:
            pred = self.predictions[synapse.event_id]
            # Enforce: reveal only after both the validator's commit_deadline AND
            # our own minimum window have passed. This protects against a malicious
            # validator sending a backdated deadline to force an early reveal.
            earliest_reveal = pred["received_at"] + MIN_COMMIT_WINDOW
            actual_deadline = max(pred["commit_deadline"], earliest_reveal)

            if int(time.time()) < actual_deadline:
                synapse.probability = None
                synapse.nonce = None
                bt.logging.warning(
                    f"Reveal rejected for {synapse.event_id}: "
                    f"deadline not yet passed (earliest={actual_deadline})"
                )
            else:
                synapse.probability = pred["p"]
                synapse.nonce = pred["nonce"]
                bt.logging.info(
                    f"Revealed prediction for {synapse.event_id}: "
                    f"p={synapse.probability}, nonce={synapse.nonce}"
                )
        return synapse

    async def blacklist_commit(self, synapse: template.protocol.Commit) -> typing.Tuple[bool, str]:
        return await self.blacklist_base(synapse)
        
    async def priority_commit(self, synapse: template.protocol.Commit) -> float:
        return await self.priority_base(synapse)

    async def blacklist_reveal(self, synapse: template.protocol.Reveal) -> typing.Tuple[bool, str]:
        return await self.blacklist_base(synapse)
        
    async def priority_reveal(self, synapse: template.protocol.Reveal) -> float:
        return await self.priority_base(synapse)

    async def blacklist_base(
        self, synapse: bt.Synapse
    ) -> typing.Tuple[bool, str]:  

        """
        Determines whether an incoming request should be blacklisted and thus ignored. Your implementation should
        define the logic for blacklisting requests based on your needs and desired security parameters.

        Blacklist runs before the synapse data has been deserialized (i.e. before synapse.data is available).
        The synapse is instead contracted via the headers of the request. It is important to blacklist
        requests before they are deserialized to avoid wasting resources on requests that will be ignored.

        Args:
            synapse (template.protocol.Dummy): A synapse object constructed from the headers of the incoming request.

        Returns:
            Tuple[bool, str]: A tuple containing a boolean indicating whether the synapse's hotkey is blacklisted,
                            and a string providing the reason for the decision.

        This function is a security measure to prevent resource wastage on undesired requests. It should be enhanced
        to include checks against the metagraph for entity registration, validator status, and sufficient stake
        before deserialization of synapse data to minimize processing overhead.

        Example blacklist logic:
        - Reject if the hotkey is not a registered entity within the metagraph.
        - Consider blacklisting entities that are not validators or have insufficient stake.

        In practice it would be wise to blacklist requests from entities that are not validators, or do not have
        enough stake. This can be checked via metagraph.S and metagraph.validator_permit. You can always attain
        the uid of the sender via a metagraph.hotkeys.index( synapse.dendrite.hotkey ) call.

        Otherwise, allow the request to be processed further.
        """

        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            bt.logging.warning(
                "Received a request without a dendrite or hotkey."
            )
            return True, "Missing dendrite or hotkey"

        # TODO(developer): Define how miners should blacklist requests.
        uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        if (
            not self.config.blacklist.allow_non_registered
            and synapse.dendrite.hotkey not in self.metagraph.hotkeys
        ):
            # Ignore requests from un-registered entities.
            bt.logging.trace(
                f"Blacklisting un-registered hotkey {synapse.dendrite.hotkey}"
            )
            return True, "Unrecognized hotkey"

        if self.config.blacklist.force_validator_permit:
            # If the config is set to force validator permit, then we should only allow requests from validators.
            if not self.metagraph.validator_permit[uid]:
                bt.logging.warning(
                    f"Blacklisting a request from non-validator hotkey {synapse.dendrite.hotkey}"
                )
                return True, "Non-validator hotkey"

        bt.logging.trace(
            f"Not Blacklisting recognized hotkey {synapse.dendrite.hotkey}"
        )
        return False, "Hotkey recognized!"

    async def priority_base(self, synapse: bt.Synapse) -> float:
        """
        The priority function determines the order in which requests are handled. More valuable or higher-priority
        requests are processed before others. You should design your own priority mechanism with care.

        This implementation assigns priority to incoming requests based on the calling entity's stake in the metagraph.

        Args:
            synapse (template.protocol.Dummy): The synapse object that contains metadata about the incoming request.

        Returns:
            float: A priority score derived from the stake of the calling entity.

        Miners may receive messages from multiple entities at once. This function determines which request should be
        processed first. Higher values indicate that the request should be processed first. Lower values indicate
        that the request should be processed later.

        Example priority logic:
        - A higher stake results in a higher priority value.
        """
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            bt.logging.warning(
                "Received a request without a dendrite or hotkey."
            )
            return 0.0

        # TODO(developer): Define how miners should prioritize requests.
        caller_uid = self.metagraph.hotkeys.index(
            synapse.dendrite.hotkey
        )  # Get the caller index.
        priority = float(
            self.metagraph.S[caller_uid]
        )  # Return the stake as the priority.
        bt.logging.trace(
            f"Prioritizing {synapse.dendrite.hotkey} with value: {priority}"
        )
        return priority


# This is the main function, which runs the miner.
if __name__ == "__main__":
    with Miner() as miner:
        while True:
            bt.logging.info(f"Miner running... {time.time()}")
            time.sleep(5)
