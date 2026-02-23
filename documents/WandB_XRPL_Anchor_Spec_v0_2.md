# A2A Weave XRPL Anchor Specification

**Version:** 0.2
**Status:** Implemented
**Repository Target:** `wandb-xrpl-proof`

---

## 1. Purpose

This specification defines a standardized method for anchoring Weave-traced W&B runs to the XRPL blockchain using canonical hashing and optional Merkle trees.

The system guarantees:

- Deterministic canonicalization of committed data
- Cryptographic integrity via SHA-256 (single anchor) or Hash Chain (incremental anchor)
- Off-chain storage (IPFS optional; local file as primary alternative)
- On-chain timestamped commitment (XRPL Memo)
- Third-party verifiability without IPFS when `save_payload_path` or `chunk_hashes` is used

---

## 2. Architecture Overview

```
Weave Op Execution
→ W&B Run (summary / config / history)
→ Canonicalization  (+PII exclusion)
→ SHA-256  or  Hash Chain step
→ Optional IPFS Storage  /  Local File (save_payload_path)
→ XRPL Memo Commit (AccountSet)
```

Two anchoring modes are provided:

| Mode | Class / Function | XRPL Txs | Use case |
|---|---|---|---|
| Single anchor | `@xrpl_anchor`, `anchor_run_end` | 1 per run | Final run summary |
| Incremental anchor | `IncrementalAnchor` | 1 per chunk | Long runs, streaming eval |

---

## 3. Scope (Weave Integration)

This specification applies to:

- `@weave.op()` decorated functions
- W&B-backed runs
- Post-execution anchoring via `@xrpl_anchor` decorator or `IncrementalAnchor` context manager

Anchoring MAY occur:

- Per Weave Op call (`mode="per_op"`)
- Once at run completion (`mode="per_run"`, `anchor_run_end`)
- Periodically during a run (`IncrementalAnchor`, every `chunk_size` rows)

---

## 4. Committed Data Model

### 4.1 Required Fields (all modes)

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "weave_op_name": "function_name"
}
```

### 4.2 Optional Fields — Single Anchor (`@xrpl_anchor`, `anchor_run_end`)

| Field | Description |
|---|---|
| `summary` | Selected `run.summary` values (filtered by `summary_allowlist`) |
| `config` | Selected `run.config` values (filtered by `config_allowlist`) |
| `weave_call_id` | Weave trace call ID (present when wrapping a `@weave.op()`) |
| `weave_input_hash` | `sha256(canonicalize(call.inputs))` |
| `weave_output_hash` | `sha256(canonicalize(call.output))` — absent if output is `None` |
| `history_root` | Merkle root of history chunks (optional extension) |
| `chunk_count` | Number of chunks in Merkle tree |

### 4.3 Per-Row Fields — Incremental Anchor (`IncrementalAnchor`)

Each row pushed via `record()` / `log()` contains the caller-supplied metrics plus any Weave trace fields extracted from the supplied `weave_call`:

| Field | Description |
|---|---|
| *(metrics)* | Caller-supplied key/value pairs (e.g. `f1`, `loss`, `step`) |
| `weave_call_id` | Weave trace call ID |
| `weave_op_name` | Op short name (`call.func_name`) |
| `weave_input_hash` | `sha256(canonicalize(call.inputs))` |
| `weave_output_hash` | `sha256(canonicalize(call.output))` — absent if output is `None` |
| `weave_tool_calls` | List of child call op names — absent if no children |

The full chunk (list of rows) is canonicalized together to produce `chunk_hash`.

---

## 5. Canonicalization Rules

1. JSON keys sorted lexicographically (recursively)
2. UTF-8 encoding
3. No whitespace (compact separators `,` and `:`)
4. Unstable fields always excluded: `_timestamp`, `_runtime`
5. Additional keys excluded via `exclude_keys` parameter (e.g. PII fields)

Python reference:

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"))
```

`DEFAULT_PII_KEYS` (frozenset) provides a ready-made exclusion set covering `email`, `name`, `phone`, `ip_address`, `ssn`, `password`, `token`, and related fields. It can be extended:

```python
from wandb_xrpl_proof import DEFAULT_PII_KEYS
exclude = DEFAULT_PII_KEYS | {"internal_user_id"}
```

---

## 6. Hash Specification

### 6.1 Single Hash

- **Algorithm:** SHA-256
- **Output:** lowercase hex string (64 chars)
- **Input:** Canonical JSON string (UTF-8)

```
commit_hash = sha256(canonical_json)
```

### 6.2 Hash Chain (IncrementalAnchor)

Each checkpoint covers one chunk of rows. The chain links all checkpoints together so that modifying any chunk invalidates all subsequent chain hashes.

```
chunk_hash_i   = sha256(canonicalize(chunk_i_rows))
chain_hash_0   = sha256("" + chunk_hash_0)            # genesis (no prev)
chain_hash_i   = sha256(chain_hash_{i-1} + chunk_hash_i)   # i ≥ 1
```

`commit_hash` stored on XRPL = `chain_hash_i` for checkpoint `i`.

Verification recomputes `chain_hash_i` from locally held `chunk_hashes` (see Section 11.2).

---

## 7. Merkle Tree (Optional Extension)

### 7.1 Leaf Definition

- History split into chunks (recommended: 1000 steps)
- Each chunk canonicalized and SHA-256 hashed

### 7.2 Tree Construction

- Binary Merkle tree
- Duplicate last leaf if odd
- Root = `SHA256(left || right)`

### 7.3 XRPL Stored Value

```json
{
  "history_root": "<merkle-root-hex>",
  "chunk_count": 3
}
```

---

## 8. Off-Chain Storage

### 8.1 IPFS (optional)

Payload MAY be uploaded to IPFS (Kubo-compatible HTTP API via `IPFS_API_URL`).
CID MUST be included in the XRPL Memo if used.
Retrieval uses an IPFS HTTP gateway (`IPFS_GATEWAY_URL`).

> Upload and fetch are configured separately because they have different availability requirements (local daemon vs. public gateway).

### 8.2 Local File (`save_payload_path`)

As an IPFS-free alternative, the full payload JSON MAY be saved to a local file by passing `save_payload_path="payload.json"` to `@xrpl_anchor` or `anchor_run_end`. The file can be passed directly to `verify_demo.py --payload` for offline verification.

---

## 9. XRPL Commit Specification

**Transaction Type:** `AccountSet`

> **Rationale:** XRPL protocol (and xrpl-py v2+) prohibits self-payment transactions.
> `AccountSet` is the minimal-cost transaction type that supports `Memos` without requiring a destination address.

`MemoData` field: UTF-8 JSON → hex encoded. Maximum **256 bytes**.

### 9.1 Single Anchor Memo (`@xrpl_anchor`, `anchor_run_end`)

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<sha256-hex-64>",
  "cid": "<ipfs-cid-if-used>"
}
```

`cid` is omitted when IPFS is not used.

### 9.2 IncrementalAnchor Memo — Genesis (`seq=0`)

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<chain-hash-hex-64>",
  "seq": 0
}
```

Binds the W&B run path to the chain. No `prev` field. (~170 bytes ✓)

### 9.3 IncrementalAnchor Memo — Chained (`seq ≥ 1`)

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "commit_hash": "<chain-hash-hex-64>",
  "prev": "<prev-tx-hash-hex-64>",
  "seq": 5
}
```

`wandb_run_path` is omitted; verifiers walk back to the genesis to retrieve it. (~205 bytes ✓)

---

## 10. SDK Behavior

### 10.1 `@xrpl_anchor` Decorator

```python
@xrpl_anchor(
    include_summary=True,
    include_config=True,
    use_ipfs=False,
    mode="per_op",               # "per_op" | "per_run"
    summary_allowlist=["loss", "accuracy"],
    config_allowlist=None,
    xrpl_seed_env="XRPL_WALLET_SEED",
    xrpl_node_env="XRPL_NODE_URL",
    save_payload_path="payload.json",  # optional; enables IPFS-free verification
)
@weave.op()
def my_eval(prompt: str, response: str) -> dict:
    ...
```

Execution flow:

1. `func.call(*args, **kwargs)` executed (Weave op detected via `hasattr(func, "call")`)
2. `weave_call_id`, `weave_input_hash`, `weave_output_hash` extracted from the returned `Call`
3. Payload assembled from run + Weave fields
4. Canonicalized; `commit_hash` = `sha256(canonical)`
5. Optional IPFS upload
6. XRPL `AccountSet` submitted
7. `run.summary["xrpl_tx_hash"]` and `run.summary["xrpl_commit_hash"]` written
8. Payload saved to `save_payload_path` if specified

`mode="per_run"` registers the anchor via `atexit`; use `anchor_run_end(run)` for explicit control.

### 10.2 `anchor_run_end`

Explicit single-anchor at run completion. Accepts the same options as `@xrpl_anchor` (except `mode`).

```python
anchor_run_end(
    run,
    summary_allowlist=["loss"],
    save_payload_path="payload.json",
)
```

### 10.3 `IncrementalAnchor`

Mid-run checkpointing. Hash chain links all checkpoints; tampering with any chunk invalidates subsequent hashes.

```python
with IncrementalAnchor(
    run,
    chunk_size=1000,
    exclude_keys=DEFAULT_PII_KEYS,
    xrpl_seed_env="XRPL_WALLET_SEED",
    xrpl_node_env="XRPL_NODE_URL",
) as anchor:
    for step in range(N):
        result, weave_call = evaluate.call(...)
        anchor.log({"f1": result["f1"], "step": step}, weave_call=weave_call)
# close() flushes partial chunk; run.summary["xrpl_checkpoint_txs"] updated
```

Key properties:

| Property | Type | Description |
|---|---|---|
| `tx_hashes` | `list[str]` | All committed checkpoint tx_hashes (chronological) |
| `chunk_hashes` | `list[str]` | Per-chunk data SHA-256 before chain step — pass to `verify_chain` |
| `seq` | `int` | Next checkpoint sequence number |

Design constraints:

- No `run.history()` polling (push-based local buffer, O(1) per step)
- `record()` / `log()` are lock-protected (thread-safe)
- XRPL failures never crash training (errors written to `run.summary["xrpl_anchor_error"]`)
- `close()` is idempotent

---

## 11. Verification Procedure

### 11.1 Single Anchor (`verify_anchor`)

```python
result = verify_anchor(tx_hash="<TX_HASH>", payload=payload_dict)
# result.verified: True = integrity confirmed
```

1. Fetch XRPL transaction → decode MemoData
2. Obtain payload (from `payload=` argument, local file, or IPFS via CID)
3. Canonicalize payload → recompute `sha256`
4. Compare with `commit_hash` from memo

CLI:
```bash
python demo/verify_demo.py --tx <TX_HASH> --payload payload.json
```

### 11.2 Hash Chain (`verify_chain`)

```python
results = verify_chain(
    final_tx_hash="<LAST_TX_HASH>",
    chunk_hashes=anchor.chunk_hashes,   # from demo_proof.json
)
assert all(r.verified for r in results)
```

Walk procedure (IPFS not required):

1. Walk `prev` links from `final_tx_hash` back to `seq=0`
2. Reverse to chronological order
3. For each checkpoint verify:
   - **seq continuity:** `seq == i`
   - **prev-link integrity:** `memo.prev == tx_hashes[i-1]`
   - **schema_version:** equals `"wandb-xrpl-proof-0.2"`
   - **hash chain** (when `chunk_hashes` supplied): `sha256((prev_chain or "") + chunk_hashes[i]) == memo.commit_hash`

When `chunk_hashes=None`, only structural verification is performed (`commit_hash_computed` is empty string).

CLI:
```bash
python demo/verify_demo.py --chain --proof demo/demo_proof.json
```

`demo_proof.json` is generated by `run_demo.py --samples N --chunk-size K` and contains:
`final_tx_hash`, `tx_hashes`, `chunk_hashes`, `samples`, `chunk_size`, `wandb_run_url`.

---

## 12. Security Requirements

- XRPL seed MUST NOT be stored in repository; use environment variables (`XRPL_WALLET_SEED`)
- Secrets MUST be filtered before canonicalization via `exclude_keys`
- `DEFAULT_PII_KEYS` covers common PII; extend as needed: `DEFAULT_PII_KEYS | {"my_field"}`
- `summary_allowlist` / `config_allowlist` SHOULD be used to avoid leaking private metrics on-chain
- XRPL node defaults to testnet (`https://s.altnet.rippletest.net:51234`); override with `XRPL_NODE_URL`
- IPFS upload API and fetch gateway are configured separately (`IPFS_API_URL`, `IPFS_GATEWAY_URL`) because upload requires a locally running daemon while public gateways only serve publicly pinned content

---

## 13. Failure Handling

- XRPL submission failure MUST NOT crash training
- All errors logged via Python `logging`; additionally written to `run.summary["xrpl_anchor_error"]`
- `IncrementalAnchor` state is NOT advanced on failed submit (seq, chain_hash, prev_tx_hash unchanged)
- Optional retry queue recommended for production use

---

## 14. Versioning

- Breaking changes require `schema_version` increment (e.g. `"wandb-xrpl-proof-0.3"`)
- Minor updates retain backward compatibility

---

## 15. Future Extensions

- Multi-sig XRPL support
- Scheduled anchoring
- ZK proof integration
- Cross-chain anchoring
- DID integration

---

End of Specification
