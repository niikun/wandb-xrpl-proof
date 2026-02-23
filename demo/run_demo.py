"""
Demo: Weave × IPFS × XRPL Anchoring

Weave でトレースした LLM 評価ログを IPFS に保存し、
その証跡ハッシュを XRPL ブロックチェーンに刻むデモ。

必要な環境変数 (../.env に記述):
    XRPL_WALLET_SEED : XRPL テストネットウォレットのシード
    WANDB_API_KEY    : W&B API キー
    IPFS_API_URL     : IPFS デーモンの API URL (省略時: http://127.0.0.1:5001)

実行:
    python run_demo.py
"""

import json
import os
import sys
from pathlib import Path

# リポジトリルートの .env を読み込む
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

import weave
import wandb

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.ipfs import upload_to_ipfs
from wandb_xrpl_proof.xrpl_client import submit_anchor, XRPL_TESTNET_URL


# ---------------------------------------------------------------------------
# Step 1: Weave でトレースする評価関数を定義
# ---------------------------------------------------------------------------

@weave.op()
def evaluate_response(prompt: str, response: str, reference: str) -> dict:
    """
    LLM の応答品質を評価する関数。
    @weave.op() により、入力・出力・実行時間が Weave に自動トレースされる。
    """
    # 簡易スコアリング: reference との共通単語の割合
    resp_words = set(response.lower().split())
    ref_words = set(reference.lower().split())
    overlap = len(resp_words & ref_words)
    precision = overlap / len(resp_words) if resp_words else 0.0
    recall = overlap / len(ref_words) if ref_words else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


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
]


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main() -> None:
    # 環境変数チェック
    seed = os.environ.get("XRPL_WALLET_SEED")
    if not seed:
        print("[ERROR] XRPL_WALLET_SEED が設定されていません。../.env を確認してください。")
        sys.exit(1)

    ipfs_url = os.environ.get("IPFS_API_URL", "http://127.0.0.1:5001")
    xrpl_node = os.environ.get("XRPL_NODE_URL", XRPL_TESTNET_URL)

    # ---------------------------------------------------------------------------
    # Step 1: W&B + Weave を初期化
    # ---------------------------------------------------------------------------
    _section("Step 1: W&B + Weave を初期化")

    # Weave を初期化 (W&B プロジェクトと連携)
    weave.init("wandb-xrpl-proof-demo")

    run = wandb.init(
        project="wandb-xrpl-proof-demo",
        config={
            "model": "demo-llm-v1",
            "evaluator": "f1-overlap",
            "dataset_size": len(EVAL_DATASET),
        },
        tags=["xrpl-anchor", "weave", "ipfs"],
    )
    print(f"W&B Run: https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}")
    print(f"Weave UI: https://wandb.ai/{run.entity}/weave/{run.project}")

    # ---------------------------------------------------------------------------
    # Step 2: Weave でトレースしながら評価を実行
    # ---------------------------------------------------------------------------
    _section("Step 2: @weave.op() でトレースしながら評価を実行")

    scores = []
    for i, sample in enumerate(EVAL_DATASET):
        print(f"\n  [{i+1}/{len(EVAL_DATASET)}] prompt: {sample['prompt'][:50]}...")
        result = evaluate_response(
            prompt=sample["prompt"],
            response=sample["response"],
            reference=sample["reference"],
        )
        scores.append(result)
        wandb.log({
            "eval/f1": result["f1"],
            "eval/precision": result["precision"],
            "eval/recall": result["recall"],
            "step": i,
        })
        print(f"           → F1={result['f1']:.4f}  P={result['precision']:.4f}  R={result['recall']:.4f}")

    # 最終サマリを W&B に記録
    avg_f1 = sum(s["f1"] for s in scores) / len(scores)
    avg_precision = sum(s["precision"] for s in scores) / len(scores)
    avg_recall = sum(s["recall"] for s in scores) / len(scores)
    run.summary["avg_f1"] = round(avg_f1, 4)
    run.summary["avg_precision"] = round(avg_precision, 4)
    run.summary["avg_recall"] = round(avg_recall, 4)
    run.summary["num_samples"] = len(EVAL_DATASET)
    print(f"\n  平均スコア: F1={avg_f1:.4f}  P={avg_precision:.4f}  R={avg_recall:.4f}")

    # ---------------------------------------------------------------------------
    # Step 3: ペイロード組み立て & 正規化 & SHA-256 ハッシュ
    # ---------------------------------------------------------------------------
    _section("Step 3: ペイロード正規化 & SHA-256 ハッシュ計算")

    payload = {
        "schema_version": "wandb-xrpl-proof-0.2",
        "wandb_run_path": f"{run.entity}/{run.project}/{run.id}",
        "weave_op_name": "evaluate_response",
        "summary": {
            "avg_f1": run.summary["avg_f1"],
            "avg_precision": run.summary["avg_precision"],
            "avg_recall": run.summary["avg_recall"],
            "num_samples": run.summary["num_samples"],
        },
        "config": {
            "model": run.config["model"],
            "evaluator": run.config["evaluator"],
            "dataset_size": run.config["dataset_size"],
        },
    }

    canonical = canonicalize(payload)
    commit_hash = compute_hash(canonical)

    print(f"  ペイロード:\n{json.dumps(payload, indent=4, ensure_ascii=False)}")
    print(f"\n  正規化 JSON:\n  {canonical}")
    print(f"\n  SHA-256 commit_hash:\n  {commit_hash}")

    # ---------------------------------------------------------------------------
    # Step 4: IPFS にペイロードをアップロード
    # ---------------------------------------------------------------------------
    _section("Step 4: IPFS にペイロードをアップロード")

    print(f"  IPFS API: {ipfs_url}")
    try:
        cid = upload_to_ipfs(payload, api_url=ipfs_url)
        print(f"  CID: {cid}")
        print(f"  ゲートウェイ確認: http://127.0.0.1:8080/ipfs/{cid}")
        run.summary["ipfs_cid"] = cid
    except Exception as e:
        print(f"  [ERROR] IPFS アップロード失敗: {e}")
        print("  IPFS デーモンが起動しているか確認してください: ipfs daemon &")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Step 5: XRPL に commit_hash + CID をアンカリング
    # ---------------------------------------------------------------------------
    _section("Step 5: XRPL テストネットにアンカリング")

    memo = {
        "schema_version": "wandb-xrpl-proof-0.2",
        "wandb_run_path": f"{run.entity}/{run.project}/{run.id}",
        "commit_hash": commit_hash,
        "cid": cid,
    }
    memo_bytes = json.dumps(memo, sort_keys=True, separators=(",", ":")).encode()
    print(f"  XRPL Node: {xrpl_node}")
    print(f"  Memo ({len(memo_bytes)} bytes / 256 bytes 上限):")
    print(f"  {json.dumps(memo, indent=4)}")

    tx_hash = submit_anchor(wallet_seed=seed, memo=memo, network_url=xrpl_node)

    run.summary["xrpl_tx_hash"] = tx_hash
    run.summary["xrpl_commit_hash"] = commit_hash

    print(f"\n  tx_hash: {tx_hash}")
    print(f"  XRPL Explorer: https://testnet.xrpl.org/transactions/{tx_hash}")

    # ---------------------------------------------------------------------------
    # 完了サマリ
    # ---------------------------------------------------------------------------
    _section("完了: 証跡の記録が完了しました")

    print(f"""
  W&B Run  : https://wandb.ai/{run.entity}/{run.project}/runs/{run.id}
  IPFS CID : {cid}
  XRPL tx  : {tx_hash}

  検証コマンド:
    python verify_demo.py --tx {tx_hash} --cid {cid}
""")

    wandb.finish()


if __name__ == "__main__":
    main()
