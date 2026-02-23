"""
wandb-xrpl-proof: Cryptographic anchoring of W&B runs to XRPL.

Public API:
- xrpl_anchor: decorator for Weave ops (single anchor at op/run end)
- IncrementalAnchor: mid-run checkpointing via hash chain
- anchor_run_end: explicit per-run anchoring
- verify_anchor: verify a single anchored transaction
- verify_chain: verify an IncrementalAnchor checkpoint chain
- canonicalize: canonical JSON serialization
- DEFAULT_PII_KEYS: default set of PII field names to exclude
- compute_hash: SHA-256 hash of canonical JSON
- compute_chain_step: one step of the incremental hash chain
- build_merkle_tree: Merkle root from history chunks
"""

__version__ = "0.2.0"

from wandb_xrpl_proof.anchor import anchor_run_end, build_payload, xrpl_anchor
from wandb_xrpl_proof.incremental import IncrementalAnchor
from wandb_xrpl_proof.canonicalize import DEFAULT_PII_KEYS, canonicalize
from wandb_xrpl_proof.hash import compute_chain_step, compute_hash
from wandb_xrpl_proof.merkle import build_merkle_tree, split_history
from wandb_xrpl_proof.verify import VerificationResult, verify_anchor, verify_chain

__all__ = [
    "xrpl_anchor",
    "anchor_run_end",
    "IncrementalAnchor",
    "verify_anchor",
    "verify_chain",
    "canonicalize",
    "DEFAULT_PII_KEYS",
    "compute_hash",
    "compute_chain_step",
    "build_merkle_tree",
    "split_history",
    "build_payload",
    "VerificationResult",
]
