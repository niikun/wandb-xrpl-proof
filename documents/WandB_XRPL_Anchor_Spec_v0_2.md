# A2A Weave XRPL Anchor Specification

**Version:** 0.2
**Status:** Draft (Weave-specific)
**Repository Target:** `wandb-xrpl-proof`

---

## 1. Purpose

This specification defines a standardized method for anchoring Weave-traced W&B runs to the XRPL blockchain using canonical hashing and optional Merkle trees.

The system guarantees:

- Deterministic canonicalization of committed data
- Cryptographic integrity via SHA-256 or Merkle Root
- Off-chain storage (IPFS optional)
- On-chain timestamped commitment (XRPL Memo)
- Third-party verifiability

---

## 2. Architecture Overview

```
Weave Op Execution
→ W&B Run (summary/config/history)
→ Canonicalization
→ Hash or Merkle Root
→ Optional IPFS Storage
→ XRPL Memo Commit
```

---

## 3. Scope (Weave Integration)

This specification applies to:

- `@weave.op()` decorated functions
- W&B-backed runs
- Post-execution anchoring via `@xrpl_anchor` decorator

Anchoring MAY occur:

- Per Weave Op call
- At evaluation-only ops
- Once at run completion

---

## 4. Committed Data Model

### 4.1 Required Fields

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "weave_op_name": "function_name"
}
```

### 4.2 Optional Fields

| Field | Description |
|---|---|
| `summary` | Selected run.summary values |
| `config` | Selected run.config values |
| `code_ref` | Git commit hash |
| `data_ref` | Dataset version or artifact digest |
| `history_root` | Merkle root of history chunks |

---

## 5. Canonicalization Rules

1. JSON keys sorted lexicographically
2. UTF-8 encoding
3. No whitespace (`,` `:` only)
4. Float normalization (fixed precision recommended)
5. Exclude unstable fields:
   - `_timestamp`
   - `_runtime`
   - Host-dependent paths
   - Secrets

Python reference:

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"))
```

---

## 6. Hash Specification

- **Algorithm:** SHA-256
- **Output:** lowercase hex string
- **Input:** Canonical JSON string

```
commit_hash = sha256(canonical_json)
```

---

## 7. Merkle Tree (Optional Extension)

### 7.1 Leaf Definition

- History split into chunks (recommended: 1000 steps)
- Each chunk canonicalized and SHA-256 hashed

### 7.2 Tree Construction

- Binary Merkle tree
- Duplicate last leaf if odd
- Root = SHA256(left || right)

### 7.3 XRPL Stored Value

```json
{
  "history_root": "...",
  "chunk_count": 3
}
```

---

## 8. Off-Chain Storage (IPFS)

Payload MAY be uploaded to IPFS.

Stored content:

- Canonical payload JSON
- Optional Merkle proof data

CID MUST be included in XRPL Memo if used.

> **Implementation note:** Upload uses the Kubo-compatible HTTP API (`IPFS_API_URL`).
> Retrieval uses an IPFS HTTP gateway (`IPFS_GATEWAY_URL`). These are configured separately
> as upload and fetch have different availability requirements.

---

## 9. XRPL Commit Specification

**Transaction Type:** AccountSet
**Rationale:** XRPL protocol (and xrpl-py v2+) prohibits self-payment transactions.
`AccountSet` is the minimal-cost transaction type that supports `Memos` without requiring a destination address.

> **Note on Payment (deprecated):** Earlier drafts specified `Payment` with `Destination: Self`.
> This is no longer valid as xrpl-py v2+ rejects self-payment at the model-validation level.
> `AccountSet` is now the canonical transaction type for this specification.

Memo JSON (MemoData field, UTF-8 → hex encoded):

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "...",
  "commit_hash": "...",
  "cid": "..."
}
```

Constraints:

- MemoData: 256 bytes maximum
- Total Memos size: ~1KB
- Full payload MUST NOT be stored on-chain

---

## 10. SDK Behavior

Decorator signature:

```python
@xrpl_anchor(
    include_summary=True,
    include_config=True,
    use_ipfs=True,
    mode="per_op",          # or "per_run"
    summary_allowlist=["loss", "accuracy"],
)
```

Execution flow:

1. Wrapped Weave op executes
2. W&B run retrieved
3. Payload assembled
4. Canonicalized
5. Hash or Merkle root generated
6. Optional IPFS upload
7. XRPL transaction submitted
8. tx_hash written back to run.summary

### 10.1 mode="per_run" behavior

When `mode="per_run"`, anchoring is triggered at process exit via `atexit`, ensuring
the W&B run is complete before committing. Alternatively, `anchor_run_end(run)` can be
called explicitly for deterministic control.

---

## 11. Verification Procedure

1. Retrieve XRPL tx_hash
2. Extract MemoData
3. Obtain CID (if present)
4. Fetch payload from IPFS gateway
5. Canonicalize
6. Recompute SHA-256 or Merkle root
7. Compare with commit_hash

Match = integrity verified

---

## 12. Security Requirements

- XRPL seed MUST NOT be stored in repository
- Use environment variables or custody service
- Secrets MUST be filtered before canonicalization
- Allowlist-based summary selection recommended

---

## 13. Failure Handling

- XRPL submission failure MUST NOT crash training
- Log failure in W&B summary (`xrpl_anchor_error`)
- Optional retry queue recommended

---

## 14. Versioning

- Breaking changes require `schema_version` increment
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
