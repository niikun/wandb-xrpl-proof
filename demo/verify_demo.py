"""
Demo: XRPL 証跡の整合性検証

run_demo.py で生成された tx_hash と CID を使い、
オンチェーンとオフチェーンのデータが一致することを確認する。

実行:
    python verify_demo.py --tx <TX_HASH> --cid <IPFS_CID>

例:
    python verify_demo.py \\
        --tx B7C712769ABCD6693BAD6F04940EC5D70A8C0C4CA2ECD354E14449DAC8EF3C22 \\
        --cid QmULnZrVxMfp8jcuzCW4pAySKaaJkMipV3UBjbkAGQ45kT
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
from wandb_xrpl_proof.verify import verify_anchor
from wandb_xrpl_proof.xrpl_client import XRPL_TESTNET_URL, decode_memo, fetch_transaction


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="XRPL アンカリング証跡の整合性検証")
    parser.add_argument("--tx", required=True, metavar="TX_HASH", help="XRPL トランザクションハッシュ")
    parser.add_argument("--cid", required=False, metavar="CID", help="IPFS CID (省略時はオンチェーンの CID を使用)")
    parser.add_argument("--xrpl-node", default=XRPL_TESTNET_URL, help="XRPL ノード URL")
    parser.add_argument("--ipfs-gateway", default="http://127.0.0.1:8080/ipfs", help="IPFS ゲートウェイ URL")
    args = parser.parse_args()

    # ---------------------------------------------------------------------------
    # Step 1: XRPL からトランザクションを取得
    # ---------------------------------------------------------------------------
    _section("Step 1: XRPL からトランザクションを取得")

    print(f"  tx_hash : {args.tx}")
    print(f"  XRPL Explorer: https://testnet.xrpl.org/transactions/{args.tx}")

    tx_result = fetch_transaction(args.tx, network_url=args.xrpl_node)
    tx_json = tx_result.get("tx_json", tx_result)

    print(f"\n  TransactionType : {tx_json.get('TransactionType')}")
    print(f"  Account         : {tx_json.get('Account')}")
    print(f"  LedgerIndex     : {tx_result.get('ledger_index', tx_result.get('LedgerIndex', 'N/A'))}")

    # ---------------------------------------------------------------------------
    # Step 2: MemoData をデコード
    # ---------------------------------------------------------------------------
    _section("Step 2: オンチェーンの MemoData をデコード")

    memo = decode_memo(tx_result)
    print(f"  オンチェーンのメモ:")
    print(json.dumps(memo, indent=4, ensure_ascii=False))

    commit_hash_onchain = memo.get("commit_hash", "")
    cid_onchain = memo.get("cid")
    wandb_run_path = memo.get("wandb_run_path", "")

    print(f"\n  W&B Run Path  : {wandb_run_path}")
    print(f"  commit_hash   : {commit_hash_onchain}")
    print(f"  IPFS CID      : {cid_onchain or '(なし)'}")

    # ---------------------------------------------------------------------------
    # Step 3: IPFS からペイロードを取得
    # ---------------------------------------------------------------------------
    _section("Step 3: IPFS からペイロードを取得")

    target_cid = args.cid or cid_onchain
    if not target_cid:
        print("  [ERROR] CID が指定されておらず、オンチェーンにも CID が存在しません。")
        print("  --cid オプションで CID を指定するか、IPFS 付きでアンカリングしてください。")
        sys.exit(1)

    ipfs_url = f"{args.ipfs_gateway.rstrip('/')}/{target_cid}"
    print(f"  取得先: {ipfs_url}")

    try:
        resp = requests.get(ipfs_url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"  [ERROR] IPFS からの取得に失敗: {e}")
        print("  IPFS デーモンが起動しているか確認してください: ipfs daemon &")
        sys.exit(1)

    print(f"\n  取得したペイロード:")
    print(json.dumps(payload, indent=4, ensure_ascii=False))

    # ---------------------------------------------------------------------------
    # Step 4: ローカルで再ハッシュして比較
    # ---------------------------------------------------------------------------
    _section("Step 4: ペイロードを正規化して SHA-256 を再計算")

    canonical = canonicalize(payload)
    commit_hash_computed = compute_hash(canonical)

    print(f"  正規化 JSON:\n  {canonical}")
    print(f"\n  オンチェーンの commit_hash : {commit_hash_onchain}")
    print(f"  再計算した commit_hash     : {commit_hash_computed}")

    # ---------------------------------------------------------------------------
    # Step 5: 検証結果
    # ---------------------------------------------------------------------------
    _section("Step 5: 検証結果")

    match = commit_hash_onchain == commit_hash_computed

    if match:
        print("""
  ✅ INTEGRITY VERIFIED

  オンチェーンに刻まれたハッシュと、
  IPFS から取得したペイロードのハッシュが一致しました。

  このデータは改ざんされていません。
""")
    else:
        print(f"""
  ❌ INTEGRITY MISMATCH

  ハッシュが一致しません。データが改ざんされている可能性があります。

  オンチェーン : {commit_hash_onchain}
  再計算結果   : {commit_hash_computed}
""")
        sys.exit(1)

    print(f"  W&B Run  : https://wandb.ai/{wandb_run_path}")
    print(f"  XRPL tx  : https://testnet.xrpl.org/transactions/{args.tx}")
    print(f"  IPFS CID : {target_cid}")


if __name__ == "__main__":
    main()
