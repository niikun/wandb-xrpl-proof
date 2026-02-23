# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, including dev dependencies)
pip install -e ".[dev]"

# Unit tests only (no external connections needed)
pytest tests/unit/ -v

# Integration tests (XRPL testnet + W&B — requires .env)
pytest tests/integration/ -v

# Single test
pytest tests/unit/test_merkle.py::TestOddLeafHandling::test_odd_number_of_chunks -v

# All tests
pytest -v
```

Integration tests auto-skip when `XRPL_WALLET_SEED` / `WANDB_API_KEY` are not set. `tests/conftest.py` loads `.env` from the repo root via `python-dotenv`. Copy `.env.example` to `.env` to get started.

## Architecture

The package implements the pipeline defined in `documents/WandB_XRPL_Anchor_Spec_v0_2.md`:

```
Weave op → W&B Run → Canonicalize → SHA-256 / Merkle Root → (IPFS) → XRPL Memo
```

### Module responsibilities

| Module | Role |
|---|---|
| `canonicalize.py` | `canonicalize(obj)` — sorts keys, strips whitespace, filters `_timestamp`/`_runtime` and caller-supplied secrets |
| `hash.py` | `compute_hash(canonical_json)` — SHA-256, lowercase hex |
| `merkle.py` | `split_history()` + `build_merkle_tree()` — 1000-step chunks, binary tree with odd-leaf duplication |
| `xrpl_client.py` | `submit_anchor()` / `fetch_transaction()` / `decode_memo()` — uses `AccountSet` (not `Payment`) because xrpl-py v2+ forbids self-payment; `Tx` response wraps fields under `tx_json` |
| `ipfs.py` | `upload_to_ipfs()` / `fetch_from_ipfs()` — Kubo-compatible HTTP API |
| `anchor.py` | `@xrpl_anchor` decorator + `_anchor_current_run()` + `build_payload()` — orchestrates the full pipeline; failures are caught and logged to `run.summary["xrpl_anchor_error"]`, never raised |
| `verify.py` | `verify_anchor(tx_hash, payload)` — fetches XRPL tx, decodes memo, recomputes hash, returns `VerificationResult` |

### On-chain data format

`AccountSet.Memos[0].MemoData` contains UTF-8 JSON hex-encoded (≤256 bytes):

```json
{
  "schema_version": "a2a-weave-xrpl-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<sha256-hex>",
  "cid": "<ipfs-cid-if-used>"
}
```

The off-chain payload (stored in IPFS or held locally) is the full object including `summary`, `config`, `history_root`, etc., with schema version `"wandb-xrpl-proof-0.2"`.

### Key design constraints

- Canonicalization always excludes `_timestamp` and `_runtime` (defined in `canonicalize._UNSTABLE_FIELDS`). Additional keys can be excluded via `exclude_keys`.
- `summary_allowlist` / `config_allowlist` on `@xrpl_anchor` filter which W&B fields enter the payload — use these to avoid leaking private metrics on-chain.
- XRPL node defaults to testnet (`https://s.altnet.rippletest.net:51234`). Override with `XRPL_NODE_URL`.
- Testnet XRP faucet: https://faucet.altnet.rippletest.net/accounts
