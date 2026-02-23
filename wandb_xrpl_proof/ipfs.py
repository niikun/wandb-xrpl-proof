"""
IPFS module for wandb-xrpl-proof.

Implements Section 8 of WandB_XRPL_Anchor_Spec_v0_2:
- Payload MAY be uploaded to IPFS (Kubo-compatible HTTP API)
- Stored content: canonical payload JSON + optional Merkle proof data
- CID MUST be included in XRPL Memo if used
"""

import json
import logging

import requests

logger = logging.getLogger(__name__)

DEFAULT_IPFS_API_URL = "http://127.0.0.1:5001"


def upload_to_ipfs(payload: dict, api_url: str = DEFAULT_IPFS_API_URL) -> str:
    """
    ペイロードを IPFS にアップロードし、CID を返す。

    Kubo 互換の IPFS HTTP API を使用する。
    IPFS デーモンが起動していること (ipfs daemon) が必要。

    Args:
        payload: アップロードする辞書 (JSON シリアライズ済みで保存される)
        api_url: IPFS HTTP API のベース URL (デフォルト: ローカルデーモン)

    Returns:
        アップロードされたコンテンツの CID 文字列

    Raises:
        requests.HTTPError: IPFS API エラー
        requests.ConnectionError: IPFS デーモンに接続できない場合
    """
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content_bytes = canonical_json.encode("utf-8")

    url = f"{api_url.rstrip('/')}/api/v0/add"
    response = requests.post(
        url,
        files={"file": ("payload.json", content_bytes, "application/json")},
        timeout=30,
    )
    response.raise_for_status()

    cid: str = response.json()["Hash"]
    logger.info("IPFS upload successful: CID=%s", cid)
    return cid


def fetch_from_ipfs(cid: str, gateway_url: str = "https://ipfs.io/ipfs") -> dict:
    """
    IPFS ゲートウェイからペイロードを取得して辞書として返す。

    Args:
        cid: コンテンツ識別子
        gateway_url: IPFS ゲートウェイのベース URL

    Returns:
        デコードされたペイロード辞書

    Raises:
        requests.HTTPError: ゲートウェイエラー
        json.JSONDecodeError: コンテンツが有効な JSON でない場合
    """
    url = f"{gateway_url.rstrip('/')}/{cid}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()
