# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 <your name>

import typing
import pydantic
import bittensor as bt


class EventInfo(pydantic.BaseModel):
    """Lightweight event descriptor served to miners via EventList."""
    event_id: str
    question: str
    market_prob: float
    commit_deadline: int   # Unix ts — submit commitments before this
    reveal_deadline: int   # Unix ts — reveal window opens at this time


class EventList(bt.Synapse):
    """
    Pull protocol — Step 1.
    Miner queries validator's axon to get the list of active events.
    Miner sends empty synapse; validator fills in the events list.
    """
    events: typing.List[EventInfo] = pydantic.Field(default_factory=list)

    def deserialize(self) -> typing.List[EventInfo]:
        return self.events


class CommitSubmission(bt.Synapse):
    """
    Pull protocol — Step 2.
    Miner submits a commitment for a chosen event to the validator's axon.
    Miner fills event_id, commitment_hash, timestamp.
    Validator responds with accepted=True/False.
    """
    # Input from miner
    event_id: str = pydantic.Field(
        ..., title="Event ID", frozen=True,
    )
    commitment_hash: str = pydantic.Field(
        ..., title="Commitment Hash", frozen=True,
        description="sha256(p + nonce + event_id + miner_hotkey)",
    )
    timestamp: int = pydantic.Field(
        ..., title="Timestamp", frozen=True,
        description="Unix timestamp when the miner created the commitment.",
    )

    # Output from validator
    accepted: bool = pydantic.Field(default=False)

    def deserialize(self) -> bool:
        return self.accepted


class Reveal(bt.Synapse):
    """
    Reveal protocol — Step 3.
    Validator pushes this to miners who committed, requesting the actual probability.
    Stays as push (validator → miner) since validator controls reveal timing.
    """
    event_id: str = pydantic.Field(
        ..., title="Event ID", frozen=True,
    )

    probability: typing.Optional[float] = pydantic.Field(None)
    nonce: typing.Optional[str] = pydantic.Field(None)

    def deserialize(self) -> typing.Tuple[typing.Optional[float], typing.Optional[str]]:
        return self.probability, self.nonce
