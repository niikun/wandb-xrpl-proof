"""
@xrpl_anchor decorator for wandb-xrpl-proof.

Implements Section 10 of WandB_XRPL_Anchor_Spec_v0_2:

Execution flow:
1. Wrapped Weave op executes
2. W&B run retrieved
3. Payload assembled (schema_version, wandb_run_path, weave_op_name, summary, config)
4. Canonicalized
5. Hash or Merkle root generated
6. Optional IPFS upload
7. XRPL transaction submitted
8. tx_hash written back to run.summary

XRPL submission failure MUST NOT crash training (Section 13).
"""

import functools
import logging
import os
from typing import Callable, Literal

import wandb

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.ipfs import upload_to_ipfs
from wandb_xrpl_proof.merkle import build_merkle_tree, split_history
from wandb_xrpl_proof.xrpl_client import submit_anchor

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "wandb-xrpl-proof-0.2"
MEMO_SCHEMA_VERSION = "wandb-xrpl-proof-0.2"


def xrpl_anchor(
    include_summary: bool = True,
    include_config: bool = True,
    use_ipfs: bool = False,
    mode: Literal["per_op", "per_run"] = "per_op",
    summary_allowlist: list[str] | None = None,
    config_allowlist: list[str] | None = None,
    xrpl_seed_env: str = "XRPL_WALLET_SEED",
    xrpl_node_env: str = "XRPL_NODE_URL",
    ipfs_api_env: str = "IPFS_API_URL",
) -> Callable:
    """
    Weave op を XRPL にアンカリングするデコレータ。

    Args:
        include_summary: run.summary をペイロードに含める
        include_config: run.config をペイロードに含める
        use_ipfs: IPFS にペイロードをアップロードする
        mode: "per_op" (各 op 呼び出し後) or "per_run" (run 完了後)
        summary_allowlist: summary から含めるキーのリスト (None = 全て)
        config_allowlist: config から含めるキーのリスト (None = 全て)
        xrpl_seed_env: XRPL シードを保持する環境変数名
        xrpl_node_env: XRPL ノード URL の環境変数名
        ipfs_api_env: IPFS API URL の環境変数名
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)

            if mode == "per_op":
                _anchor_current_run(
                    op_name=func.__name__,
                    include_summary=include_summary,
                    include_config=include_config,
                    use_ipfs=use_ipfs,
                    summary_allowlist=summary_allowlist,
                    config_allowlist=config_allowlist,
                    xrpl_seed_env=xrpl_seed_env,
                    xrpl_node_env=xrpl_node_env,
                    ipfs_api_env=ipfs_api_env,
                )

            return result

        if mode == "per_run":
            # per_run: run finish 時にアンカリング
            original_finish = wandb.finish

            @functools.wraps(original_finish)
            def patched_finish(*args, **kwargs):
                _anchor_current_run(
                    op_name=func.__name__,
                    include_summary=include_summary,
                    include_config=include_config,
                    use_ipfs=use_ipfs,
                    summary_allowlist=summary_allowlist,
                    config_allowlist=config_allowlist,
                    xrpl_seed_env=xrpl_seed_env,
                    xrpl_node_env=xrpl_node_env,
                    ipfs_api_env=ipfs_api_env,
                )
                return original_finish(*args, **kwargs)

            wandb.finish = patched_finish

        return wrapper

    return decorator


def _anchor_current_run(
    op_name: str,
    include_summary: bool,
    include_config: bool,
    use_ipfs: bool,
    summary_allowlist: list[str] | None,
    config_allowlist: list[str] | None,
    xrpl_seed_env: str,
    xrpl_node_env: str,
    ipfs_api_env: str,
) -> None:
    """現在の W&B run をアンカリングする。失敗しても例外を伝播しない (Section 13)。"""
    try:
        run = wandb.run
        if run is None:
            logger.warning("xrpl_anchor: No active W&B run. Skipping anchor.")
            return

        wandb_run_path = f"{run.entity}/{run.project}/{run.id}"

        # ペイロード組み立て (Section 4)
        payload: dict = {
            "schema_version": SCHEMA_VERSION,
            "wandb_run_path": wandb_run_path,
            "weave_op_name": op_name,
        }

        if include_summary and run.summary:
            summary = dict(run.summary)
            if summary_allowlist:
                summary = {k: v for k, v in summary.items() if k in summary_allowlist}
            payload["summary"] = summary

        if include_config and run.config:
            config = dict(run.config)
            if config_allowlist:
                config = {k: v for k, v in config.items() if k in config_allowlist}
            payload["config"] = config

        # 正規化 & ハッシュ (Section 5, 6)
        canonical = canonicalize(payload)
        commit_hash = compute_hash(canonical)

        # IPFS アップロード (Section 8)
        cid: str | None = None
        if use_ipfs:
            ipfs_url = os.environ.get(ipfs_api_env, "http://127.0.0.1:5001")
            cid = upload_to_ipfs(payload, api_url=ipfs_url)

        # XRPL Memo 組み立て (Section 9)
        memo: dict = {
            "schema_version": MEMO_SCHEMA_VERSION,
            "wandb_run_path": wandb_run_path,
            "commit_hash": commit_hash,
        }
        if cid:
            memo["cid"] = cid

        # XRPL トランザクション送信 (Section 9)
        seed = os.environ.get(xrpl_seed_env)
        if not seed:
            logger.error("xrpl_anchor: %s not set. Skipping XRPL submission.", xrpl_seed_env)
            run.summary["xrpl_anchor_error"] = f"{xrpl_seed_env} not configured"
            return

        node_url_from_env = os.environ.get(xrpl_node_env)
        from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL
        node_url = node_url_from_env or XRPL_TESTNET_URL

        tx_hash = submit_anchor(wallet_seed=seed, memo=memo, network_url=node_url)

        # tx_hash を run.summary に書き込み (Section 10, step 8)
        run.summary["xrpl_tx_hash"] = tx_hash
        run.summary["xrpl_commit_hash"] = commit_hash
        if cid:
            run.summary["ipfs_cid"] = cid

        logger.info("xrpl_anchor: Anchored run=%s tx_hash=%s", wandb_run_path, tx_hash)

    except Exception as exc:
        # Section 13: XRPL 失敗はクラッシュしない
        logger.error("xrpl_anchor: Anchoring failed: %s", exc, exc_info=True)
        try:
            if wandb.run:
                wandb.run.summary["xrpl_anchor_error"] = str(exc)
        except Exception:
            pass


def build_payload(
    run: "wandb.sdk.wandb_run.Run",
    op_name: str,
    include_summary: bool = True,
    include_config: bool = True,
    summary_allowlist: list[str] | None = None,
    config_allowlist: list[str] | None = None,
    history_chunks: list[dict] | None = None,
) -> dict:
    """
    W&B run からアンカリングペイロードを組み立てる。

    テスト・外部利用向けの公開関数。

    Args:
        run: アクティブな wandb.Run
        op_name: Weave op 名
        include_summary: summary を含める
        include_config: config を含める
        summary_allowlist: 含める summary キー (None = 全て)
        config_allowlist: 含める config キー (None = 全て)
        history_chunks: Merkle ツリー用の history チャンク

    Returns:
        正規化前のペイロード辞書
    """
    wandb_run_path = f"{run.entity}/{run.project}/{run.id}"
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "wandb_run_path": wandb_run_path,
        "weave_op_name": op_name,
    }

    if include_summary and run.summary:
        summary = dict(run.summary)
        if summary_allowlist:
            summary = {k: v for k, v in summary.items() if k in summary_allowlist}
        payload["summary"] = summary

    if include_config and run.config:
        config = dict(run.config)
        if config_allowlist:
            config = {k: v for k, v in config.items() if k in config_allowlist}
        payload["config"] = config

    if history_chunks:
        merkle_result = build_merkle_tree(history_chunks)
        payload["history_root"] = merkle_result["history_root"]
        payload["chunk_count"] = merkle_result["chunk_count"]

    return payload
