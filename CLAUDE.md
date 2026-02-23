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
pytest tests/unit/test_incremental.py::TestExtractTraceFields::test_extracts_call_id -v

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
| `canonicalize.py` | `canonicalize(obj, exclude_keys)` — sorts keys, strips whitespace, filters `_timestamp`/`_runtime` always; `DEFAULT_PII_KEYS` frozenset for stripping PII fields before hashing |
| `hash.py` | `compute_hash(canonical_json)` — SHA-256, lowercase hex; `compute_chain_step(prev_hash, chunk_hash)` — one step of the incremental hash chain: `SHA-256((prev or "") + chunk_hash)` |
| `merkle.py` | `split_history()` + `build_merkle_tree()` — 1000-step chunks, binary tree with odd-leaf duplication |
| `xrpl_client.py` | `submit_anchor()` / `fetch_transaction()` / `decode_memo()` — uses `AccountSet` (not `Payment`) because xrpl-py v2+ forbids self-payment; `Tx` response wraps fields under `tx_json` |
| `ipfs.py` | `upload_to_ipfs()` / `fetch_from_ipfs()` — upload uses Kubo HTTP API (`IPFS_API_URL`); fetch uses HTTP gateway (`IPFS_GATEWAY_URL`); configured separately because they have different availability requirements |
| `anchor.py` | `@xrpl_anchor` decorator + `build_payload()` + `anchor_run_end()` — orchestrates the single-anchor pipeline; `mode="per_run"` registers via `atexit`; when wrapping a `@weave.op()`, calls `func.call()` to capture `weave_call_id` and `weave_ui_url`; failures logged to `run.summary["xrpl_anchor_error"]`, never raised |
| `incremental.py` | `IncrementalAnchor` — mid-run checkpointing via hash chain; `record(data, weave_call=None)` / `log(data, weave_call=None)` push rows into a local buffer; `_extract_trace_fields(call)` pulls Weave input/output hashes, op name, and tool call summary from the Call object; `close()` flushes any partial chunk |
| `verify.py` | `verify_anchor(tx_hash, payload)` — fetches XRPL tx, decodes memo, recomputes hash, returns `VerificationResult`; `verify_chain(final_tx_hash, chunk_hashes?)` — walks `prev` links back to seq=0, verifies seq continuity, prev-link integrity, schema_version, and optionally hash chain values |

### On-chain data format

`AccountSet.Memos[0].MemoData` contains UTF-8 JSON hex-encoded (≤256 bytes):

```json
// @xrpl_anchor / anchor_run_end (single anchor)
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<sha256-hex>",
  "cid": "<ipfs-cid-if-used>"
}

// IncrementalAnchor seq=0 (genesis — binds run to chain)
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<chain-hash-hex>",
  "seq": 0
}

// IncrementalAnchor seq≥1 (links to previous checkpoint)
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "commit_hash": "<chain-hash-hex>",
  "prev": "<prev-tx-hash-hex>",
  "seq": 5
}
```

The off-chain payload (stored in IPFS or held locally) is the full object including `summary`, `config`, `history_root`, `weave_call_id`, etc.

### Key design constraints

- Canonicalization always excludes `_timestamp` and `_runtime` (defined in `canonicalize._UNSTABLE_FIELDS`). Additional keys can be excluded via `exclude_keys`.
- `summary_allowlist` / `config_allowlist` on `@xrpl_anchor` filter which W&B fields enter the payload — use these to avoid leaking private metrics on-chain.
- XRPL node defaults to testnet (`https://s.altnet.rippletest.net:51234`). Override with `XRPL_NODE_URL`.
- IPFS fetch gateway defaults to local daemon (`http://127.0.0.1:8080/ipfs`). Override with `IPFS_GATEWAY_URL` (env var) or pass `ipfs_gateway=` to `verify_anchor()` / `fetch_from_ipfs()`.
- `IncrementalAnchor` two-tier memo: seq=0 includes `wandb_run_path` (no `prev`); seq≥1 includes `prev=<64-char tx hex>` (no `wandb_run_path`). Both fit within the 256-byte XRPL limit.
- `DEFAULT_PII_KEYS` can be extended: `DEFAULT_PII_KEYS | {"my_field"}`. Pass to `IncrementalAnchor(exclude_keys=...)` or `canonicalize(exclude_keys=...)`.
- `_extract_trace_fields(call)` accesses `call._children` (private attribute) for tool-call summary; this is intentional and guarded by a broad `except`.
- `@xrpl_anchor` detects Weave ops via `hasattr(func, "call")` and uses `func.call(*args, **kwargs)` to obtain the `Call` object without monkey-patching Weave internals.
- Testnet XRP faucet: https://faucet.altnet.rippletest.net/accounts
