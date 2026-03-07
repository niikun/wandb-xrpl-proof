#!/usr/bin/env python3
"""
Hackathon Demo ①  — wandb-xrpl-proof LIVE DEMO

Weave でトレースした LLM 呼び出しを XRPL ブロックチェーンへ記録し、
その場で整合性を検証する。

実行:
    python demo/hackathon_live.py

出力:
    - XRPL テストネット URL (チェックポイントごと)
    - weave_proof.json  (Demo ② で使用)
"""

import json
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

import wandb
import weave
from openai import OpenAI

from wandb_xrpl_proof import IncrementalAnchor
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
    print(f"  {C}{B}{'═' * (W - 2)}{RESET}")
    title = "wandb-xrpl-proof  ×  XRPL Blockchain Anchoring  |  Demo ①"
    pad = (W - 2 - len(title)) // 2
    print(f"  {C}{B}{'║'}{' ' * pad}{title}{' ' * (W - 2 - pad - len(title) - 1)}{'║'}{RESET}")
    print(f"  {C}{B}{'═' * (W - 2)}{RESET}")
    blank()

def step_header(n, total, text):
    blank()
    print(f"  {C}{B}[STEP {n}/{total}]{RESET}  {B}{text}{RESET}")
    sep()

def ok(label, detail=""):
    detail_str = f"  {DIM}{detail}{RESET}" if detail else ""
    print(f"  {G}✓{RESET}  {label}{detail_str}")

def info(label, value="", color=Y):
    val_str = f"  {color}{value}{RESET}" if value else ""
    print(f"  {C}→{RESET}  {label}{val_str}")

def result_line(verified, label, detail=""):
    mark = f"{G}✓{RESET}" if verified else f"{R}✗{RESET}"
    print(f"  {mark}  {label}  {DIM}{detail}{RESET}")

def final_banner(all_ok, n):
    blank()
    print(f"  {C}{B}{'═' * (W - 2)}{RESET}")
    if all_ok:
        msg = f"✅  {n}/{n} CHECKPOINTS VERIFIED  —  NO TAMPERING DETECTED"
        print(f"  {G}{B}  {msg}{RESET}")
    else:
        msg = "❌  VERIFICATION FAILED  —  DATA MAY BE CORRUPTED"
        print(f"  {R}{B}  {msg}{RESET}")
    print(f"  {C}{B}{'═' * (W - 2)}{RESET}")
    blank()


# ── Config ───────────────────────────────────────────────────────────────────
CHUNK_SIZE   = 2
N_CALLS      = 5
PROJECT      = "hackathon-xrpl-demo"
PROOF_PATH   = Path(__file__).parent / "weave_proof.json"

SENTENCE = (
    "I watched as a Tyrannosaurus rex chased after a Triceratops. "
    "Meanwhile, a gentle Brachiosaurus calmly munched on treetops."
)

# ── Main ─────────────────────────────────────────────────────────────────────
header()
print(f"  {DIM}Weave でトレースした LLM ログを、リアルタイムで XRPL に刻みます。{RESET}")
print(f"  {DIM}chunk_size={CHUNK_SIZE} → {CHUNK_SIZE} 呼び出しごとに自動チェックポイント送信。{RESET}")
blank()
time.sleep(1)

# ── Step 1: Init ─────────────────────────────────────────────────────────────
step_header(1, 4, "Weave + W&B を初期化")
weave.init(PROJECT)
ok("weave.init()", f"プロジェクト: {PROJECT}")

run = wandb.init(project=PROJECT, settings=wandb.Settings(silent=True))
ok("wandb.init()", run.url)
time.sleep(0.5)

# ── Step 2: Define Weave op ───────────────────────────────────────────────────
client = OpenAI()

@weave.op()
def extract_dinos(sentence: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "In JSON format extract a list of `dinosaurs`, "
                    "with their `name`, `common_name`, and `diet` (herbivore or carnivore)."
                ),
            },
            {"role": "user", "content": sentence},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content

# ── Step 3: Run ───────────────────────────────────────────────────────────────
step_header(2, 4, f"LLM を {N_CALLS} 回呼び出し  (chunk_size={CHUNK_SIZE})")
blank()

with IncrementalAnchor(run, chunk_size=CHUNK_SIZE) as anchor:
    for i in range(N_CALLS):
        t0 = time.time()
        result, call = extract_dinos.call(SENTENCE)
        elapsed = time.time() - t0

        ok(f"Call {i+1}/{N_CALLS}  extract_dinos()  ({elapsed:.1f}s)", "Weave trace captured")

        tx = anchor.log({"step": i, "result_len": len(result)}, weave_call=call)
        if tx:
            info(f"  CHECKPOINT → XRPL TX", tx[:20] + "...", color=Y)
            info(f"  URL", f"https://testnet.xrpl.org/transactions/{tx}", color=DIM)
        blank()

# ── Step 4: Show checkpoints ──────────────────────────────────────────────────
step_header(3, 4, "XRPL チェックポイント一覧")
blank()
for idx, tx in enumerate(anchor.tx_hashes):
    kind = "genesis" if idx == 0 else "chained"
    print(f"  {C}seq={idx}{RESET} [{kind}]")
    print(f"    {Y}{tx}{RESET}")
    print(f"    {DIM}https://testnet.xrpl.org/transactions/{tx}{RESET}")
    blank()

# Proof ファイルを保存
proof = {
    "final_tx_hash": anchor.tx_hashes[-1],
    "chunk_hashes":  anchor.chunk_hashes,
    "tx_hashes":     anchor.tx_hashes,
    "wandb_run_url": run.url,
    "samples":       N_CALLS,
    "chunk_size":    CHUNK_SIZE,
}
PROOF_PATH.write_text(json.dumps(proof, indent=2))
info("weave_proof.json を保存", "(Demo ② の改ざん検知デモで使用)", color=DIM)

run.finish()

# ── Step 5: Auto verify ───────────────────────────────────────────────────────
step_header(4, 4, "その場で整合性検証  —  XRPL prev リンクを辿り hash chain を再計算")
blank()
print(f"  {DIM}検証ステップ:{RESET}")
print(f"  {DIM}  1. XRPL の prev リンクを末尾から genesis まで辿る{RESET}")
print(f"  {DIM}  2. seq 連続性・prev リンク整合性を確認{RESET}")
print(f"  {DIM}  3. SHA-256((chain_{{i-1}} or '') + chunk_hash_i) が on-chain commit_hash と一致するか{RESET}")
blank()
time.sleep(0.5)

results = verify_chain(
    final_tx_hash=anchor.tx_hashes[-1],
    chunk_hashes=anchor.chunk_hashes,
)

all_ok = True
for r in results:
    idx = results.index(r)
    kind = "genesis" if idx == 0 else "chained"
    label = f"CHECKPOINT #{idx} [{kind}]"
    detail = "seq ✓  prev ✓  hash ✓" if r.verified else "  ".join(r.errors)
    result_line(r.verified, label, detail)
    if not r.verified:
        all_ok = False

final_banner(all_ok, len(results))

print(f"  {DIM}次のステップ:{RESET}")
print(f"  {DIM}  python demo/hackathon_tamper.py   ← 改ざんを検知するデモ{RESET}")
blank()
