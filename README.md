# wandb-xrpl-proof

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**Weave でトレースした W&B の実験ログを、IPFS に保存し、その証跡ハッシュを XRPL ブロックチェーンに刻むための Python SDK。**

```
@weave.op() → W&B Run → 正規化 → SHA-256 → IPFS → XRPL Memo
```

---

## Why

LLM 評価・ML 実験のログは事後的に改ざんできてしまう。
XRPL のオンチェーンタイムスタンプと IPFS のコンテンツアドレッシングを組み合わせることで、**第三者が独立して整合性を検証できる証跡**を残す。

---

## How it works

| Layer | 役割 |
|-------|------|
| **Weave** | `@weave.op()` で関数の入出力・実行時間を自動トレース |
| **W&B** | メトリクス・config・summary を記録 |
| **Canonicalization** | JSON キーをソート・空白除去・不安定フィールド除外 → 決定論的バイト列 |
| **SHA-256** | 正規化 JSON から 64 文字の commit_hash を生成 |
| **IPFS** | ペイロード全体 (summary / config など) をコンテンツアドレスで保存 |
| **XRPL** | `{ commit_hash, cid, wandb_run_path }` を AccountSet トランザクションのメモとしてオンチェーンに記録 |

オンチェーンに残るデータは **256 bytes 以内**の軽量なメモのみ。フルペイロードは IPFS が保持する。

```json
{
  "schema_version": "wandb-xrpl-proof-0.2",
  "wandb_run_path": "entity/project/run_id",
  "commit_hash": "<sha256-hex>",
  "cid": "<ipfs-cid>"
}
```

---

## Quickstart

```bash
pip install -e ".[dev]"

# IPFS デーモンを起動 (Kubo)
ipfs init && ipfs daemon &

# 環境変数を設定
cp .env.example .env
# .env を編集: XRPL_WALLET_SEED, WANDB_API_KEY
```

テストネット用 XRP の取得: https://faucet.altnet.rippletest.net/accounts

### デモを実行する

```bash
# Weave ログ → IPFS → XRPL アンカリング
python demo/run_demo.py

# 証跡の整合性検証
python demo/verify_demo.py --tx <TX_HASH> --cid <CID>
```

### `@xrpl_anchor` デコレータを使う

```python
import weave
import wandb
from wandb_xrpl_proof import xrpl_anchor

weave.init("my-project")

@weave.op()
@xrpl_anchor(
    include_summary=True,
    include_config=True,
    use_ipfs=True,
    summary_allowlist=["avg_f1", "avg_loss"],  # 公開して良いメトリクスのみ
)
def evaluate(prompt: str, response: str) -> dict:
    ...

with wandb.init(project="my-project"):
    evaluate(prompt="...", response="...")
    # → 実行後に自動で IPFS → XRPL にアンカリングされる
    # → run.summary["xrpl_tx_hash"] に tx_hash が記録される
```

### 手動で検証する

```python
from wandb_xrpl_proof import verify_anchor

result = verify_anchor(tx_hash="<TX_HASH>", payload=payload)
print(result.verified)  # True = 改ざんなし
```

---

## Tests

```bash
# ユニットテスト (外部接続不要)
pytest tests/unit/ -v

# 統合テスト (XRPL テストネット + W&B、.env が必要)
pytest tests/integration/ -v
```

---

## Project Structure

```
wandb_xrpl_proof/
├── canonicalize.py   # 正規化 (キーソート・不安定フィールド除外)
├── hash.py           # SHA-256
├── merkle.py         # Binary Merkle ツリー (history 用)
├── ipfs.py           # IPFS HTTP API (Kubo 互換)
├── xrpl_client.py    # XRPL AccountSet 送信・取得
├── anchor.py         # @xrpl_anchor デコレータ
└── verify.py         # 整合性検証

demo/
├── run_demo.py       # Weave × IPFS × XRPL デモ
└── verify_demo.py    # 証跡検証デモ
```

---

## Spec

実装仕様: [`documents/WandB_XRPL_Anchor_Spec_v0_2.md`](documents/WandB_XRPL_Anchor_Spec_v0_2.md)
