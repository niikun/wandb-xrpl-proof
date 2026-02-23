"""
Hash module for wandb-xrpl-proof.

Implements Section 6 of WandB_XRPL_Anchor_Spec_v0_2:
- Algorithm: SHA-256
- Output: lowercase hex string
- Input: canonical JSON string (UTF-8)
"""

import hashlib


def compute_chain_step(prev_hash: str | None, chunk_hash: str) -> str:
    """
    Hash chain の 1 ステップを計算する。

    hash_chain_i = SHA-256((prev_hash or "") + chunk_hash)

    chunk j を改ざんすると chain_hash_j 以降が全て変わるため tamper-evident。

    Args:
        prev_hash: 直前の chain hash (最初のステップでは None)
        chunk_hash: 今回のチャンクの SHA-256 ハッシュ (64 文字 hex)

    Returns:
        新しい chain hash (SHA-256、小文字 64 文字 hex)
    """
    data = (prev_hash or "") + chunk_hash
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compute_hash(canonical_json: str) -> str:
    """
    正規化 JSON 文字列から SHA-256 ハッシュを計算する。

    Args:
        canonical_json: canonicalize() で生成した正規化 JSON 文字列

    Returns:
        SHA-256 ハッシュの小文字 16 進数文字列 (64 文字)
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
