import time
import hashlib
import asyncio
import random

import numpy as np
import bittensor as bt
from bittensor_wallet import Keypair
from typing import List


class MockWallet:
    """
    Minimal mock wallet for testing. hotkey/coldkey are actual Keypair objects
    so they support both .ss58_address and .sign() (required by bt.Dendrite).
    """
    def __init__(self):
        kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        self.hotkey = kp
        self.coldkey = kp

    def __str__(self):
        return f"MockWallet(hotkey={self.hotkey.ss58_address})"


class MockSubtensor(bt.MockSubtensor):
    def __init__(self, netuid, n=16, wallet=None, network="mock"):
        # bt.MockSubtensor stores ALL instance state in __GLOBAL_MOCK_STATE__ (a
        # module-level dict shared across every instance). Call reset() to wipe it
        # before constructing this instance so each test gets a clean slate.
        bt.MockSubtensor.reset()
        super().__init__(network=network)

        # subnet_exists() uses MagicMock substrate so always returns truthy.
        # Check chain_state directly instead.
        if netuid not in self.chain_state["SubtensorModule"]["NetworksAdded"]:
            self.create_subnet(netuid)

        # Register ourself (the validator) as a neuron at uid=0
        if wallet is not None:
            self.force_register_neuron(
                netuid=netuid,
                hotkey_ss58=wallet.hotkey.ss58_address,
                coldkey_ss58=wallet.coldkey.ss58_address,
                balance=100000,
                stake=100000,
            )

        # Register n mock neurons who will be miners
        for i in range(1, n + 1):
            self.force_register_neuron(
                netuid=netuid,
                hotkey_ss58=f"miner-hotkey-{i}",
                coldkey_ss58="mock-coldkey",
                balance=100000,
                stake=100000,
            )


class MockMetagraph:
    """
    Lightweight mock metagraph. Avoids bt.Metagraph.sync() which is broken
    with MockSubtensor in bittensor 10.x.
    """
    default_ip = "127.0.0.1"
    default_port = 8091

    def __init__(self, netuid=1, network="mock", subtensor=None):
        self.netuid = netuid
        self.network = network

        if subtensor is not None:
            neurons = subtensor.neurons(netuid=netuid)
            hotkeys = [n.hotkey for n in neurons]
        else:
            hotkeys = [f"miner-hotkey-{i}" for i in range(1, 17)]

        n = len(hotkeys)
        self.n = np.int64(n)   # .item() is called by get_random_uids
        self.hotkeys = hotkeys
        self.uids = np.arange(n, dtype=np.int64)
        self.S = np.ones(n, dtype=np.float32) * 100000
        self.validator_permit = np.zeros(n, dtype=bool)
        self.last_update = np.zeros(n, dtype=np.int64)
        self.axons = [
            bt.AxonInfo(
                version=1,
                ip=self.default_ip,
                port=self.default_port,
                ip_type=4,
                hotkey=hk,
                coldkey="mock-coldkey",
            )
            for hk in hotkeys
        ]
        bt.logging.info(f"MockMetagraph: {self}")

    def sync(self, subtensor=None):
        pass  # no-op for mock

    def __repr__(self):
        return f"MockMetagraph(n={self.n}, netuid={self.netuid})"


class MockDendrite(bt.Dendrite):
    """
    Mock dendrite for testing the Probity commit-reveal flow.
    Simulates a miner that generates a random probability, commits a hash,
    and reveals the probability + nonce on request.
    """

    def __init__(self, wallet):
        # bt.Dendrite expects a Wallet or Keypair. Pass the hotkey Keypair directly
        # so self.keypair gets ss58_address AND sign() without needing a full Wallet.
        super().__init__(wallet.hotkey)
        # Store per-event predictions keyed by (axon_hotkey, event_id)
        self._predictions: dict = {}

    async def forward(
        self,
        axons: List[bt.AxonInfo],
        synapse: bt.Synapse = bt.Synapse(),
        timeout: float = 12,
        deserialize: bool = True,
        run_async: bool = True,
        streaming: bool = False,
    ):
        if streaming:
            raise NotImplementedError("Streaming not implemented yet.")

        from template.protocol import Commit, Reveal

        async def single_axon_response(axon):
            start_time = time.time()
            s = synapse.model_copy()
            s = self.preprocess_synapse_for_request(axon, s, timeout)

            process_time = random.uniform(0.01, 0.1)
            if process_time < timeout:
                s.dendrite.process_time = str(time.time() - start_time)
                s.dendrite.status_code = 200
                s.dendrite.status_message = "OK"

                # Handle Commit phase
                if isinstance(s, Commit):
                    prob = max(0.01, min(0.99, s.market_prob + random.uniform(-0.1, 0.1)))
                    nonce = str(random.randint(1000000, 9999999))
                    self._predictions[(axon.hotkey, s.event_id)] = {
                        "p": prob,
                        "nonce": nonce,
                    }
                    data_to_hash = f"{prob}_{nonce}_{s.event_id}_{axon.hotkey}"
                    s.commitment_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()

                # Handle Reveal phase
                elif isinstance(s, Reveal):
                    key = (axon.hotkey, s.event_id)
                    if key in self._predictions:
                        s.probability = self._predictions[key]["p"]
                        s.nonce = self._predictions[key]["nonce"]
                    else:
                        s.probability = None
                        s.nonce = None
            else:
                s.dendrite.status_code = 408
                s.dendrite.status_message = "Timeout"

            if deserialize:
                return s.deserialize()
            return s

        return await asyncio.gather(
            *(single_axon_response(axon) for axon in axons)
        )

    def __str__(self):
        return "MockDendrite({})".format(self.keypair.ss58_address)
