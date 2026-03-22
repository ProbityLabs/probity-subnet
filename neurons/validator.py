# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

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


import time

import numpy as np

# Bittensor
import bittensor as bt

# import base validator class which takes care of most of the boilerplate
from template.base.validator import BaseValidatorNeuron

from template.protocol import EventList, CommitSubmission

# Bittensor Validator Template:
from template.validator.forward import forward, forward_event_list, forward_commit_submission
from template.validator.reward import RollingSkillTracker


class Validator(BaseValidatorNeuron):
    """
    Your validator neuron class. You should use this class to define your validator's behavior. In particular, you should replace the forward function with your own logic.

    This class inherits from the BaseValidatorNeuron class, which in turn inherits from BaseNeuron. The BaseNeuron class takes care of routine tasks such as setting up wallet, subtensor, metagraph, logging directory, parsing config, etc. You can override any of the methods in BaseNeuron if you need to customize the behavior.

    This class provides reasonable default behavior for a validator such as keeping a moving average of the scores of the miners and using them to set weights at the end of each epoch. Additionally, the scores are reset for new hotkeys at the end of each epoch.
    """

    def __init__(self, config=None):
        super(Validator, self).__init__(config=config)

        bt.logging.info("load_state()")
        self.load_state()

        # Attach pull-model axon handlers so miners can query events and submit commitments
        self.axon.attach(
            forward_fn=self._handle_event_list,
        ).attach(
            forward_fn=self._handle_commit_submission,
        )

    async def forward(self):
        """
        Validator forward pass. Consists of:
        - Adding new events to the pool
        - Closing commits for past-deadline events
        - Sending Reveal to committed miners
        - Scoring resolved events
        """
        return await forward(self)

    async def _handle_event_list(self, synapse: EventList) -> EventList:
        return await forward_event_list(self, synapse)

    async def _handle_commit_submission(self, synapse: CommitSubmission) -> CommitSubmission:
        return await forward_commit_submission(self, synapse)

    def save_state(self):
        """Save validator state including rolling skill tracker."""
        super().save_state()

        if hasattr(self, "_skill_tracker"):
            data = self._skill_tracker.save()
            np.savez(
                self.config.neuron.full_path + "/state_skill.npz",
                sum_skill=data["sum_skill"],
                count=data["count"],
                N0=data["N0"],
            )
            bt.logging.info("Saved skill tracker state.")

    def load_state(self):
        """Load validator state including rolling skill tracker."""
        try:
            super().load_state()
        except Exception:
            bt.logging.info("No previous validator state found, starting fresh.")

        skill_path = self.config.neuron.full_path + "/state_skill.npz"
        try:
            state = np.load(skill_path)
            self._skill_tracker = RollingSkillTracker.load({
                "sum_skill": state["sum_skill"].tolist(),
                "count":     state["count"].tolist(),
                "N0":        float(state["N0"]),
            })
            bt.logging.info(
                f"Loaded skill tracker: {len(state['sum_skill'])} miners, "
                f"{int(state['count'].sum())} total predictions."
            )
        except Exception:
            bt.logging.info("No skill tracker state found, starting fresh.")


# The main function parses the configuration and runs the validator.
if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info(f"Validator running... {time.time()}")
            time.sleep(5)
