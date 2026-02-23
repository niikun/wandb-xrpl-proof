"""
IncrementalAnchor — mid-run XRPL checkpointing via hash chain.

Implements Section 10 (incremental variant) of WandB_XRPL_Anchor_Spec_v0_2:

Hash chain:
    chain_hash_0 = SHA-256("" + chunk_hash_0)
    chain_hash_i = SHA-256(chain_hash_{i-1} + chunk_hash_i)

Changing chunk j invalidates chain_hash_j and all subsequent hashes (tamper-evident).
"""

import logging
import os
import threading
from typing import Any

import wandb

from wandb_xrpl_proof.anchor import SCHEMA_VERSION
from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_chain_step, compute_hash
from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, submit_anchor

logger = logging.getLogger(__name__)


def _extract_trace_fields(call: Any) -> dict:
    """
    Weave Call オブジェクトからアンカーに含めるフィールドを抽出する。

    以下を抽出する（取得失敗は黙って無視）:
    - weave_call_id   : call.id
    - weave_op_name   : call.func_name (短縮名) または call.op_name
    - weave_input_hash : sha256(canonicalize(call.inputs))
    - weave_output_hash: sha256(canonicalize(call.output))
    - weave_tool_calls : 子 call の func_name リスト (tool calling サマリ)

    Returns:
        フィールド辞書 (空の場合もある)
    """
    fields: dict = {}

    try:
        fields["weave_call_id"] = str(call.id)
    except Exception:
        pass

    try:
        # func_name は "weave:///..." ref を剥がした短縮名
        fields["weave_op_name"] = str(call.func_name)
    except Exception:
        try:
            fields["weave_op_name"] = str(call.op_name)
        except Exception:
            pass

    # 入力ハッシュ
    try:
        inputs = dict(call.inputs)
        fields["weave_input_hash"] = compute_hash(canonicalize(inputs))
    except Exception:
        pass

    # 出力ハッシュ
    try:
        output = call.output
        if output is not None:
            fields["weave_output_hash"] = compute_hash(canonicalize(output))
    except Exception:
        pass

    # tool call サマリ — 実行時に蓄積された子 call の func_name リスト
    try:
        tool_calls = [
            str(c.func_name)
            for c in call._children  # noqa: SLF001
            if hasattr(c, "func_name")
        ]
        if tool_calls:
            fields["weave_tool_calls"] = tool_calls
    except Exception:
        pass

    return fields


class IncrementalAnchor:
    """
    トレーニング途中に定期的に W&B ログを XRPL へアンカリングするクラス。

    Hash chain 方式:
        chain_hash_0 = SHA-256("" + chunk_hash_0)
        chain_hash_i = SHA-256(chain_hash_{i-1} + chunk_hash_i)

    chunk j を改ざんすると chain_hash_j 以降が全て変わるため tamper-evident。

    設計上の特徴:
    - run.history() を叩かない (API コールなし、同期遅延なし)
    - バックグラウンドスレッド不要 (record()/log() 呼び出し時に自動処理)
    - O(1)/ステップ (hash chain)

    Usage::

        with IncrementalAnchor(run, chunk_size=1000) as anchor:
            for step in range(10_000):
                metrics = {"loss": loss}
                anchor.log(metrics)        # wandb.log() + バッファに追加
        # __exit__ で close() → 残余チャンクをフラッシュして最終アンカー

    PII 除外::

        from wandb_xrpl_proof import DEFAULT_PII_KEYS
        with IncrementalAnchor(
            run,
            exclude_keys=DEFAULT_PII_KEYS | {"my_internal_field"},
        ) as anchor:
            ...

    Args:
        run: アクティブな wandb.Run (必須)
        chunk_size: チェックポイント 1 つあたりの行数 (デフォルト 1000)
        exclude_keys: 正規化時に除外するキー (PII・シークレット等)
        xrpl_seed_env: XRPL シードの環境変数名
        xrpl_node_env: XRPL ノード URL の環境変数名
    """

    def __init__(
        self,
        run: "wandb.sdk.wandb_run.Run",
        chunk_size: int = 1000,
        exclude_keys: set[str] | frozenset[str] | None = None,
        xrpl_seed_env: str = "XRPL_WALLET_SEED",
        xrpl_node_env: str = "XRPL_NODE_URL",
    ) -> None:
        if run is None:
            raise ValueError("IncrementalAnchor requires an active wandb.Run; got None")
        self._run = run
        self._chunk_size = chunk_size
        self._exclude_keys = exclude_keys
        self._xrpl_seed_env = xrpl_seed_env
        self._xrpl_node_env = xrpl_node_env

        self._buffer: list[dict] = []
        self._chain_hash: str | None = None
        self._prev_tx_hash: str | None = None
        self._seq: int = 0
        self._tx_hashes: list[str] = []
        self._chunk_hashes: list[str] = []
        self._closed: bool = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "IncrementalAnchor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, data: dict, weave_call: Any = None) -> str | None:
        """
        1 ステップ分のデータをバッファに追加する。

        chunk_size 行溜まると自動的に hash を計算し XRPL へ送信する。
        wandb.log() は呼ばない（別途呼ぶ場合に使用）。

        Args:
            data: ログするメトリクス辞書
            weave_call: Weave Call オブジェクト (省略可)。渡すと入出力ハッシュ・
                        op 名・tool call サマリもチャンクハッシュに含まれる。

        Returns:
            チェックポイント送信時の tx_hash、未送信時は None
        """
        row = dict(data)
        if weave_call is not None:
            row.update(_extract_trace_fields(weave_call))

        with self._lock:
            if self._closed:
                return None
            self._buffer.append(row)
            if len(self._buffer) < self._chunk_size:
                return None
            # チャンク完成 — ロック保護下でスナップショットを取る
            chunk = self._buffer[: self._chunk_size]
            self._buffer = self._buffer[self._chunk_size :]
            prev_chain_hash = self._chain_hash
            prev_tx_hash = self._prev_tx_hash
            current_seq = self._seq

        # ロック外で計算・送信（ネットワーク IO）
        return self._process_chunk(chunk, prev_chain_hash, prev_tx_hash, current_seq)

    def log(self, data: dict, weave_call: Any = None, **kwargs) -> str | None:
        """
        wandb.log() を呼んだ後、バッファにも追加する。

        通常の wandb.log() の代わりに使用する。

        Args:
            data: ログするメトリクス辞書
            weave_call: Weave Call オブジェクト (省略可)。渡すと入出力ハッシュ・
                        op 名・tool call サマリもチャンクハッシュに含まれる。
            **kwargs: wandb.log() に転送するキーワード引数 (step= など)

        Returns:
            チェックポイント送信時の tx_hash、未送信時は None
        """
        wandb.log(data, **kwargs)
        return self.record(data, weave_call=weave_call)

    def close(self) -> str | None:
        """
        残余チャンク（chunk_size 未満の行）をフラッシュして最終アンカーを送信する。

        __exit__ から自動的に呼ばれる。冪等（2 回呼んでも安全）。

        Returns:
            最終チェックポイントの tx_hash、残余なければ None
        """
        with self._lock:
            if self._closed:
                return None
            self._closed = True
            remaining = list(self._buffer)
            self._buffer.clear()
            prev_chain_hash = self._chain_hash
            prev_tx_hash = self._prev_tx_hash
            current_seq = self._seq

        tx = None
        if remaining:
            tx = self._process_chunk(remaining, prev_chain_hash, prev_tx_hash, current_seq)

        try:
            self._run.summary["xrpl_checkpoint_txs"] = list(self._tx_hashes)
        except Exception:
            pass

        return tx

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def tx_hashes(self) -> list[str]:
        """送信済み全チェックポイントの tx_hash リスト（時系列順）。"""
        with self._lock:
            return list(self._tx_hashes)

    @property
    def chunk_hashes(self) -> list[str]:
        """送信済み全チェックポイントのチャンクデータ SHA-256 リスト（時系列順）。

        verify_chain(final_tx_hash, chunk_hashes=anchor.chunk_hashes) に渡すことで
        hash chain を再計算してオンチェーン値と照合できる。
        """
        with self._lock:
            return list(self._chunk_hashes)

    @property
    def seq(self) -> int:
        """次に送信されるチェックポイントの seq 番号。"""
        with self._lock:
            return self._seq

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_chunk(
        self,
        chunk: list[dict],
        prev_chain_hash: str | None,
        prev_tx_hash: str | None,
        current_seq: int,
    ) -> str | None:
        """チャンクをハッシュ化して XRPL へ送信し、成功時に状態を更新する。"""
        chunk_hash = compute_hash(canonicalize(chunk, exclude_keys=self._exclude_keys))
        new_chain_hash = compute_chain_step(prev_chain_hash, chunk_hash)

        tx = self._submit_checkpoint(new_chain_hash, current_seq, prev_tx_hash)
        if tx:
            with self._lock:
                self._chain_hash = new_chain_hash
                self._prev_tx_hash = tx
                self._seq += 1
                self._tx_hashes.append(tx)
                self._chunk_hashes.append(chunk_hash)
            try:
                self._run.summary["xrpl_checkpoint_txs"] = list(self._tx_hashes)
            except Exception:
                pass
        return tx

    def _submit_checkpoint(
        self,
        chain_hash: str,
        seq: int,
        prev_tx_hash: str | None,
    ) -> str | None:
        """XRPL AccountSet トランザクションを送信する。失敗は None を返す（例外伝播なし）。"""
        run = self._run
        try:
            memo: dict = {
                "schema_version": SCHEMA_VERSION,
                "commit_hash": chain_hash,
                "seq": seq,
            }
            if seq == 0:
                memo["wandb_run_path"] = f"{run.entity}/{run.project}/{run.id}"
            else:
                if prev_tx_hash is None:
                    logger.error(
                        "IncrementalAnchor: seq=%d だが prev_tx_hash が None", seq
                    )
                    return None
                memo["prev"] = prev_tx_hash

            seed = os.environ.get(self._xrpl_seed_env)
            if not seed:
                logger.error(
                    "IncrementalAnchor: %s が未設定のため seq=%d をスキップ",
                    self._xrpl_seed_env,
                    seq,
                )
                try:
                    run.summary["xrpl_anchor_error"] = f"{self._xrpl_seed_env} not configured"
                except Exception:
                    pass
                return None

            node_url = os.environ.get(self._xrpl_node_env) or XRPL_TESTNET_URL
            tx = submit_anchor(wallet_seed=seed, memo=memo, network_url=node_url)
            logger.info(
                "IncrementalAnchor: seq=%d tx=%s... chain=%s...",
                seq,
                tx[:8],
                chain_hash[:8],
            )
            return tx

        except Exception as exc:
            logger.error(
                "IncrementalAnchor: seq=%d の送信に失敗: %s", seq, exc, exc_info=True
            )
            try:
                run.summary["xrpl_anchor_error"] = f"checkpoint seq={seq}: {exc}"
            except Exception:
                pass
            return None
