import pytest
import asyncio
import bittensor as bt

from template.mock import MockDendrite, MockMetagraph, MockSubtensor, MockWallet
from template.protocol import Reveal


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
def test_mock_dendrite_reveal(n):
    wallet = MockWallet()
    dendrite = MockDendrite(wallet=wallet)
    subtensor = MockSubtensor(netuid=1, n=n)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    axons = metagraph.axons
    event_id = "test-event-reveal"

    # Seed predictions directly (simulating miners having committed)
    for axon in axons:
        dendrite._predictions[(axon.hotkey, event_id)] = {
            "p": 0.65,
            "nonce": "1234567",
            "commit_deadline": 0,  # already past — reveal allowed
        }

    async def run():
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


@pytest.mark.parametrize("n", [4, 8])
def test_mock_dendrite_reveal_blocked_before_deadline(n):
    """Reveal should return None if commit_deadline has not yet passed."""
    wallet = MockWallet()
    dendrite = MockDendrite(wallet=wallet)
    subtensor = MockSubtensor(netuid=1, n=n)
    metagraph = MockMetagraph(netuid=1, subtensor=subtensor)
    axons = metagraph.axons
    event_id = "test-event-blocked"

    import time
    for axon in axons:
        dendrite._predictions[(axon.hotkey, event_id)] = {
            "p": 0.55,
            "nonce": "9876543",
            "commit_deadline": int(time.time()) + 9999,  # far future
        }

    async def run():
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
        assert prob is None
        assert nonce is None
