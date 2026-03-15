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

import typing
import pydantic
import bittensor as bt


class Commit(bt.Synapse):
    """
    Commit protocol for Probity subnet.
    Validator sends the event details, and Miner returns a commitment_hash.
    """

    # Input: Required request input, filled by sending validator caller.
    event_id: str = pydantic.Field(
        ...,
        title="Event ID",
        description="The unique identifier of the event.",
        frozen=True,
    )
    market_prob: float = pydantic.Field(
        ...,
        title="Market Probability",
        description="The baseline market probability at the time of commit.",
        frozen=True,
    )
    commit_deadline: int = pydantic.Field(
        ...,
        title="Commit Deadline",
        description="The Unix timestamp deadline for submitting this commit.",
        frozen=True,
    )
    question: str = pydantic.Field(
        "",
        title="Question",
        description="The natural-language question miners are asked to forecast.",
        frozen=True,
    )

    # Output: Optional request output, filled by receiving miner axon.
    commitment_hash: typing.Optional[str] = pydantic.Field(
        None,
        title="Commitment Hash",
        description="The SHA256 hash of the miner's prediction and a secret nonce.",
    )

    def deserialize(self) -> typing.Optional[str]:
        """
        Deserialize the commitment hash.
        """
        return self.commitment_hash


class Reveal(bt.Synapse):
    """
    Reveal protocol for Probity subnet.
    Validator sends the event id, and Miner returns the original probability and nonce.
    """

    # Input:
    event_id: str = pydantic.Field(
        ...,
        title="Event ID",
        description="The unique identifier of the event to reveal.",
        frozen=True,
    )

    # Output:
    probability: typing.Optional[float] = pydantic.Field(
        None,
        title="Predicted Probability",
        description="The miner's original predicted probability (between 0 and 1).",
    )
    nonce: typing.Optional[str] = pydantic.Field(
        None,
        title="Nonce",
        description="The secret string used in the commitment hash.",
    )

    def deserialize(self) -> typing.Tuple[typing.Optional[float], typing.Optional[str]]:
        """
        Deserialize the original prediction and the nonce.
        """
        return self.probability, self.nonce
