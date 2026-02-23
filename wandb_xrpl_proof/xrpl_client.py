"""
XRPL client module for wandb-xrpl-proof.

Implements Section 9 of WandB_XRPL_Anchor_Spec_v0_2:
- Transaction type: AccountSet (memo-only, no destination needed)
  Note: 仕様では Payment + self-destination を推奨しているが、
  xrpl-py v2 以降は XRPL プロトコルの制約により self-payment が禁止されている。
  AccountSet はメモを付与できる最小コストのトランザクションとして代替採用。
- MemoData: JSON-encoded commit metadata (max 256 bytes)
- MemoType: hex("application/json")
- Total Memos size: ~1KB
"""

import json
import logging

import xrpl
from xrpl.clients import JsonRpcClient
from xrpl.models.transactions import AccountSet, Memo
from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet

logger = logging.getLogger(__name__)

XRPL_TESTNET_URL = "https://s.altnet.rippletest.net:51234"
_MEMO_TYPE_HEX = "application/json".encode("utf-8").hex()


def submit_anchor(
    wallet_seed: str,
    memo: dict,
    network_url: str = XRPL_TESTNET_URL,
) -> str:
    """
    XRPL に commit を AccountSet トランザクションとして送信する。

    AccountSet は宛先不要でメモを付与できる XRPL トランザクション。
    xrpl-py v2+ で self-payment が禁止されているため Payment の代替として採用。

    Args:
        wallet_seed: XRPL ウォレットのシード (環境変数から取得すること)
        memo: XRPL Memo に書き込む辞書 (JSON シリアライズ後 256 バイト以内)
        network_url: XRPL ノード URL (デフォルト: テストネット)

    Returns:
        送信されたトランザクションのハッシュ (tx_hash)

    Raises:
        ValueError: memo が 256 バイトを超える場合
        xrpl.XRPLException: トランザクション送信エラー
    """
    memo_json = json.dumps(memo, sort_keys=True, separators=(",", ":"))
    memo_bytes = memo_json.encode("utf-8")

    if len(memo_bytes) > 256:
        raise ValueError(
            f"MemoData exceeds 256 bytes ({len(memo_bytes)} bytes). "
            "Consider using IPFS and storing only the CID."
        )

    memo_data_hex = memo_bytes.hex()
    client = JsonRpcClient(network_url)
    wallet = Wallet.from_seed(wallet_seed)

    account_set = AccountSet(
        account=wallet.address,
        memos=[
            Memo(
                memo_data=memo_data_hex,
                memo_type=_MEMO_TYPE_HEX,
            )
        ],
    )

    response = submit_and_wait(account_set, client, wallet)
    tx_hash: str = response.result["hash"]
    logger.info("XRPL anchor submitted: tx_hash=%s", tx_hash)
    return tx_hash


def fetch_transaction(tx_hash: str, network_url: str = XRPL_TESTNET_URL) -> dict:
    """
    XRPL からトランザクションを取得する。

    Args:
        tx_hash: トランザクションハッシュ
        network_url: XRPL ノード URL

    Returns:
        トランザクション結果の辞書
    """
    client = JsonRpcClient(network_url)
    request = xrpl.models.requests.Tx(transaction=tx_hash)
    response = client.request(request)
    return response.result


def decode_memo(tx_result: dict) -> dict:
    """
    トランザクション結果から最初の MemoData をデコードして返す。

    xrpl-py v2 以降は Memos が tx_json 以下に格納される場合があるため両方を探索する。

    Args:
        tx_result: fetch_transaction() の戻り値

    Returns:
        デコードされた memo 辞書

    Raises:
        KeyError: Memos フィールドが存在しない場合
        json.JSONDecodeError: MemoData が JSON でない場合
    """
    # xrpl-py v2+ では tx_json 以下にトランザクションフィールドが格納される
    tx_json = tx_result.get("tx_json", tx_result)
    memos = tx_json.get("Memos", [])
    if not memos:
        raise KeyError("No Memos found in transaction")

    memo_data_hex = memos[0]["Memo"]["MemoData"]
    memo_json = bytes.fromhex(memo_data_hex).decode("utf-8")
    return json.loads(memo_json)
