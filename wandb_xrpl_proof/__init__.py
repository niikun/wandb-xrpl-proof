"""
wandb-xrpl-proof: Cryptographic anchoring of W&B runs to XRPL.

Public API:
- xrpl_anchor: decorator for Weave ops
- verify_anchor: verify an anchored transaction
- canonicalize: canonical JSON serialization
- compute_hash: SHA-256 hash of canonical JSON
- build_merkle_tree: Merkle root from history chunks
"""

from wandb_xrpl_proof.anchor import build_payload, xrpl_anchor
from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.merkle import build_merkle_tree, split_history
from wandb_xrpl_proof.verify import VerificationResult, verify_anchor

__all__ = [
    "xrpl_anchor",
    "verify_anchor",
    "canonicalize",
    "compute_hash",
    "build_merkle_tree",
    "split_history",
    "build_payload",
    "VerificationResult",
]
