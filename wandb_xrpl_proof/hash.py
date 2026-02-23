"""
Hash module for wandb-xrpl-proof.

Implements Section 6 of WandB_XRPL_Anchor_Spec_v0_2:
- Algorithm: SHA-256
- Output: lowercase hex string
- Input: canonical JSON string (UTF-8)
"""

import hashlib


def compute_hash(canonical_json: str) -> str:
    """
    正規化 JSON 文字列から SHA-256 ハッシュを計算する。

    Args:
        canonical_json: canonicalize() で生成した正規化 JSON 文字列

    Returns:
        SHA-256 ハッシュの小文字 16 進数文字列 (64 文字)
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
