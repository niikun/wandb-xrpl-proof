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
from wandb_xrpl_proof.hash import compute_chain_step, compute_hash
from wandb_xrpl_proof.ipfs import DEFAULT_IPFS_GATEWAY_URL, fetch_from_ipfs
from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, decode_memo, fetch_transaction

_EXPECTED_SCHEMA = "wandb-xrpl-proof-0.2"

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


def verify_chain(
    final_tx_hash: str,
    chunk_hashes: list[str] | None = None,
    xrpl_node: str = XRPL_TESTNET_URL,
) -> list[VerificationResult]:
    """
    IncrementalAnchor が生成した XRPL チェックポイント chain を検証する。

    final_tx_hash から prev リンクを辿って seq=0 まで遡り、以下を検証する:

    1. **構造検証**: seq が 0, 1, 2, ... と連続しているか
    2. **prev リンク検証**: 各 memo の prev が直前の tx_hash と一致するか
    3. **schema_version 一貫性**: 全チェックポイントで同一か
    4. **hash chain 検証** (chunk_hashes 指定時):
       expected_i = SHA-256((prev_chain or "") + chunk_hashes[i])
       これが on-chain commit_hash と一致するか

    Note:
        chunk_hashes を渡さない場合は構造検証のみ (commit_hash_computed = "")。
        個々のチェックポイントのコンテンツ検証には verify_anchor() を使用する。

    Args:
        final_tx_hash: chain の末尾チェックポイントの tx_hash
        chunk_hashes: 各チェックポイントに対応するチャンクの SHA-256 hex リスト (時系列順)
        xrpl_node: XRPL ノード URL

    Returns:
        VerificationResult のリスト (時系列順、seq=0 が先頭)
    """
    # --- Phase 1: prev リンクを辿って全チェックポイントを収集 ---
    collected: list[tuple[str, dict]] = []
    visited: set[str] = set()
    current_hash = final_tx_hash
    global_errors: list[str] = []

    while True:
        if current_hash in visited:
            global_errors.append(f"チェーンにサイクルを検出: tx_hash={current_hash}")
            break
        visited.add(current_hash)

        try:
            tx_result = fetch_transaction(current_hash, network_url=xrpl_node)
            memo = decode_memo(tx_result)
        except Exception as exc:
            global_errors.append(f"tx_hash={current_hash} の取得/デコードに失敗: {exc}")
            break

        collected.append((current_hash, memo))

        seq = memo.get("seq")
        if seq is None:
            global_errors.append(
                f"tx_hash={current_hash} の memo に 'seq' フィールドがありません。"
                " IncrementalAnchor が生成したトランザクションではない可能性があります。"
            )
            break

        if seq == 0:
            break  # genesis に到達

        prev = memo.get("prev")
        if not prev:
            global_errors.append(
                f"seq={seq}, tx_hash={current_hash} の memo に 'prev' フィールドがありません"
            )
            break
        current_hash = prev

    if not collected:
        return [
            VerificationResult(
                verified=False,
                tx_hash=final_tx_hash,
                commit_hash_on_chain="",
                commit_hash_computed="",
                wandb_run_path="",
                errors=global_errors or ["チェックポイントを取得できませんでした"],
            )
        ]

    # --- Phase 2: 時系列順に並べ直す (collected は逆順) ---
    collected.reverse()

    # genesis (seq=0) から wandb_run_path を取得
    wandb_run_path: str = collected[0][1].get("wandb_run_path", "")
    if not wandb_run_path:
        global_errors.append("genesis memo (seq=0) に wandb_run_path がありません")

    # --- Phase 3: 各チェックポイントを検証 ---
    results: list[VerificationResult] = []
    rolling_chain: str | None = None  # hash chain 再計算用

    for i, (tx_hash, memo) in enumerate(collected):
        errors: list[str] = list(global_errors) if i == 0 else []

        seq = memo.get("seq", -1)
        commit_hash_on_chain = memo.get("commit_hash", "")
        schema = memo.get("schema_version", "")
        commit_hash_computed = ""

        # seq 連続性チェック
        if seq != i:
            errors.append(f"seq 不整合: expected={i}, got={seq} (tx_hash={tx_hash})")

        # schema_version チェック
        if schema != _EXPECTED_SCHEMA:
            errors.append(
                f"schema_version 不一致: expected={_EXPECTED_SCHEMA!r}, got={schema!r} (seq={seq})"
            )

        # prev リンクチェック (seq≥1)
        if seq > 0:
            memo_prev = memo.get("prev", "")
            expected_prev = collected[i - 1][0]
            if memo_prev != expected_prev:
                errors.append(
                    f"prev リンク不一致 seq={seq}: memo.prev={memo_prev!r}, expected={expected_prev!r}"
                )

        # hash chain 再計算 (chunk_hashes 指定時)
        if chunk_hashes is not None:
            if i < len(chunk_hashes):
                rolling_chain = compute_chain_step(rolling_chain, chunk_hashes[i])
                commit_hash_computed = rolling_chain
                if commit_hash_computed != commit_hash_on_chain:
                    errors.append(
                        f"hash chain 不一致 seq={seq}: "
                        f"on-chain={commit_hash_on_chain}, computed={commit_hash_computed}"
                    )
            else:
                errors.append(
                    f"chunk_hashes[{i}] が指定されていません (chunk_hashes の長さ: {len(chunk_hashes)})"
                )

        results.append(
            VerificationResult(
                verified=len(errors) == 0,
                tx_hash=tx_hash,
                commit_hash_on_chain=commit_hash_on_chain,
                commit_hash_computed=commit_hash_computed,
                wandb_run_path=wandb_run_path,
                errors=errors,
            )
        )

    return results
