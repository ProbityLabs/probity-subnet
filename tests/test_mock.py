import pytest
import asyncio
import bittensor as bt

from template.mock import MockDendrite, MockMetagraph, MockSubtensor, MockWallet
from template.protocol import Commit, Reveal


@pytest.mark.parametrize("netuid", [1, 2, 3])
@pytest.mark.parametrize("n", [2, 4, 8, 16])
def test_mock_subtensor(netuid, n):
    subtensor = MockSubtensor(netuid=netuid, n=n)
    neurons = subtensor.neurons(netuid=netuid)
    assert subtensor.subnet_exists(netuid)
    assert subtensor.network == "mock"
    assert len(neurons) == n


@pytest.mark.parametrize("n", [4, 8, 16])
def test_mock_metagraph(n):
    subtensor = MockSubtensor(netuid=1, n=n)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    assert metagraph.n == n
    assert len(metagraph.axons) == n
    for axon in metagraph.axons:
        assert axon.ip == MockMetagraph.default_ip
        assert axon.port == MockMetagraph.default_port


@pytest.mark.parametrize("n", [4, 8])
def test_mock_dendrite_commit(n):
    wallet = MockWallet()
    dendrite = MockDendrite(wallet=wallet)
    subtensor = MockSubtensor(netuid=1, n=n)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    axons = metagraph.axons

    async def run():
        return await dendrite(
            axons,
            synapse=Commit(event_id="test-event-1", market_prob=0.6, commit_deadline=int(__import__("time").time()) - 1),
            timeout=5,
            deserialize=True,
        )

    responses = asyncio.run(run())
    assert len(responses) == n
    for resp in responses:
        assert resp is not None
        assert isinstance(resp, str)
        assert len(resp) == 64  # sha256 hex digest length


@pytest.mark.parametrize("n", [4, 8])
def test_mock_dendrite_reveal(n):
    wallet = MockWallet()
    dendrite = MockDendrite(wallet=wallet)
    subtensor = MockSubtensor(netuid=1, n=n)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    axons = metagraph.axons
    event_id = "test-event-reveal"

    async def run():
        # Must commit first so dendrite stores predictions
        await dendrite(
            axons,
            synapse=Commit(event_id=event_id, market_prob=0.5, commit_deadline=int(__import__("time").time()) - 1),
            timeout=5,
            deserialize=True,
        )
        return await dendrite(
            axons,
            synapse=Reveal(event_id=event_id),
            timeout=5,
            deserialize=True,
        )

    responses = asyncio.run(run())
    assert len(responses) == n
    for resp in responses:
        prob, nonce = resp
        assert prob is not None
        assert 0.0 < prob < 1.0
        assert nonce is not None
