"""
Demo: XRPL 証跡の整合性検証

2 つのモードで動作する:

  --chain モード (IncrementalAnchor の hash chain を検証):
      run_demo.py が生成した demo_proof.json を読み込み、
      XRPL の prev リンクを辿りながら hash chain を再計算して照合する。

      python verify_demo.py --chain --proof demo_proof.json

  通常モード (単一アンカーを IPFS + SHA-256 で検証):
      python verify_demo.py --tx <TX_HASH> [--cid <CID>]

実行例:
    # chain 検証
    python verify_demo.py --chain --proof demo_proof.json

    # 単一アンカー検証
    python verify_demo.py --tx B7C712769ABCD6693BAD6F04940EC5D70A8C0C4CA2ECD354E14449DAC8EF3C22
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

import requests

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.verify import verify_chain
from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, decode_memo, fetch_transaction

W = 62


def _sep(char: str = "=") -> None:
    print(char * W)


# ---------------------------------------------------------------------------
# chain 検証モード
# ---------------------------------------------------------------------------

def _run_chain_verify(proof_path: Path, xrpl_node: str) -> None:
    """demo_proof.json を使って hash chain をフル検証する。"""

    # --- proof ファイル読み込み ---
    try:
        proof = json.loads(proof_path.read_text())
    except Exception as e:
        print(f"[ERROR] proof ファイルの読み込みに失敗: {e}")
        sys.exit(1)

    final_tx_hash: str = proof.get("final_tx_hash") or ""
    chunk_hashes: list[str] = proof.get("chunk_hashes", [])
    tx_hashes: list[str] = proof.get("tx_hashes", [])
    samples: int = proof.get("samples", "?")
    chunk_size: int = proof.get("chunk_size", "?")
    wandb_run_url: str = proof.get("wandb_run_url", "")

    if not final_tx_hash:
        print("[ERROR] proof ファイルに final_tx_hash がありません。run_demo.py を先に実行してください。")
        sys.exit(1)

    _sep()
    print("  wandb-xrpl-proof  |  Hash Chain Verification")
    _sep()
    print()
    print(f"  Proof file   : {proof_path.name}")
    print(f"  Samples      : {samples}  /  chunk_size={chunk_size}")
    print(f"  Checkpoints  : {len(tx_hashes)} tx  /  chunk_hashes={len(chunk_hashes)}")
    print(f"  W&B Run      : {wandb_run_url}")
    print()
    print("  検証方法:")
    print("    1. XRPL の prev リンクを辿り全チェックポイントを収集")
    print("    2. seq 連続性・prev リンク整合性を確認")
    print("    3. hash chain を再計算してオンチェーン commit_hash と照合")
    print("       expected_i = SHA-256((chain_{i-1} or '') + chunk_hash_i)")
    _sep("-")

    # --- verify_chain 呼び出し ---
    results = verify_chain(
        final_tx_hash=final_tx_hash,
        chunk_hashes=chunk_hashes if chunk_hashes else None,
        xrpl_node=xrpl_node,
    )

    # --- 結果表示 ---
    all_ok = True
    for r in results:
        seq_label = f"#{ results.index(r)}"
        kind = "genesis" if results.index(r) == 0 else "chained"
        status = "OK" if r.verified else "FAIL"

        print()
        print(f"  CHECKPOINT {seq_label} [{kind}]  --  {status}")
        print(f"    TX           : https://testnet.xrpl.org/transactions/{r.tx_hash}")
        if r.commit_hash_on_chain:
            print(f"    on-chain     : {r.commit_hash_on_chain}")
        if r.commit_hash_computed:
            match = r.commit_hash_on_chain == r.commit_hash_computed
            marker = "==" if match else "!= (MISMATCH)"
            print(f"    recomputed   : {r.commit_hash_computed}  {marker}")
        if r.errors:
            for err in r.errors:
                print(f"    [ERROR] {err}")
        if not r.verified:
            all_ok = False

    # --- サマリ ---
    print()
    _sep("-")
    passed = sum(1 for r in results if r.verified)
    print()
    if all_ok:
        print(f"  RESULT: {passed}/{len(results)} checkpoints VERIFIED")
        print()
        if chunk_hashes:
            print("  Hash chain integrity : CONFIRMED")
            print("  chunk_hashes が XRPL 上の commit_hash と完全に一致しました。")
            print("  このデータは改ざんされていません。")
        else:
            print("  Structural integrity : CONFIRMED  (hash chain 再計算は chunk_hashes なしのためスキップ)")
    else:
        failed = len(results) - passed
        print(f"  RESULT: {failed}/{len(results)} checkpoints FAILED")
        print()
        print("  整合性の問題が検出されました。データが改ざんされている可能性があります。")
    print()


# ---------------------------------------------------------------------------
# 単一アンカー検証モード (既存)
# ---------------------------------------------------------------------------

def _run_single_verify(
    tx_hash: str,
    cid_arg: str | None,
    xrpl_node: str,
    ipfs_gateway: str,
    payload_file: str | None = None,
) -> None:
    """単一 @xrpl_anchor / anchor_run_end のトランザクションを検証する。"""

    _sep()
    print("  wandb-xrpl-proof  |  Single Anchor Verification")
    _sep()

    # Step 1: XRPL からトランザクションを取得
    print()
    print(f"  TX Hash       : {tx_hash}")
    print(f"  XRPL Explorer : https://testnet.xrpl.org/transactions/{tx_hash}")

    tx_result = fetch_transaction(tx_hash, network_url=xrpl_node)
    tx_json = tx_result.get("tx_json", tx_result)

    print(f"  Type          : {tx_json.get('TransactionType')}")
    print(f"  Account       : {tx_json.get('Account')}")
    print(f"  LedgerIndex   : {tx_result.get('ledger_index', tx_result.get('LedgerIndex', 'N/A'))}")

    # Step 2: MemoData をデコード
    print()
    _sep("-")
    print("  オンチェーン MemoData:")
    _sep("-")
    memo = decode_memo(tx_result)
    print(json.dumps(memo, indent=4, ensure_ascii=False))

    commit_hash_onchain = memo.get("commit_hash", "")
    cid_onchain = memo.get("cid")
    wandb_run_path = memo.get("wandb_run_path", "")

    print()
    print(f"  W&B Run Path  : {wandb_run_path}")
    print(f"  commit_hash   : {commit_hash_onchain}")
    print(f"  IPFS CID      : {cid_onchain or '(なし)'}")

    # Step 3: ペイロード取得 (--payload ファイル優先、次に IPFS)
    print()
    _sep("-")

    if payload_file:
        # --payload FILE が指定されている場合は IPFS 不要
        try:
            payload = json.loads(Path(payload_file).read_text())
            print(f"  Payload : {payload_file} (ローカルファイル)")
        except Exception as e:
            print(f"  [ERROR] payload ファイルの読み込みに失敗: {e}")
            sys.exit(1)
    else:
        target_cid = cid_arg or cid_onchain
        if not target_cid:
            print("  [ERROR] ペイロードの取得方法が指定されていません。")
            print("  以下のいずれかを指定してください:")
            print("    --payload payload.json  (アンカリング時に保存したファイル)")
            print("    --cid <CID>             (IPFS CID、daemon が必要)")
            sys.exit(1)

        ipfs_url = f"{ipfs_gateway.rstrip('/')}/{target_cid}"
        print(f"  IPFS URL : {ipfs_url}")

        try:
            resp = requests.get(ipfs_url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except requests.ConnectionError:
            print()
            print("  [ERROR] IPFS daemon に接続できません。")
            print("  ペイロードはローカル IPFS ノードにのみ存在するため、公開ゲートウェイでは取得できません。")
            print()
            print("  解決策:")
            print("    1. ローカル IPFS daemon を起動して再実行:  ipfs daemon &")
            print("    2. --payload でペイロード JSON を直接渡す:")
            print("       python verify_demo.py --tx <TX> --payload payload.json")
            print()
            print("  IncrementalAnchor の検証 (IPFS 不要) は --chain を使用:")
            print("    python verify_demo.py --chain --proof demo_proof.json")
            sys.exit(1)
        except Exception as e:
            print(f"  [ERROR] IPFS からの取得に失敗: {e}")
            sys.exit(1)

    print()
    print("  ペイロード:")
    print(json.dumps(payload, indent=4, ensure_ascii=False))

    # Step 4: 再ハッシュして比較
    print()
    _sep("-")
    canonical = canonicalize(payload)
    commit_hash_computed = compute_hash(canonical)

    print(f"  on-chain  commit_hash : {commit_hash_onchain}")
    print(f"  recomputed            : {commit_hash_computed}")

    # Step 5: 結果
    print()
    _sep("-")
    if commit_hash_onchain == commit_hash_computed:
        print()
        print("  RESULT: VERIFIED")
        print("  ハッシュが一致しました。このデータは改ざんされていません。")
    else:
        print()
        print("  RESULT: MISMATCH")
        print("  ハッシュが一致しません。データが改ざんされている可能性があります。")
        sys.exit(1)
    print()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="XRPL アンカリング証跡の整合性検証")
    parser.add_argument(
        "--chain", action="store_true",
        help="IncrementalAnchor の hash chain を検証 (--proof が必要)",
    )
    parser.add_argument(
        "--proof", default="demo_proof.json", metavar="FILE",
        help="run_demo.py が生成した proof ファイル (デフォルト: demo_proof.json)",
    )
    parser.add_argument("--tx", metavar="TX_HASH", help="単一アンカーの XRPL tx hash")
    parser.add_argument("--cid", metavar="CID", help="IPFS CID (省略時はオンチェーンの値を使用)")
    parser.add_argument(
        "--payload", metavar="FILE",
        help="ペイロード JSON ファイル (IPFS 不要; アンカリング時に保存したファイルを渡す)",
    )
    parser.add_argument("--xrpl-node", default=XRPL_TESTNET_URL, help="XRPL ノード URL")
    parser.add_argument(
        "--ipfs-gateway", default="http://127.0.0.1:8080/ipfs",
        help="IPFS ゲートウェイ URL",
    )
    args = parser.parse_args()

    if args.chain:
        proof_path = Path(__file__).parent / args.proof
        if not proof_path.exists():
            # 絶対パスも試みる
            proof_path = Path(args.proof)
        if not proof_path.exists():
            print(f"[ERROR] proof ファイルが見つかりません: {args.proof}")
            print("  run_demo.py を先に実行してください。")
            sys.exit(1)
        _run_chain_verify(proof_path, xrpl_node=args.xrpl_node)
    else:
        if not args.tx:
            parser.error("--tx TX_HASH が必要です (単一アンカー検証)。chain 検証は --chain を使用してください。")
        _run_single_verify(
            tx_hash=args.tx,
            cid_arg=args.cid,
            xrpl_node=args.xrpl_node,
            ipfs_gateway=args.ipfs_gateway,
            payload_file=args.payload,
        )


if __name__ == "__main__":
    main()
