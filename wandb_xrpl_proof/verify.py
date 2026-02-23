"""
Verification module for wandb-xrpl-proof.

Implements Section 11 of WandB_XRPL_Anchor_Spec_v0_2:

Verification procedure:
1. Retrieve XRPL tx_hash
2. Extract MemoData
3. Obtain CID (if present)
4. Fetch payload
5. Canonicalize
6. Recompute SHA-256 or Merkle root
7. Compare with commit_hash

Match = integrity verified
"""

import logging
import os
from dataclasses import dataclass, field

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.ipfs import DEFAULT_IPFS_GATEWAY_URL, fetch_from_ipfs
from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, decode_memo, fetch_transaction

_DEFAULT_IPFS_GATEWAY = os.environ.get("IPFS_GATEWAY_URL", DEFAULT_IPFS_GATEWAY_URL)

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """検証結果を表すデータクラス。"""

    verified: bool
    tx_hash: str
    commit_hash_on_chain: str
    commit_hash_computed: str
    wandb_run_path: str
    cid: str | None = None
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.verified


def verify_anchor(
    tx_hash: str,
    payload: dict | None = None,
    xrpl_node: str = XRPL_TESTNET_URL,
    ipfs_gateway: str = _DEFAULT_IPFS_GATEWAY,
) -> VerificationResult:
    """
    XRPL トランザクションのアンカリングを検証する。

    ペイロードが提供されない場合、CID が存在すれば IPFS から取得する。

    Args:
        tx_hash: 検証する XRPL トランザクションハッシュ
        payload: 検証対象のペイロード辞書 (None の場合は IPFS から取得)
        xrpl_node: XRPL ノード URL
        ipfs_gateway: IPFS ゲートウェイ URL

    Returns:
        VerificationResult (bool に変換可能: True = 整合性確認済み)
    """
    errors: list[str] = []

    # Step 1 & 2: XRPL からトランザクション取得 & MemoData 抽出
    try:
        tx_result = fetch_transaction(tx_hash, network_url=xrpl_node)
        memo = decode_memo(tx_result)
    except Exception as exc:
        return VerificationResult(
            verified=False,
            tx_hash=tx_hash,
            commit_hash_on_chain="",
            commit_hash_computed="",
            wandb_run_path="",
            errors=[f"Failed to fetch/decode XRPL transaction: {exc}"],
        )

    commit_hash_on_chain: str = memo.get("commit_hash", "")
    wandb_run_path: str = memo.get("wandb_run_path", "")
    cid: str | None = memo.get("cid")

    # Step 3 & 4: ペイロード取得
    if payload is None:
        if cid:
            try:
                payload = fetch_from_ipfs(cid, gateway_url=ipfs_gateway)
            except Exception as exc:
                errors.append(f"Failed to fetch payload from IPFS (CID={cid}): {exc}")
                return VerificationResult(
                    verified=False,
                    tx_hash=tx_hash,
                    commit_hash_on_chain=commit_hash_on_chain,
                    commit_hash_computed="",
                    wandb_run_path=wandb_run_path,
                    cid=cid,
                    errors=errors,
                )
        else:
            errors.append("No payload provided and no CID in XRPL Memo.")
            return VerificationResult(
                verified=False,
                tx_hash=tx_hash,
                commit_hash_on_chain=commit_hash_on_chain,
                commit_hash_computed="",
                wandb_run_path=wandb_run_path,
                errors=errors,
            )

    # Step 5 & 6: 正規化して SHA-256 再計算
    try:
        canonical = canonicalize(payload)
        commit_hash_computed = compute_hash(canonical)
    except Exception as exc:
        errors.append(f"Canonicalization/hash computation failed: {exc}")
        return VerificationResult(
            verified=False,
            tx_hash=tx_hash,
            commit_hash_on_chain=commit_hash_on_chain,
            commit_hash_computed="",
            wandb_run_path=wandb_run_path,
            cid=cid,
            errors=errors,
        )

    # Step 7: commit_hash と比較
    verified = commit_hash_computed == commit_hash_on_chain
    if not verified:
        errors.append(
            f"Hash mismatch: on-chain={commit_hash_on_chain}, computed={commit_hash_computed}"
        )

    return VerificationResult(
        verified=verified,
        tx_hash=tx_hash,
        commit_hash_on_chain=commit_hash_on_chain,
        commit_hash_computed=commit_hash_computed,
        wandb_run_path=wandb_run_path,
        cid=cid,
        errors=errors,
    )
