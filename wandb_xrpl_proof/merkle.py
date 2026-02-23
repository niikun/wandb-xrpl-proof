"""
Merkle tree module for wandb-xrpl-proof.

Implements Section 7 of WandB_XRPL_Anchor_Spec_v0_2:
- Leaf: each history chunk (default 1000 steps) canonicalized and SHA-256 hashed
- Binary Merkle tree, duplicate last leaf if odd
- Root = SHA256(left || right)
- XRPL stored value: {"history_root": "...", "chunk_count": N}
"""

import hashlib

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash

DEFAULT_CHUNK_SIZE = 1000


def build_merkle_tree(chunks: list[dict]) -> dict:
    """
    history チャンクリストから Merkle ツリーを構築し、ルートを返す。

    Args:
        chunks: history チャンクの辞書リスト (各チャンクは dict)

    Returns:
        {"history_root": "<hex>", "chunk_count": N}

    Raises:
        ValueError: chunks が空の場合
    """
    if not chunks:
        raise ValueError("chunks must not be empty")

    leaves = [compute_hash(canonicalize(chunk)) for chunk in chunks]
    root = _compute_merkle_root(leaves)
    return {"history_root": root, "chunk_count": len(chunks)}


def split_history(history: list[dict], chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[list[dict]]:
    """
    history ステップリストを chunk_size ごとに分割する。

    Args:
        history: W&B run history の各ステップ辞書のリスト
        chunk_size: チャンクあたりのステップ数 (デフォルト 1000)

    Returns:
        チャンクのリスト (各チャンクは dict のリスト)
    """
    return [history[i:i + chunk_size] for i in range(0, len(history), chunk_size)]


def _compute_merkle_root(leaves: list[str]) -> str:
    """
    リーフハッシュリストから Merkle ルートを計算する。

    仕様:
    - 奇数リーフは最後を複製して偶数にする
    - 各ノード = SHA256(left || right) (バイト連結)
    """
    nodes = list(leaves)

    while len(nodes) > 1:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1])  # 奇数の場合、最後のリーフを複製

        next_level = []
        for i in range(0, len(nodes), 2):
            combined = bytes.fromhex(nodes[i]) + bytes.fromhex(nodes[i + 1])
            parent = hashlib.sha256(combined).hexdigest()
            next_level.append(parent)
        nodes = next_level

    return nodes[0]
