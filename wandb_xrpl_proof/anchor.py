
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

import atexit
import functools
import logging
import os
from typing import Callable, Literal

import wandb

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.ipfs import upload_to_ipfs
from wandb_xrpl_proof.merkle import build_merkle_tree
from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, submit_anchor

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "wandb-xrpl-proof-0.2"


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
    save_payload_path: str | None = None,
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
        save_payload_path: ペイロードを保存する JSON ファイルパス。
            指定すると IPFS なしで verify_demo.py --payload で検証できる。
    """

    def decorator(func: Callable) -> Callable:
        # per_run: atexit 登録済みかをインスタンスごとに追跡
        _atexit_registered = False

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal _atexit_registered

            # Weave op (.call 属性を持つ) なら call() で実行してトレース ID を取得する
            weave_call_id: str | None = None
            weave_ui_url: str | None = None
            weave_input_hash: str | None = None
            weave_output_hash: str | None = None
            if hasattr(func, "call"):
                result, weave_call = func.call(*args, **kwargs)
                try:
                    weave_call_id = str(weave_call.id)
                    weave_ui_url = weave_call.ui_url
                except Exception:
                    pass
                try:
                    weave_input_hash = compute_hash(canonicalize(dict(weave_call.inputs)))
                except Exception:
                    pass
                try:
                    if weave_call.output is not None:
                        weave_output_hash = compute_hash(canonicalize(weave_call.output))
                except Exception:
                    pass
            else:
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
                    weave_call_id=weave_call_id,
                    weave_ui_url=weave_ui_url,
                    weave_input_hash=weave_input_hash,
                    weave_output_hash=weave_output_hash,
                    save_payload_path=save_payload_path,
                )

            elif mode == "per_run" and not _atexit_registered:
                # monkey-patch を避け atexit で登録。
                # atexit は LIFO のため wandb.init() 後に登録すれば
                # wandb 自身の atexit より先に実行される。
                # 明示的な制御が必要な場合は anchor_run_end(run) を使うこと。
                atexit.register(
                    _anchor_current_run,
                    op_name=func.__name__,
                    include_summary=include_summary,
                    include_config=include_config,
                    use_ipfs=use_ipfs,
                    summary_allowlist=summary_allowlist,
                    config_allowlist=config_allowlist,
                    xrpl_seed_env=xrpl_seed_env,
                    xrpl_node_env=xrpl_node_env,
                    ipfs_api_env=ipfs_api_env,
                    save_payload_path=save_payload_path,
                )
                _atexit_registered = True

            return result

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
    run: "wandb.sdk.wandb_run.Run | None" = None,
    weave_call_id: str | None = None,
    weave_ui_url: str | None = None,
    weave_input_hash: str | None = None,
    weave_output_hash: str | None = None,
    save_payload_path: str | None = None,
) -> None:
    """現在の W&B run をアンカリングする。失敗しても例外を伝播しない (Section 13)。"""
    target_run = run or wandb.run
    try:
        if target_run is None:
            logger.warning("xrpl_anchor: No active W&B run. Skipping anchor.")
            return

        # ペイロード組み立て (Section 4)
        payload = build_payload(
            run=target_run,
            op_name=op_name,
            include_summary=include_summary,
            include_config=include_config,
            summary_allowlist=summary_allowlist,
            config_allowlist=config_allowlist,
            weave_call_id=weave_call_id,
            weave_input_hash=weave_input_hash,
            weave_output_hash=weave_output_hash,
        )

        # 正規化 & ハッシュ (Section 5, 6)
        canonical = canonicalize(payload)
        commit_hash = compute_hash(canonical)

        # IPFS アップロード (Section 8)
        cid: str | None = None
        if use_ipfs:
            ipfs_url = os.environ.get(ipfs_api_env, "http://127.0.0.1:5001")
            cid = upload_to_ipfs(payload, api_url=ipfs_url)

        # XRPL Memo 組み立て (Section 9)
        wandb_run_path: str = payload["wandb_run_path"]
        memo: dict = {
            "schema_version": SCHEMA_VERSION,
            "wandb_run_path": wandb_run_path,
            "commit_hash": commit_hash,
        }
        if cid:
            memo["cid"] = cid

        # XRPL トランザクション送信 (Section 9)
        seed = os.environ.get(xrpl_seed_env)
        if not seed:
            logger.error("xrpl_anchor: %s not set. Skipping XRPL submission.", xrpl_seed_env)
            target_run.summary["xrpl_anchor_error"] = f"{xrpl_seed_env} not configured"
            return

        node_url = os.environ.get(xrpl_node_env) or XRPL_TESTNET_URL
        tx_hash = submit_anchor(wallet_seed=seed, memo=memo, network_url=node_url)

        # tx_hash を run.summary に書き込み (Section 10, step 8)
        target_run.summary["xrpl_tx_hash"] = tx_hash
        target_run.summary["xrpl_commit_hash"] = commit_hash
        if cid:
            target_run.summary["ipfs_cid"] = cid
        if weave_ui_url:
            target_run.summary["weave_trace_url"] = weave_ui_url

        # ペイロードをローカルファイルに保存 (IPFS なしで検証するため)
        if save_payload_path:
            import json as _json
            try:
                with open(save_payload_path, "w", encoding="utf-8") as f:
                    _json.dump(payload, f, ensure_ascii=False, indent=2)
                logger.info("xrpl_anchor: Payload saved to %s", save_payload_path)
            except Exception as exc:
                logger.warning("xrpl_anchor: Failed to save payload to %s: %s", save_payload_path, exc)

        logger.info("xrpl_anchor: Anchored run=%s tx_hash=%s", wandb_run_path, tx_hash)

    except Exception as exc:
        # Section 13: XRPL 失敗はクラッシュしない
        logger.error("xrpl_anchor: Anchoring failed: %s", exc, exc_info=True)
        try:
            if target_run:
                target_run.summary["xrpl_anchor_error"] = str(exc)
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
    weave_call_id: str | None = None,
    weave_input_hash: str | None = None,
    weave_output_hash: str | None = None,
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
        weave_call_id: Weave トレース call ID (文字列、省略可)
        weave_input_hash: sha256(canonicalize(call.inputs)) (省略可)
        weave_output_hash: sha256(canonicalize(call.output)) (省略可)

    Returns:
        正規化前のペイロード辞書
    """
    wandb_run_path = f"{run.entity}/{run.project}/{run.id}"
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "wandb_run_path": wandb_run_path,
        "weave_op_name": op_name,
    }
    if weave_call_id:
        payload["weave_call_id"] = weave_call_id
    if weave_input_hash:
        payload["weave_input_hash"] = weave_input_hash
    if weave_output_hash:
        payload["weave_output_hash"] = weave_output_hash

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

def anchor_run_end(
    run: "wandb.sdk.wandb_run.Run | None" = None,
    op_name: str = "run_end",
    include_summary: bool = True,
    include_config: bool = True,
    use_ipfs: bool = False,
    summary_allowlist: list[str] | None = None,
    config_allowlist: list[str] | None = None,
    xrpl_seed_env: str = "XRPL_WALLET_SEED",
    xrpl_node_env: str = "XRPL_NODE_URL",
    ipfs_api_env: str = "IPFS_API_URL",
    save_payload_path: str | None = None,
) -> None:
    """
    run 終了時に明示的にアンカリングを行う関数。

    `mode="per_run"` の atexit に頼らず、呼び出しタイミングを完全に制御したい場合に使用する。
    wandb.finish() を呼ぶ直前に実行するのが推奨。

    Args:
        run: 対象の wandb.Run (None の場合は wandb.run を使用)
        op_name: ペイロードに記録する op 名
        include_summary: summary をペイロードに含める
        include_config: config をペイロードに含める
        use_ipfs: IPFS にペイロードをアップロードする
        summary_allowlist: 含める summary キーのリスト (None = 全て)
        config_allowlist: 含める config キーのリスト (None = 全て)
        xrpl_seed_env: XRPL シードの環境変数名
        xrpl_node_env: XRPL ノード URL の環境変数名
        ipfs_api_env: IPFS API URL の環境変数名
        save_payload_path: ペイロードを保存する JSON ファイルパス (IPFS 不要で検証するため)

    Example:
        with wandb.init(project="my-project") as run:
            train(run)
            anchor_run_end(run, summary_allowlist=["loss"], save_payload_path="payload.json")
    """
    target_run = run or wandb.run
    if target_run is None:
        logger.warning("anchor_run_end: No active W&B run. Skipping.")
        return

    _anchor_current_run(
        op_name=op_name,
        include_summary=include_summary,
        include_config=include_config,
        use_ipfs=use_ipfs,
        summary_allowlist=summary_allowlist,
        config_allowlist=config_allowlist,
        xrpl_seed_env=xrpl_seed_env,
        xrpl_node_env=xrpl_node_env,
        ipfs_api_env=ipfs_api_env,
        run=target_run,
        save_payload_path=save_payload_path,
    )
