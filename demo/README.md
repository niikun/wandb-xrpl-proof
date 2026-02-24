# Demo: Weave × IPFS × XRPL Anchoring

Weave でトレースした LLM 評価ログを、IPFS に保存し、その証跡を XRPL ブロックチェーンに刻むデモです。

```
Weave (トレース) → W&B Run (メトリクス) → IPFS (ペイロード保存) → XRPL (ハッシュをオンチェーンに)
```

---

## What this demo does

| Step | What happens |
|------|-------------|
| 1 | `@weave.op()` で LLM 評価関数をトレース。入出力・スコアが Weave に記録される |
| 2 | 評価結果 (スコア統計) を W&B に `wandb.log()` でメトリクスとして記録 |
| 3 | W&B run の summary / config を正規化 JSON に変換し SHA-256 ハッシュを計算 |
| 4 | ペイロード全体を IPFS にアップロード → CID 取得 |
| 5 | `{ commit_hash, cid, wandb_run_path }` を XRPL に AccountSet トランザクションとして送信 |
| 6 | 任意のタイミングで tx_hash を使って整合性を検証 |

**オンチェーンに残るデータ (256 bytes 以内):**
```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<sha256>",
  "cid": "<ipfs-cid>"
}
```

**オフチェーン (IPFS) に残るデータ:**
- 評価スコアの統計 (summary)
- 実験設定 (config)
- Weave op 名・スキーマバージョン

---

## Prerequisites

```bash
# 依存パッケージのインストール
pip install -e ".[dev]"

# IPFS デーモンの起動 (Kubo)
ipfs init        # 初回のみ
ipfs daemon &    # バックグラウンドで起動
```

環境変数を `.env` に設定してください:

```bash
cp ../.env.example ../.env
# 以下を編集:
XRPL_WALLET_SEED=sXxxYourTestnetSeedHere   # https://faucet.altnet.rippletest.net/accounts
WANDB_API_KEY=your_wandb_api_key
```

---

## Run

```bash
cd demo

# デモ実行 (Weave ログ → IPFS → XRPL)
python run_demo.py

# 検証 (tx_hash を指定して整合性確認)
python verify_demo.py --tx <TX_HASH> --cid <CID>
```

---

## Files

| File | Description |
|------|-------------|
| `weave_demo.py` | 最小構成デモ: `@weave.op()` + `IncrementalAnchor` → XRPL チェックポイント → 検証 |
| `run_demo.py` | フルデモ: Weave トレース → IPFS → XRPL アンカリング |
| `verify_demo.py` | 検証: `--chain --proof weave_proof.json` または `--tx TX_HASH` |
| `weave_proof.json` | `weave_demo.py` が生成する proof ファイル（検証に使用） |

---

## Weave Demo（最小構成）

```bash
# 実行 → XRPL URL を表示 + weave_proof.json を保存
python weave_demo.py

# 検証 → hash chain を再計算して改ざんを確認
python verify_demo.py --chain --proof weave_proof.json
```
