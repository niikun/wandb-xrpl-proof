"""
Basic usage example for wandb-xrpl-proof.

必要な環境変数:
    XRPL_WALLET_SEED: XRPL テストネットウォレットのシード
    WANDB_API_KEY: W&B API キー

テストネット XRP の取得:
    https://faucet.altnet.rippletest.net/accounts

実行方法:
    cp .env.example .env  # .env を編集して環境変数を設定
    pip install -e ".[dev]"
    python examples/basic_usage.py
"""

import os

import wandb

from wandb_xrpl_proof import canonicalize, compute_hash, verify_anchor, xrpl_anchor


# --- Example 1: per_op モード (各 op 呼び出し後にアンカリング) ---

@xrpl_anchor(
    include_summary=True,
    include_config=True,
    use_ipfs=False,          # True にする場合は IPFS デーモンが必要
    mode="per_op",
    summary_allowlist=["loss", "accuracy"],   # 公開して良い指標のみ
)
def train_epoch(model_name: str, epoch: int) -> dict:
    """模擬学習 op。実際は W&B run 内で呼び出す。"""
    # 模擬的な学習処理
    loss = 1.0 / (epoch + 1)
    accuracy = 1.0 - loss
    wandb.log({"loss": loss, "accuracy": accuracy, "epoch": epoch})
    return {"loss": loss, "accuracy": accuracy}


# --- Example 2: 手動アンカリング (デコレータなし) ---

def manual_anchor_example():
    """デコレータを使わず、手動でアンカリングする例。"""
    from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, submit_anchor

    run = wandb.run
    if run is None:
        print("No active W&B run.")
        return

    # ペイロード組み立て
    payload = {
        "schema_version": "wandb-xrpl-proof-0.2",
        "wandb_run_path": f"{run.entity}/{run.project}/{run.id}",
        "weave_op_name": "manual_op",
        "summary": {"loss": run.summary.get("loss")},
    }

    # 正規化 & ハッシュ
    canonical = canonicalize(payload)
    commit_hash = compute_hash(canonical)
    print(f"Commit hash: {commit_hash}")

    # XRPL 送信
    seed = os.environ.get("XRPL_WALLET_SEED")
    if not seed:
        print("XRPL_WALLET_SEED not set. Skipping XRPL submission.")
        return

    memo = {
        "schema_version": "a2a-weave-xrpl-0.2",
        "wandb_run_path": f"{run.entity}/{run.project}/{run.id}",
        "commit_hash": commit_hash,
    }

    tx_hash = submit_anchor(wallet_seed=seed, memo=memo)
    print(f"XRPL tx_hash: {tx_hash}")

    # 検証
    result = verify_anchor(tx_hash=tx_hash, payload=payload)
    print(f"Verified: {result.verified}")
    if not result.verified:
        print(f"Errors: {result.errors}")


# --- Example 3: Merkle ツリーを使ったアンカリング ---

def merkle_anchor_example():
    """W&B history の Merkle ツリーアンカリング例。"""
    from wandb_xrpl_proof import build_merkle_tree, split_history
    from wandb_xrpl_proof.xrpl_client import submit_anchor

    # 模擬 history データ (実際は run.history() で取得)
    history = [{"step": i, "loss": 1.0 / (i + 1)} for i in range(2500)]
    chunks = split_history(history, chunk_size=1000)
    merkle_result = build_merkle_tree(chunks)

    print(f"History root: {merkle_result['history_root']}")
    print(f"Chunk count: {merkle_result['chunk_count']}")


if __name__ == "__main__":
    # W&B run を開始 (WANDB_API_KEY が必要)
    run = wandb.init(
        project="wandb-xrpl-proof-example",
        config={"learning_rate": 0.001, "epochs": 5},
        mode="offline",  # オフラインモードで実行 (実際は "online")
    )

    print("=== Example 1: per_op アンカリング ===")
    for epoch in range(3):
        result = train_epoch(model_name="simple_model", epoch=epoch)
        print(f"Epoch {epoch}: loss={result['loss']:.4f}, accuracy={result['accuracy']:.4f}")

    print("\n=== Example 2: 手動アンカリング ===")
    manual_anchor_example()

    print("\n=== Example 3: Merkle ツリー ===")
    merkle_anchor_example()

    wandb.finish()
    print("\nDone.")
