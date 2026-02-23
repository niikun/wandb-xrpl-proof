# wandb-xrpl-proof

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**A Python SDK that anchors Weave-traced W&B experiment logs to the XRPL blockchain, creating a tamper-evident audit trail.**

> [日本語版 README](README_ja.md)

```
@weave.op() → W&B Run → Canonicalize → SHA-256 / Hash Chain → (IPFS) → XRPL Memo
```

---

## Why

LLM evaluation and ML experiment logs can be altered after the fact.
By combining XRPL on-chain timestamps with SHA-256 hashes, this SDK creates an **independently verifiable audit trail** that third parties can validate without trusting any single party.

---

## How it works

| Layer | Role |
|-------|------|
| **Weave** | `@weave.op()` automatically traces function inputs, outputs, and execution time |
| **W&B** | Records metrics, config, and summary |
| **Canonicalization** | Sorts JSON keys, removes whitespace, excludes unstable fields → deterministic byte sequence |
| **SHA-256** | Generates a 64-character `commit_hash` from the canonical JSON |
| **IPFS** *(optional)* | Stores the full payload by content address |
| **XRPL** | Records `commit_hash` on-chain as an AccountSet transaction Memo |

Only a lightweight memo of **≤ 256 bytes** is stored on-chain.

---

## Quickstart

```bash
pip install -e ".[dev]"

# Set up environment variables
cp .env.example .env
# Edit .env: XRPL_WALLET_SEED, WANDB_API_KEY
```

Get testnet XRP: https://faucet.altnet.rippletest.net/accounts

---

## Three Ways to Use It

### 1. `@xrpl_anchor` — Anchor once at run end

When stacked with `@weave.op()`, the Weave trace ID and **input/output hashes** are automatically included in the payload.

```python
import weave, wandb
from wandb_xrpl_proof import xrpl_anchor

weave.init("my-project")

@xrpl_anchor(
    summary_allowlist=["avg_f1"],      # only expose metrics you're comfortable making public
    save_payload_path="payload.json",  # save payload locally for IPFS-free verification
)
@weave.op()
def evaluate(prompt: str, response: str) -> dict:
    ...

with wandb.init(project="my-project"):
    evaluate(prompt="...", response="...")
    # → run.summary["xrpl_tx_hash"]     : XRPL transaction hash
    # → run.summary["xrpl_commit_hash"] : SHA-256 of the payload
    # → run.summary["weave_trace_url"]  : direct link to Weave UI
    # → payload.json                    : verification payload (no IPFS needed)
```

**Payload fields when used with `@weave.op()`:**

| Field | Content |
|---|---|
| `weave_call_id` | Weave trace ID |
| `weave_input_hash` | `sha256(canonicalize(call.inputs))` |
| `weave_output_hash` | `sha256(canonicalize(call.output))` |
| `summary` | W&B summary filtered by `summary_allowlist` |
| `config` | W&B config filtered by `config_allowlist` |

### 2. `IncrementalAnchor` — Checkpoint periodically during a run

Writes a hash-chain checkpoint to XRPL every `chunk_size` rows.
Use `evaluate.call()` to receive a Weave Call object and include input/output hashes and tool call summaries in the chain.

```python
import weave, wandb
from wandb_xrpl_proof import IncrementalAnchor, DEFAULT_PII_KEYS

weave.init("my-project")

@weave.op()
def evaluate(prompt: str, response: str) -> dict:
    ...

with wandb.init(project="my-project") as run:
    with IncrementalAnchor(
        run,
        chunk_size=1000,
        exclude_keys=DEFAULT_PII_KEYS,  # strip PII before hashing
    ) as anchor:
        for sample in dataset:
            result, weave_call = evaluate.call(
                prompt=sample["prompt"],
                response=sample["response"],
            )
            # bundle metrics + Weave trace into the same chunk
            anchor.log(
                {"f1": result["f1"], "step": step},
                weave_call=weave_call,
            )
    # close() auto-flushes any remaining partial chunk
    # run.summary["xrpl_checkpoint_txs"] holds the full list of tx_hashes
```

**Per-row fields in each chunk:**

| Field | Content |
|---|---|
| metrics | Caller-supplied values (`f1`, `loss`, etc.) |
| `weave_call_id` | Weave trace ID |
| `weave_op_name` | Op name |
| `weave_input_hash` | `sha256(canonicalize(inputs))` |
| `weave_output_hash` | `sha256(canonicalize(output))` |
| `weave_tool_calls` | List of child call op names (tool calling) |

### 3. `anchor_run_end` — Explicit single anchor

```python
from wandb_xrpl_proof import anchor_run_end

with wandb.init(project="my-project") as run:
    train(run)
    anchor_run_end(
        run,
        summary_allowlist=["loss"],
        save_payload_path="payload.json",  # save payload locally for IPFS-free verification
    )
```

---

## Verification

### Hash chain verification for `IncrementalAnchor` — no IPFS needed

`run_demo.py` generates `demo_proof.json` after execution. This file contains `chunk_hashes` for each checkpoint, enabling hash-chain recomputation and comparison without a local IPFS node.

```bash
# Run the demo → generates demo/demo_proof.json
python demo/run_demo.py --samples 12 --chunk-size 4

# Walk XRPL prev links, recompute hash chain, and verify
python demo/verify_demo.py --chain --proof demo/demo_proof.json
```

Each checkpoint is verified for:

1. **Structure** — `seq` continuity, `prev` link integrity, `schema_version`
2. **Hash chain recomputation** — `expected_i = SHA-256((chain_{i-1} or "") + chunk_hash_i)` matches the on-chain `commit_hash`

Equivalent Python API:

```python
from wandb_xrpl_proof import verify_chain

# passing chunk_hashes also recomputes and verifies the hash chain
results = verify_chain(
    final_tx_hash="<LAST_TX_HASH>",
    chunk_hashes=anchor.chunk_hashes,
)
print(all(r.verified for r in results))  # True = no tampering detected
```

### Single anchor verification — `@xrpl_anchor`

```bash
# When anchored with use_ipfs=True (requires a local IPFS daemon)
python demo/verify_demo.py --tx <TX_HASH>

# When passing the payload as a file (no IPFS needed)
python demo/verify_demo.py --tx <TX_HASH> --payload payload.json
```

Python API:

```python
from wandb_xrpl_proof import verify_anchor

result = verify_anchor(tx_hash="<TX_HASH>", payload=payload)
print(result.verified)  # True = integrity confirmed
```

---

## PII Exclusion

```python
from wandb_xrpl_proof import DEFAULT_PII_KEYS

# DEFAULT_PII_KEYS covers: email, name, phone, ip_address, ssn, password, token, ...
exclude = DEFAULT_PII_KEYS | {"internal_user_id"}

# Pass directly to canonicalize
from wandb_xrpl_proof import canonicalize
canonical = canonicalize(payload, exclude_keys=exclude)
```

---

## Tests

```bash
# Unit tests (no external connections required)
pytest tests/unit/ -v

# Integration tests (XRPL testnet + W&B — requires .env)
pytest tests/integration/ -v
```

---

## Project Structure

```
wandb_xrpl_proof/
├── canonicalize.py   # canonicalization + DEFAULT_PII_KEYS
├── hash.py           # SHA-256 + compute_chain_step
├── merkle.py         # Binary Merkle tree (for history)
├── ipfs.py           # IPFS HTTP API (Kubo-compatible)
├── xrpl_client.py    # XRPL AccountSet submit / fetch
├── anchor.py         # @xrpl_anchor, anchor_run_end, build_payload
├── incremental.py    # IncrementalAnchor + _extract_trace_fields
└── verify.py         # verify_anchor, verify_chain

demo/
├── run_demo.py       # IncrementalAnchor × Weave × XRPL demo (--samples N / --chunk-size K)
├── verify_demo.py    # proof verification (--chain --proof demo_proof.json / --tx TX_HASH)
└── demo_proof.json   # proof file generated by run_demo.py (includes chunk_hashes)
```

---

## Spec

Implementation specification: [`documents/WandB_XRPL_Anchor_Spec_v0_2.md`](documents/WandB_XRPL_Anchor_Spec_v0_2.md)
