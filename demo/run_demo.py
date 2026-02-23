"""
Demo: Weave x XRPL Anchoring

@weave.op() でトレースした LLM 評価ログを XRPL ブロックチェーンに刻むデモ。
IncrementalAnchor を使い、metrics + Weave trace 入出力ハッシュを
一本の hash chain で XRPL に記録する。

必要な環境変数 (../.env に記述):
    XRPL_WALLET_SEED : XRPL テストネットウォレットのシード
    WANDB_API_KEY    : W&B API キー

実行:
    python run_demo.py                     # デフォルト: 5 samples, chunk_size=3
    python run_demo.py --samples 12 --chunk-size 5
"""

import argparse
import itertools
import json
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


def _generate_samples(n: int) -> list[dict]:
    """EVAL_DATASET を循環させて n 件のサンプルを生成する。"""
    return list(itertools.islice(itertools.cycle(EVAL_DATASET), n))


# ---------------------------------------------------------------------------
# 表示ユーティリティ
# ---------------------------------------------------------------------------

W = 62


def _sep(char: str = "=") -> None:
    print(char * W)


def _print_header(run: "wandb.sdk.wandb_run.Run", weave_project_url: str) -> None:
    _sep()
    print("  wandb-xrpl-proof  |  Weave x XRPL Anchoring Demo")
    _sep()
    print()
    print("  Pipeline:")
    print("    @weave.op()  -->  metrics + trace  -->  hash chain  -->  XRPL")
    print()
    print(f"  W&B Run  : https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}")
    print(f"  Weave UI : {weave_project_url}")
    print()


def _print_loop_header(total: int, chunk_size: int) -> None:
    _sep("-")
    print(f"  評価開始: {total} samples, chunk_size={chunk_size}")
    print(f"  各チェックポイントに含まれるデータ:")
    print(f"    metrics : f1 / precision / recall / step")
    print(f"    Weave   : call_id / input_hash / output_hash / op_name")
    _sep("-")


def _print_step(
    idx: int,
    total: int,
    sample: dict,
    result: dict,
    weave_ui: str,
) -> None:
    prompt_short = sample["prompt"]
    if len(prompt_short) > 48:
        prompt_short = prompt_short[:45] + "..."
    print()
    print(f"  [{idx + 1}/{total}] \"{prompt_short}\"")
    print(f"        F1={result['f1']:.4f}  Prec={result['precision']:.4f}  Recall={result['recall']:.4f}")
    print(f"        Weave : {weave_ui}")


def _print_checkpoint(seq: int, tx: str, prev_tx: str | None, is_final: bool = False) -> None:
    kind = "genesis" if seq == 0 else "chained"
    label = " (final)" if is_final else ""
    print()
    print(f"  +-- CHECKPOINT #{seq} [{kind}]{label} " + "-" * max(0, W - 22 - len(kind) - len(label)) + "+")
    if seq == 0:
        print(f"  |  wandb_run_path をオンチェーンに刻みました")
    elif prev_tx:
        print(f"  |  prev : {prev_tx[:24]}...")
    print(f"  |  XRPL : https://testnet.xrpl.org/transactions/{tx}")
    print(f"  +" + "-" * (W - 2) + "+")


def _print_summary(
    run: "wandb.sdk.wandb_run.Run",
    weave_project_url: str,
    checkpoint_txs: list[str],
    proof_path: Path,
) -> None:
    print()
    _sep()
    print("  証跡の記録が完了しました")
    _sep()
    print()
    print(f"  W&B Run      : https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}")
    print(f"  Weave UI     : {weave_project_url}")
    print(f"  Checkpoints  : {len(checkpoint_txs)} tx")
    print(f"  Proof file   : {proof_path}")
    print()

    if checkpoint_txs:
        print("  Hash chain (oldest --> newest):")
        for j, tx in enumerate(checkpoint_txs):
            kind = "genesis" if j == 0 else "chained"
            suffix = "  <-- final" if j == len(checkpoint_txs) - 1 else ""
            print(f"    [{j}] {kind:7}  https://testnet.xrpl.org/transactions/{tx}{suffix}")
            if j < len(checkpoint_txs) - 1:
                print(f"          |")
        print()
        print(f"  検証コマンド:")
        print(f"    python verify_demo.py --chain --proof {proof_path.name}")
        print()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="wandb-xrpl-proof デモ")
    parser.add_argument(
        "--samples", "-n",
        type=int, default=5, metavar="N",
        help="評価サンプル数 (デフォルト: 5, EVAL_DATASET を循環)",
    )
    parser.add_argument(
        "--chunk-size", "-c",
        type=int, default=3, metavar="K",
        help="チェックポイント 1 つあたりのステップ数 (デフォルト: 3)",
    )
    args = parser.parse_args()

    if not os.environ.get("XRPL_WALLET_SEED"):
        print("[ERROR] XRPL_WALLET_SEED が設定されていません。../.env を確認してください。")
        sys.exit(1)

    samples = _generate_samples(args.samples)

    # Step 1: 初期化
    weave.init("wandb-xrpl-proof-demo")
    run = wandb.init(
        project="wandb-xrpl-proof-demo",
        config={
            "model": "demo-llm-v1",
            "evaluator": "f1-overlap",
            "dataset_size": args.samples,
            "chunk_size": args.chunk_size,
        },
        tags=["xrpl-anchor", "weave", "incremental"],
    )

    weave_project_url = f"https://wandb.ai/{run.entity}/weave/{run.project}"
    _print_header(run, weave_project_url)

    # Step 2: 評価ループ
    _print_loop_header(len(samples), args.chunk_size)

    anchor = IncrementalAnchor(run, chunk_size=args.chunk_size)

    for i, sample in enumerate(samples):
        result, weave_call = evaluate_response.call(
            prompt=sample["prompt"],
            response=sample["response"],
            reference=sample["reference"],
        )

        try:
            weave_ui = weave_call.ui_url
        except Exception:
            weave_ui = weave_project_url

        _print_step(i, len(samples), sample, result, weave_ui)

        tx = anchor.log(
            {
                "step": i,
                "f1": result["f1"],
                "precision": result["precision"],
                "recall": result["recall"],
            },
            weave_call=weave_call,
        )

        if tx:
            seq = anchor.seq - 1
            prev_tx = anchor.tx_hashes[-2] if len(anchor.tx_hashes) >= 2 else None
            _print_checkpoint(seq, tx, prev_tx, is_final=False)

    # 残余チャンクをフラッシュして最終アンカー
    final_tx = anchor.close()
    if final_tx:
        seq = anchor.seq - 1
        prev_tx = anchor.tx_hashes[-2] if len(anchor.tx_hashes) >= 2 else None
        _print_checkpoint(seq, final_tx, prev_tx, is_final=True)

    # proof ファイル保存 (verify_demo.py --chain で使用)
    checkpoint_txs = run.summary.get("xrpl_checkpoint_txs", [])
    proof = {
        "final_tx_hash": checkpoint_txs[-1] if checkpoint_txs else None,
        "tx_hashes": checkpoint_txs,
        "chunk_hashes": anchor.chunk_hashes,
        "samples": args.samples,
        "chunk_size": args.chunk_size,
        "wandb_run_url": f"https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}",
    }
    proof_path = Path(__file__).parent / "demo_proof.json"
    proof_path.write_text(json.dumps(proof, indent=2))

    _print_summary(run, weave_project_url, checkpoint_txs, proof_path)

    wandb.finish()


if __name__ == "__main__":
    main()
