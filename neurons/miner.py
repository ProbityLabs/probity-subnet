# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Probity
# Copyright © 2026 Probity

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import asyncio
import hashlib
import random
import time
import typing
import bittensor as bt

# Minimum seconds between receiving a commit request and allowing a Reveal,
# regardless of what commit_deadline the validator sends.
# Prevents a malicious validator from sending a backdated deadline.
MIN_COMMIT_WINDOW = 23 * 3600  # 23 hours — safety guard against backdated deadlines

# Bittensor Miner Template:
import template

# import base miner class which takes care of most of the boilerplate
from template.base.miner import BaseMinerNeuron


class Miner(BaseMinerNeuron):
    """
    Probity subnet miner.

    Implements the pull-based commit-reveal protocol:
      - Periodically queries validators for active events (pull_and_submit).
      - Submits a commitment hash for each event the miner wants to forecast.
      - Responds to validator Reveal requests with the original probability and nonce.

    Miners should implement their forecasting logic inside pull_and_submit
    in the section marked with TODO.
    """

    def __init__(self, config=None):
        super(Miner, self).__init__(config=config)
        # Store per-event predictions keyed by event_id
        self.predictions = {}
        # Dendrite for outbound calls to validators (pull model)
        self.dendrite = bt.Dendrite(wallet=self.wallet)

        # Attach only the Reveal handler — in the pull model, the miner
        # actively queries validators for events and submits commitments
        # (see pull_and_submit). The only inbound request is the validator
        # asking the miner to reveal.
        self.axon.non_blocking_fns = []
        self.axon.forward_fns = {}
        self.axon.blacklist_fns = {}
        self.axon.priority_fns = {}
        self.axon.verify_fns = {}

        self.axon.attach(
            forward_fn=self.forward_reveal,
            blacklist_fn=self.blacklist_reveal,
            priority_fn=self.priority_reveal,
        )

    async def forward(self, synapse: bt.Synapse) -> bt.Synapse:
        """Required by ABC. Routing is handled by forward_reveal."""
        return synapse

    async def pull_and_submit(self) -> None:
        """
        Pull active events from validators and autonomously submit commitments.

        Steps:
          1. Find validator axons in the metagraph.
          2. Query each validator for the list of active events (EventList).
          3. For each new event, compute a probability forecast and submit a
             commitment hash (CommitSubmission).

        Miners should implement their forecasting logic in the section marked
        with TODO below. The rest of the protocol handling is taken care of.
        """
        validator_axons = [
            self.metagraph.axons[uid]
            for uid in range(int(self.metagraph.n))
            if self.metagraph.validator_permit[uid] and uid != self.uid
        ]
        bt.logging.info(f"[Miner] Found {len(validator_axons)} validator(s) in metagraph.")
        if not validator_axons:
            bt.logging.warning("No validators found in metagraph.")
            return

        for val_axon in validator_axons:
            bt.logging.info(f"[Miner] Querying validator {val_axon.hotkey[:12]}... for events.")
            responses = await self.dendrite(
                axons=[val_axon],
                synapse=template.protocol.EventList(),
                deserialize=True,
                timeout=12,
            )
            events = responses[0] if responses else []
            if not events:
                bt.logging.info(f"[Miner] No active events from {val_axon.hotkey[:12]}...")
                continue

            bt.logging.info(f"[Miner] Received {len(events)} event(s) from {val_axon.hotkey[:12]}...")
            for event in events:
                if event.event_id in self.predictions:
                    bt.logging.debug(
                        f"[Miner] Already committed to {event.event_id[:12]}... — skipping."
                    )
                    continue

                bt.logging.info(
                    f"[Miner] Processing event {event.event_id[:12]}...\n"
                    f"        question  : {event.question}\n"
                    f"        market_prob: {event.market_prob:.4f}\n"
                    f"        deadline  : {event.commit_deadline}"
                )

                # ── TODO: implement your forecasting logic here ───────────────
                #
                # You receive:
                #   event.event_id    — unique market identifier
                #   event.question    — natural-language question to forecast
                #   event.market_prob — current Polymarket probability (baseline)
                #   event.commit_deadline — Unix ts; must submit before this
                #
                # You must produce:
                #   prob (float) — your probability estimate, strictly in (0, 1)
                #
                # Example approaches:
                #   - LLM-based reasoning on event.question
                #   - Bayesian model trained on historical data
                #   - Statistical ensemble
                #   - Market-derived signals with adjustments
                #   - Agenting approach that uses tools to gather more info before predicting
                #
                # Replace the random placeholder with your model's output:
                prob: typing.Optional[float] = round(random.uniform(0.05, 0.95), 4)
                # prob = your_model.predict(event.question, event.market_prob)
                # ── END TODO ─────────────────────────────────────────────────

                bt.logging.info(
                    f"[Miner] Forecast for {event.event_id[:12]}...: prob={prob:.4f} "
                    f"(market baseline={event.market_prob:.4f})"
                )

                if prob is None:
                    bt.logging.debug(
                        f"[Miner] No forecast for {event.event_id[:12]}... — skipping."
                    )
                    continue

                nonce = str(random.randint(1_000_000, 9_999_999))
                data = f"{prob}{nonce}{event.event_id}{self.wallet.hotkey.ss58_address}"
                commitment_hash = hashlib.sha256(data.encode()).hexdigest()

                submit_resp = await self.dendrite(
                    axons=[val_axon],
                    synapse=template.protocol.CommitSubmission(
                        event_id=event.event_id,
                        commitment_hash=commitment_hash,
                        timestamp=int(time.time()),
                    ),
                    deserialize=True,
                    timeout=12,
                )
                accepted = submit_resp[0] if submit_resp else False

                if accepted:
                    self.predictions[event.event_id] = {
                        "p": prob,
                        "nonce": nonce,
                        "commit_deadline": event.commit_deadline,
                        "received_at": int(time.time()),
                    }
                    bt.logging.info(
                        f"[Miner] Committed to {event.event_id[:12]}... prob={prob:.4f}"
                    )
                else:
                    bt.logging.warning(
                        f"[Miner] Commitment rejected for {event.event_id[:12]}..."
                    )

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

    async def blacklist_reveal(self, synapse: template.protocol.Reveal) -> typing.Tuple[bool, str]:
        return await self.blacklist_base(synapse)

    async def priority_reveal(self, synapse: template.protocol.Reveal) -> float:
        return await self.priority_base(synapse)

    async def blacklist_base(
        self, synapse: bt.Synapse
    ) -> typing.Tuple[bool, str]:
        """
        Returns (True, reason) to reject a request before it is processed,
        or (False, reason) to allow it through.

        Applied to all inbound requests (currently only Reveal).
        Rejects if: missing hotkey, unregistered hotkey, or non-validator
        when force_validator_permit is enabled.

        Args:
            synapse (template.protocol.Reveal): The inbound synapse.

        Returns:
            Tuple[bool, str]: (blacklisted, reason)
        """

        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            bt.logging.warning(
                "Received a request without a dendrite or hotkey."
            )
            return True, "Missing dendrite or hotkey"

        hotkey = synapse.dendrite.hotkey
        if hotkey not in self.metagraph.hotkeys:
            if not self.config.blacklist.allow_non_registered:
                bt.logging.trace(f"Blacklisting un-registered hotkey {hotkey}")
                return True, "Unrecognized hotkey"
            return False, "Hotkey recognized!"

        uid = self.metagraph.hotkeys.index(hotkey)

        if self.config.blacklist.force_validator_permit:
            if not self.metagraph.validator_permit[uid]:
                bt.logging.warning(
                    f"Blacklisting a request from non-validator hotkey {hotkey}"
                )
                return True, "Non-validator hotkey"

        bt.logging.trace(
            f"Not Blacklisting recognized hotkey {hotkey}"
        )
        return False, "Hotkey recognized!"

    async def priority_base(self, synapse: bt.Synapse) -> float:
        """
        Returns a priority score for an inbound request. Higher = processed first.

        Uses the caller's stake in the metagraph as the priority score, so
        higher-stake validators are served before lower-stake ones when
        multiple Reveal requests arrive simultaneously.

        Args:
            synapse (template.protocol.Reveal): The inbound synapse.

        Returns:
            float: Priority score (caller's stake).
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


async def _run():
    with Miner() as miner:
        while True:
            bt.logging.info(f"Miner running... {time.time()}")
            await miner.pull_and_submit()
            await asyncio.sleep(60)  # pull every 60 seconds


if __name__ == "__main__":
    asyncio.run(_run())
