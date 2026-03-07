#!/usr/bin/env python3
"""
Hackathon Demo ②  — 改ざん検知デモ

weave_proof.json を読み込み、chunk_hash を書き換えて
XRPL が改ざんを検出することを示す。

  - XRPL 上の commit_hash は変更不可（過去に刻まれた真実）
  - ローカルの chunk_hash を変えると hash chain が一致しなくなる
  - → 改ざんを即座に検出

実行 (Demo ① の後に):
    python demo/hackathon_tamper.py
"""

import json
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from wandb_xrpl_proof.verify import verify_chain

# ── ANSI ─────────────────────────────────────────────────────────────────────
G    = "\033[92m"
R    = "\033[91m"
Y    = "\033[93m"
C    = "\033[96m"
B    = "\033[1m"
DIM  = "\033[2m"
RESET= "\033[0m"
W    = 66

def sep(c="─"): print(f"  {c * (W - 2)}")
def blank():    print()

def header():
    blank()
    print(f"  {R}{B}{'═' * (W - 2)}{RESET}")
    title = "wandb-xrpl-proof  ×  TAMPERING DETECTION  |  Demo ②"
    pad = (W - 2 - len(title)) // 2
    print(f"  {R}{B}{'║'}{' ' * pad}{title}{' ' * (W - 2 - pad - len(title) - 1)}{'║'}{RESET}")
    print(f"  {R}{B}{'═' * (W - 2)}{RESET}")
    blank()

def step_header(n, total, text):
    blank()
    print(f"  {C}{B}[STEP {n}/{total}]{RESET}  {B}{text}{RESET}")
    sep()

def ok(label, detail=""):
    detail_str = f"  {DIM}{detail}{RESET}" if detail else ""
    print(f"  {G}✓{RESET}  {label}{detail_str}")

def warn(label, detail=""):
    detail_str = f"  {detail}" if detail else ""
    print(f"  {Y}⚠{RESET}  {Y}{label}{RESET}{detail_str}")

def attack(label):
    print(f"  {R}{B}[ATTACKER]{RESET}  {R}{label}{RESET}")

def result_line(verified, label, detail=""):
    mark = f"{G}✓{RESET}" if verified else f"{R}✗{RESET}"
    print(f"  {mark}  {label}  {DIM}{detail}{RESET}")

def final_banner_fail(n_fail, n_total):
    blank()
    print(f"  {R}{B}{'═' * (W - 2)}{RESET}")
    msg1 = f"🚨  TAMPERING DETECTED  —  {n_fail}/{n_total} CHECKPOINTS FAILED"
    print(f"  {R}{B}  {msg1}{RESET}")
    msg2 = "XRPL の記録と一致しません。データが改ざんされています。"
    print(f"  {R}  {msg2}{RESET}")
    print(f"  {R}{B}{'═' * (W - 2)}{RESET}")
    blank()

def final_banner_ok():
    blank()
    print(f"  {G}{B}{'═' * (W - 2)}{RESET}")
    print(f"  {G}{B}  ✅  全チェックポイントが正常  —  改ざんは検出されませんでした{RESET}")
    print(f"  {G}{B}{'═' * (W - 2)}{RESET}")
    blank()


# ── Main ─────────────────────────────────────────────────────────────────────
PROOF_PATH = Path(__file__).parent / "weave_proof.json"
TAMPER_IDX = 1   # 改ざんするチェックポイントの index

header()
print(f"  {DIM}シナリオ: 悪意ある第三者がモデルのスコアをこっそり書き換えた。{RESET}")
print(f"  {DIM}         XRPL が過去に刻んだ hash がそれを暴く。{RESET}")
blank()
time.sleep(1)

# ── Step 1: Load proof ────────────────────────────────────────────────────────
step_header(1, 4, "weave_proof.json を読み込む")
blank()

if not PROOF_PATH.exists():
    print(f"  {R}[ERROR]{RESET}  weave_proof.json が見つかりません。")
    print(f"  {DIM}先に Demo ① を実行してください:{RESET}")
    print(f"  {DIM}  python demo/hackathon_live.py{RESET}")
    blank()
    raise SystemExit(1)

proof = json.loads(PROOF_PATH.read_text())
final_tx   = proof["final_tx_hash"]
tx_hashes  = proof["tx_hashes"]
chunk_hashes = list(proof["chunk_hashes"])   # コピーして改ざん
wandb_url  = proof.get("wandb_run_url", "")

ok("weave_proof.json を読み込みました")
print(f"  {DIM}  チェックポイント数 : {len(tx_hashes)}{RESET}")
print(f"  {DIM}  W&B Run           : {wandb_url}{RESET}")
blank()

print(f"  {B}オリジナルの chunk_hashes:{RESET}")
for i, h in enumerate(chunk_hashes):
    kind = " (genesis)" if i == 0 else ""
    print(f"  {DIM}  [{i}]{kind}  {h}{RESET}")

time.sleep(1)

# ── Step 2: Tamper ────────────────────────────────────────────────────────────
step_header(2, 4, "データを改ざんする")
blank()

original_hash = chunk_hashes[TAMPER_IDX]

attack(f"チェックポイント #{TAMPER_IDX} のログデータを書き換えます...")
blank()
print(f"  {DIM}  変更前のデータイメージ:{RESET}")
print(f"  {DIM}    {{\"step\": {TAMPER_IDX}, \"result_len\": 583, \"weave_input_hash\": \"...\"}}{RESET}")
print(f"  {Y}  変更後のデータイメージ:{RESET}")
print(f"  {Y}    {{\"step\": {TAMPER_IDX}, \"result_len\": 999, \"weave_input_hash\": \"...\"}}{RESET}  {R}← 改ざん!{RESET}")
blank()
time.sleep(0.8)

# chunk_hash の先頭 8 文字を反転させて「別データのハッシュ」を模倣
tampered_hash = original_hash[:8].translate(str.maketrans("0123456789abcdef", "fedcba9876543210")) + original_hash[8:]
chunk_hashes[TAMPER_IDX] = tampered_hash

print(f"  {B}chunk_hashes[{TAMPER_IDX}] が変わりました:{RESET}")
print(f"  {DIM}  Before:{RESET}  {original_hash}")
print(f"  {R}  After: {RESET}  {tampered_hash}  {R}{B}← TAMPERED{RESET}")
blank()
time.sleep(1)

# ── Step 3: Explain ───────────────────────────────────────────────────────────
step_header(3, 4, "XRPL に刻まれた commit_hash と照合する")
blank()

print(f"  {DIM}XRPL の TX には、アンカー時点の正しい commit_hash が刻まれています:{RESET}")
print(f"  {DIM}  seq={TAMPER_IDX} TX: {tx_hashes[TAMPER_IDX][:20]}...{RESET}")
blank()
print(f"  {DIM}検証計算:{RESET}")
print(f"  {DIM}  expected = SHA-256(chain_{{i-1}} + tampered_chunk_hash){RESET}")
print(f"  {DIM}  expected ≠ on-chain commit_hash  →  改ざん検出{RESET}")
blank()
time.sleep(1)

# ── Step 4: Verify ────────────────────────────────────────────────────────────
step_header(4, 4, "verify_chain() を実行")
blank()

results = verify_chain(
    final_tx_hash=final_tx,
    chunk_hashes=chunk_hashes,   # 改ざん済み chunk_hashes を渡す
)

n_fail = 0
for r in results:
    idx = results.index(r)
    kind = "genesis" if idx == 0 else "chained"
    label = f"CHECKPOINT #{idx} [{kind}]"

    if r.verified:
        result_line(True, label, "seq ✓  prev ✓  hash ✓")
    else:
        result_line(False, label, "HASH MISMATCH")
        for e in r.errors:
            print(f"       {R}{e}{RESET}")
        if r.commit_hash_on_chain and r.commit_hash_computed:
            print(f"       {DIM}on-chain  : {r.commit_hash_on_chain}{RESET}")
            print(f"       {R}recomputed: {r.commit_hash_computed}  ← 改ざんされたデータのハッシュ{RESET}")
        n_fail += 1

if n_fail > 0:
    final_banner_fail(n_fail, len(results))
    print(f"  {DIM}なぜ検出できたか:{RESET}")
    print(f"  {DIM}  XRPL に刻まれた commit_hash は過去に遡って変更できない。{RESET}")
    print(f"  {DIM}  ローカルデータを書き換えると hash chain が一致しなくなる。{RESET}")
    print(f"  {DIM}  → データの改ざんを数学的に証明できる。{RESET}")
else:
    final_banner_ok()

blank()
