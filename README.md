# wandb-xrpl-proof

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**Weave でトレースした W&B の実験ログを XRPL ブロックチェーンに刻み、改ざん不可能な証跡を残す Python SDK。**

```
@weave.op() → W&B Run → 正規化 → SHA-256 / Hash Chain → (IPFS) → XRPL Memo
```

---

## Why

LLM 評価・ML 実験のログは事後的に改ざんできてしまう。
XRPL のオンチェーンタイムスタンプと SHA-256 ハッシュを組み合わせることで、**第三者が独立して整合性を検証できる証跡**を残す。

---

## How it works

| Layer | 役割 |
|-------|------|
| **Weave** | `@weave.op()` で関数の入出力・実行時間を自動トレース |
| **W&B** | メトリクス・config・summary を記録 |
| **Canonicalization** | JSON キーをソート・空白除去・不安定フィールド除外 → 決定論的バイト列 |
| **SHA-256** | 正規化 JSON から 64 文字の commit_hash を生成 |
| **IPFS** *(optional)* | ペイロード全体をコンテンツアドレスで保存 |
| **XRPL** | `commit_hash` を AccountSet トランザクションのメモとしてオンチェーンに記録 |

オンチェーンに残るデータは **256 bytes 以内**の軽量なメモのみ。

---

## Quickstart

```bash
pip install -e ".[dev]"

# 環境変数を設定
cp .env.example .env
# .env を編集: XRPL_WALLET_SEED, WANDB_API_KEY
```

テストネット用 XRP の取得: https://faucet.altnet.rippletest.net/accounts

---

## 3 つの使い方

### 1. `@xrpl_anchor` — run 終了時に 1 回アンカー

`@weave.op()` と重ねると Weave trace ID・**入出力ハッシュ**も自動でペイロードに含まれる。

```python
import weave, wandb
from wandb_xrpl_proof import xrpl_anchor

weave.init("my-project")

@xrpl_anchor(
    summary_allowlist=["avg_f1"],  # 公開して良いメトリクスのみ
)
@weave.op()
def evaluate(prompt: str, response: str) -> dict:
    ...

with wandb.init(project="my-project"):
    evaluate(prompt="...", response="...")
    # → run.summary["xrpl_tx_hash"]    : XRPL tx hash
    # → run.summary["xrpl_commit_hash"] : ペイロードの SHA-256
    # → run.summary["weave_trace_url"]  : Weave UI の直リンク
```

**ペイロードに含まれるフィールド (`@weave.op()` と併用時):**

| フィールド | 内容 |
|---|---|
| `weave_call_id` | Weave trace ID |
| `weave_input_hash` | `sha256(canonicalize(call.inputs))` |
| `weave_output_hash` | `sha256(canonicalize(call.output))` |
| `summary` | `summary_allowlist` でフィルタした W&B summary |
| `config` | `config_allowlist` でフィルタした W&B config |

### 2. `IncrementalAnchor` — 途中で定期的にアンカー

`chunk_size` 行ごとに hash chain で XRPL にチェックポイントを刻む。
`evaluate_response.call()` で Weave Call を受け取り、入出力ハッシュ・tool call サマリもチェーンに含められる。

```python
import weave, wandb
from wandb_xrpl_proof import IncrementalAnchor, DEFAULT_PII_KEYS

weave.init("my-project")

@weave.op()
def evaluate(prompt: str, response: str) -> dict:
    ...

with wandb.init(project="my-project") as run:
    with IncrementalAnchor(
        run,
        chunk_size=1000,
        exclude_keys=DEFAULT_PII_KEYS,  # PII を除外してからハッシュ
    ) as anchor:
        for sample in dataset:
            result, weave_call = evaluate.call(
                prompt=sample["prompt"],
                response=sample["response"],
            )
            # metrics + Weave trace を同じチャンクにまとめる
            anchor.log(
                {"f1": result["f1"], "step": step},
                weave_call=weave_call,
            )
    # close() で残余チャンクを自動フラッシュ
    # run.summary["xrpl_checkpoint_txs"] に全 tx_hash リストが記録される
```

**各チャンク行に含まれるフィールド:**

| フィールド | 内容 |
|---|---|
| metrics | `f1`, `loss` など呼び出し元が渡した値 |
| `weave_call_id` | Weave trace ID |
| `weave_op_name` | op 名 |
| `weave_input_hash` | `sha256(canonicalize(inputs))` |
| `weave_output_hash` | `sha256(canonicalize(output))` |
| `weave_tool_calls` | 子 call の op 名リスト (tool calling 時) |

### 3. `anchor_run_end` — 明示的に 1 回アンカー

```python
from wandb_xrpl_proof import anchor_run_end

with wandb.init(project="my-project") as run:
    train(run)
    anchor_run_end(run, summary_allowlist=["loss"])
```

---

## 検証

```python
from wandb_xrpl_proof import verify_anchor, verify_chain

# 単一アンカーの検証
result = verify_anchor(tx_hash="<TX_HASH>", payload=payload)
print(result.verified)  # True = 改ざんなし

# IncrementalAnchor の chain 全体を検証
results = verify_chain(final_tx_hash="<LAST_TX_HASH>")
print(all(r.verified for r in results))
```

---

## PII 除外

```python
from wandb_xrpl_proof import DEFAULT_PII_KEYS

# DEFAULT_PII_KEYS: email, name, phone, ip_address, ssn, password, token ...
exclude = DEFAULT_PII_KEYS | {"internal_user_id"}

# canonicalize に直接渡す場合
from wandb_xrpl_proof import canonicalize
canonical = canonicalize(payload, exclude_keys=exclude)
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
├── canonicalize.py   # 正規化 + DEFAULT_PII_KEYS
├── hash.py           # SHA-256 + compute_chain_step
├── merkle.py         # Binary Merkle ツリー (history 用)
├── ipfs.py           # IPFS HTTP API (Kubo 互換)
├── xrpl_client.py    # XRPL AccountSet 送信・取得
├── anchor.py         # @xrpl_anchor, anchor_run_end, build_payload
├── incremental.py    # IncrementalAnchor + _extract_trace_fields
└── verify.py         # verify_anchor, verify_chain

demo/
├── run_demo.py       # IncrementalAnchor × Weave × XRPL デモ
└── verify_demo.py    # 証跡検証デモ
```

---

## Spec

実装仕様: [`documents/WandB_XRPL_Anchor_Spec_v0_2.md`](documents/WandB_XRPL_Anchor_Spec_v0_2.md)
