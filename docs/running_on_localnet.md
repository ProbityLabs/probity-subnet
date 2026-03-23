# Running Probity Subnet on Local Subtensor

This guide walks you through setting up and running the Probity subnet on a local subtensor chain for development and testing.

## Prerequisites

- Python 3.10+
- [Bittensor CLI](https://docs.bittensor.com/btcli) (`btcli >= 9.x`)
- [Subtensor](https://github.com/opentensor/subtensor) repository cloned locally
- `substrate-interface` Python package

## 1. Start Local Subtensor Chain

Clone subtensor and start localnet using the pre-built binary (skips Rust compilation):

```bash
cd /path/to/subtensor
BUILD_BINARY=0 bash scripts/localnet.sh
```

If no binary exists yet, build first (takes ~30-60 min):

```bash
BUILD_BINARY=1 bash scripts/localnet.sh
```

This starts 2 validator nodes:
- **Node Alice**: `ws://127.0.0.1:9944`
- **Node Bob**: `ws://127.0.0.1:9945`

You can inspect the chain via [Polkadot.js Apps](https://polkadot.js.org/apps/?rpc=ws%3A%2F%2F127.0.0.1%3A9944).

## 2. Create Wallets

Skip this if you already have wallets.

```bash
# Validator wallet
btcli wallet create --wallet.name validator --wallet.hotkey default

# Miner wallet
btcli wallet create --wallet.name miner --wallet.hotkey default
```

## 3. Fund Wallets

Transfer TAO from a pre-funded dev account (Alice) to your wallets. Since localnet uses fast-blocks, use `--no-mev-protection` where needed.

```bash
# Transfer from any pre-funded wallet, e.g. owner
btcli wallet transfer --wallet.name owner --dest <validator_coldkey_ss58> --amount 2000 --subtensor.network local
btcli wallet transfer --wallet.name owner --dest <miner_coldkey_ss58> --amount 500 --subtensor.network local
```

Alternatively, use Polkadot.js Apps to transfer from Alice directly.

## 4. Create Subnet

```bash
btcli subnet create --wallet.name validator --subtensor.network local --no-mev-protection
```

> **Note**: The `--no-mev-protection` flag is required on localnet because fast-blocks (250ms) cause transactions to expire before they're submitted. This flag is only needed for `subnet create`.

The new subnet will typically be **netuid 2** (netuid 0 is root, netuid 1 is pre-existing).

## 5. Register Neurons

```bash
# Register validator
btcli subnet register --wallet.name validator --wallet.hotkey default --netuid 2 --subtensor.network local

# Register miner
btcli subnet register --wallet.name miner --wallet.hotkey default --netuid 2 --subtensor.network local
```

## 6. Enable SubToken & Stake

Subtensor v3.3.x has a **SubToken** feature that is disabled by default on localnet. You must enable it before staking. This requires a Sudo call from Alice (the chain's sudo account).

Run this Python snippet (requires `substrate-interface` and `bittensor`):

```bash
pip install substrate-interface
```

```python
python3 -c "
from substrateinterface import SubstrateInterface, Keypair
import bittensor as bt

sub = SubstrateInterface(url='ws://127.0.0.1:9944')
alice = Keypair.create_from_uri('//Alice')

# Enable SubToken on netuid 2
inner = sub.compose_call(call_module='AdminUtils', call_function='sudo_set_subtoken_enabled',
    call_params={'netuid': 2, 'subtoken_enabled': True})
outer = sub.compose_call(call_module='Sudo', call_function='sudo_unchecked_weight',
    call_params={'call': inner, 'weight': {'ref_time': 1000000000, 'proof_size': 0}})
ext = sub.create_signed_extrinsic(call=outer, keypair=alice)
receipt = sub.submit_extrinsic(ext, wait_for_inclusion=True)
print(f'SubToken enabled: {receipt.is_success}')

# Stake 500 TAO for validator
wallet = bt.Wallet(name='validator', hotkey='default')
call = sub.compose_call(call_module='SubtensorModule', call_function='add_stake',
    call_params={'hotkey': wallet.hotkey.ss58_address, 'netuid': 2, 'amount_staked': 500000000000})
ext = sub.create_signed_extrinsic(call=call, keypair=wallet.coldkey)
receipt = sub.submit_extrinsic(ext, wait_for_inclusion=True)
print(f'Stake: {\"OK\" if receipt.is_success else receipt.error_message}')
"
```

> **Why not `btcli stake add`?** On localnet with subtensor v3.3.x, `btcli stake add` fails with "Transaction is outdated" due to fast-blocks timing and SubToken being disabled. Using the Python SDK directly bypasses both issues.

## 7. Run Validator and Miner

Open two separate terminals:

**Terminal 1 — Validator:**
```bash
cd /path/to/probity-subnet
source venv/bin/activate
python neurons/validator.py --wallet.name validator --wallet.hotkey default --netuid 2 --subtensor.network local
```

**Terminal 2 — Miner:**
```bash
cd /path/to/probity-subnet
source venv/bin/activate
python neurons/miner.py --wallet.name miner --wallet.hotkey default --netuid 2 --subtensor.network local
```

Add `--logging.debug` for verbose output.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Transaction is outdated` on subnet create | Fast-blocks expire transactions | Add `--no-mev-protection` |
| `SubtokenDisabled` on staking | SubToken not enabled on localnet | Run the Python Sudo script in Step 6 |
| `SubnetNotExists` on staking | Subnet not yet created | Complete Step 4 first |
| Validator/miner shows no output | Metagraph syncing or waiting for blocks | Wait 1-2 minutes, or add `--logging.debug` |
| Chain stuck at block #0 | Binary/chainspec mismatch | Re-run localnet with `BUILD_BINARY=1` |
| `sudo` call succeeds but state unchanged | Regular `sudo` dispatch issue | Use `sudo_unchecked_weight` instead of `sudo` |

## Quick Reference

| Component | Endpoint |
|---|---|
| Local chain (Alice) | `ws://127.0.0.1:9944` |
| Local chain (Bob) | `ws://127.0.0.1:9945` |
| Polkadot.js Apps | `https://polkadot.js.org/apps/?rpc=ws://127.0.0.1:9944` |
| Validator axon | `localhost:8091` (default) |
| Miner axon | `localhost:8091` (default) |
