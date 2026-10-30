# OnsummAnchor

Writes the daily Merkle root of Onsumm's hash-chained audit log to a public chain, so anyone can check that an approval or payment record existed, unchanged, at that time. It holds no funds and stores only 32-byte roots.

Design: [docs/crypto-rail/04_LLD.md](../docs/crypto-rail/04_LLD.md) §6g and §9, and [08_API_CONTRACT.md](../docs/crypto-rail/08_API_CONTRACT.md) §7.

## Two ways to anchor

| Mode | Needs | How the root is written | When |
| --- | --- | --- | --- |
| `calldata` (default) | `ANCHOR_SIGNER_KEY` with a little gas | The anchor wallet sends a 0-value transaction to itself. Input: `"ONSM"` (4 bytes) · version `0x01` · root (32) · fromSeq (8, big-endian) · toSeq (8, big-endian) | Hackathon; nothing to deploy |
| `contract` | `ANCHOR_SIGNER_KEY` + `ANCHOR_CONTRACT` | `OnsummAnchor.anchor(root, fromSeq, toSeq)`; emits `Anchored` | Production; roots indexed by id and readable by any contract |

The API picks `contract` mode when `ANCHOR_CONTRACT` is set, otherwise `calldata` (`backend_ocr/app/domain/audit/anchor.py`). The `/verify` page understands both. Without an anchor key the API still builds and stores the Merkle trees (status `BUILT`), so proofs work locally; only the on-chain witness is missing.

## Build, test, deploy (contract mode)

Foundry is not installed in this repo's dev environment; install it from https://book.getfoundry.sh first.

```bash
cd contracts
forge install foundry-rs/forge-std --no-commit
forge build
forge test

# Deploy to Base Sepolia. ANCHORER is the address of ANCHOR_SIGNER_KEY.
ANCHORER=0xYourAnchorWallet forge script script/Deploy.s.sol \
  --rpc-url $RPC_URL --private-key $DEPLOYER_KEY --broadcast
```

Set `ANCHOR_CONTRACT` to the printed address and restart the API.

## Security

- Only `anchorer` can write; only `owner` can change the anchorer.
- Roots are never overwritten: each anchor gets the next id.
- The anchor key holds gas only. It cannot move client funds, which live in each client's own Safe.

`abi/OnsummAnchor.json` is the ABI the API and frontend use; regenerate it with `forge inspect OnsummAnchor abi` if the contract changes.
