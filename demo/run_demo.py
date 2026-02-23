"""
Demo: Weave × XRPL Anchoring

@weave.op() でトレースした LLM 評価ログを XRPL ブロックチェーンに刻むデモ。
IncrementalAnchor を使い、metrics + Weave trace 入出力ハッシュ + tool call サマリを
一本の hash chain で XRPL に記録する。

必要な環境変数 (../.env に記述):
    XRPL_WALLET_SEED : XRPL テストネットウォレットのシード
    WANDB_API_KEY    : W&B API キー

実行:
    python run_demo.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

import weave
import wandb

from wandb_xrpl_proof import IncrementalAnchor


# ---------------------------------------------------------------------------
# 評価関数 — @weave.op() でトレース
# ---------------------------------------------------------------------------

@weave.op()
def evaluate_response(prompt: str, response: str, reference: str) -> dict:
    """
    LLM の応答品質を評価する。
    @weave.op() により入力・出力・実行時間が Weave に自動トレースされる。
    """
    resp_words = set(response.lower().split())
    ref_words = set(reference.lower().split())
    overlap = len(resp_words & ref_words)
    precision = overlap / len(resp_words) if resp_words else 0.0
    recall = overlap / len(ref_words) if ref_words else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


# ---------------------------------------------------------------------------
# デモ用評価データセット
# ---------------------------------------------------------------------------

EVAL_DATASET = [
    {
        "prompt": "What is the capital of Japan?",
        "response": "The capital of Japan is Tokyo, a major metropolitan city.",
        "reference": "Tokyo is the capital of Japan.",
    },
    {
        "prompt": "Explain machine learning in one sentence.",
        "response": "Machine learning is a method where computers learn patterns from data automatically.",
        "reference": "Machine learning enables computers to learn from data without explicit programming.",
    },
    {
        "prompt": "What is XRPL?",
        "response": "XRPL is the XRP Ledger, a decentralized blockchain for fast and low-cost transactions.",
        "reference": "XRPL stands for XRP Ledger, a public blockchain designed for fast, low-cost payments.",
    },
    {
        "prompt": "What is Weave?",
        "response": "Weave is an observability tool for AI applications that tracks inputs and outputs.",
        "reference": "Weave by W&B provides LLM tracing, evaluation, and monitoring for AI apps.",
    },
    {
        "prompt": "Define hash chain.",
        "response": "A hash chain links each entry to the previous one by hashing them together.",
        "reference": "A hash chain is a sequence of hashes where each hash includes the previous hash.",
    },
]


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main() -> None:
    if not os.environ.get("XRPL_WALLET_SEED"):
        print("[ERROR] XRPL_WALLET_SEED が設定されていません。../.env を確認してください。")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Step 1: W&B + Weave を初期化
    # ---------------------------------------------------------------------------
    _section("Step 1: W&B + Weave を初期化")

    weave.init("wandb-xrpl-proof-demo")
    run = wandb.init(
        project="wandb-xrpl-proof-demo",
        config={"model": "demo-llm-v1", "evaluator": "f1-overlap",
                "dataset_size": len(EVAL_DATASET)},
        tags=["xrpl-anchor", "weave", "incremental"],
    )

    weave_project_url = f"https://wandb.ai/{run.entity}/weave/{run.project}"
    print(f"  W&B Run  : https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}")
    print(f"  Weave UI : {weave_project_url}")

    # ---------------------------------------------------------------------------
    # Step 2: IncrementalAnchor で評価ループ実行
    #   evaluate_response.call() で Weave Call オブジェクトを取得し、
    #   anchor.log(metrics, weave_call=call) で metrics + trace を同じチャンクへ
    # ---------------------------------------------------------------------------
    _section("Step 2: IncrementalAnchor + Weave trace でアンカーしながら評価")

    print("  各チャンク行に含まれるフィールド:")
    print("    metrics           : f1, precision, recall, step")
    print("    weave_call_id     : Weave trace ID")
    print("    weave_op_name     : 評価 op 名")
    print("    weave_input_hash  : sha256(canonicalize(inputs))")
    print("    weave_output_hash : sha256(canonicalize(output))")

    with IncrementalAnchor(run, chunk_size=3) as anchor:
        for i, sample in enumerate(EVAL_DATASET):
            # evaluate_response.call() → (output, Call) を返す
            result, weave_call = evaluate_response.call(
                prompt=sample["prompt"],
                response=sample["response"],
                reference=sample["reference"],
            )

            # metrics + Weave Call をまとめてバッファへ (chunk_size=3 で自動送信)
            tx = anchor.log(
                {"step": i, "f1": result["f1"],
                 "precision": result["precision"], "recall": result["recall"]},
                weave_call=weave_call,
            )

            try:
                weave_ui = weave_call.ui_url
            except Exception:
                weave_ui = weave_project_url

            print(f"\n  [{i+1}/{len(EVAL_DATASET)}] F1={result['f1']:.4f}")
            print(f"    Weave trace : {weave_ui}")
            if tx:
                print(f"    XRPL tx     : https://testnet.xrpl.org/transactions/{tx}")

    # ---------------------------------------------------------------------------
    # 完了サマリ
    # ---------------------------------------------------------------------------
    _section("完了: 証跡の記録が完了しました")

    checkpoint_txs = run.summary.get("xrpl_checkpoint_txs", [])
    print(f"""
  W&B Run      : https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}
  Weave UI     : {weave_project_url}
  Checkpoints  : {len(checkpoint_txs)} tx
""")

    for j, tx in enumerate(checkpoint_txs):
        print(f"  [{j}] https://testnet.xrpl.org/transactions/{tx}")

    if checkpoint_txs:
        print(f"""
  検証コマンド:
    python verify_demo.py --tx {checkpoint_txs[-1]}
""")

    wandb.finish()


if __name__ == "__main__":
    main()
