"""
Integration tests: XRPL testnet への実際の送信・取得・検証。

必要な環境変数:
    XRPL_WALLET_SEED: XRPL テストネットウォレットのシード

テストネット XRP の取得:
    https://faucet.altnet.rippletest.net/accounts

実行方法:
    XRPL_WALLET_SEED=s... pytest tests/integration/test_xrpl_integration.py -v
"""

import os

import pytest

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.xrpl_client import (
    XRPL_TESTNET_URL,
    decode_memo,
    fetch_transaction,
    submit_anchor,
)

# テストネットウォレットシードが設定されていない場合はスキップ
pytestmark = pytest.mark.skipif(
    not os.environ.get("XRPL_WALLET_SEED"),
    reason="XRPL_WALLET_SEED not set. Set it to run XRPL integration tests.",
)


@pytest.fixture(scope="module")
def wallet_seed() -> str:
    return os.environ["XRPL_WALLET_SEED"]


@pytest.fixture(scope="module")
def node_url() -> str:
    return os.environ.get("XRPL_NODE_URL", XRPL_TESTNET_URL)


class TestXRPLSubmitAndFetch:
    def test_submit_anchor_returns_tx_hash(self, wallet_seed, node_url):
        """実際にアンカーを送信し、tx_hash が返ることを確認する。"""
        payload = {
            "schema_version": "wandb-xrpl-proof-0.2",
            "wandb_run_path": "test_entity/test_project/integration_test",
            "weave_op_name": "test_op",
        }
        canonical = canonicalize(payload)
        commit_hash = compute_hash(canonical)

        memo = {
            "schema_version": "a2a-weave-xrpl-0.2",
            "wandb_run_path": "test_entity/test_project/integration_test",
            "commit_hash": commit_hash,
        }

        tx_hash = submit_anchor(wallet_seed=wallet_seed, memo=memo, network_url=node_url)
        assert tx_hash
        assert len(tx_hash) == 64
        assert tx_hash == tx_hash.upper()  # XRPL tx hash は大文字

    def test_fetch_and_decode_memo(self, wallet_seed, node_url):
        """送信したトランザクションを取得し、MemoData を正確にデコードできることを確認する。"""
        payload = {
            "schema_version": "wandb-xrpl-proof-0.2",
            "wandb_run_path": "test_entity/test_project/fetch_test",
            "weave_op_name": "fetch_test_op",
        }
        canonical = canonicalize(payload)
        commit_hash = compute_hash(canonical)

        memo = {
            "schema_version": "a2a-weave-xrpl-0.2",
            "wandb_run_path": "test_entity/test_project/fetch_test",
            "commit_hash": commit_hash,
        }

        tx_hash = submit_anchor(wallet_seed=wallet_seed, memo=memo, network_url=node_url)

        tx_result = fetch_transaction(tx_hash, network_url=node_url)
        decoded_memo = decode_memo(tx_result)

        assert decoded_memo["commit_hash"] == commit_hash
        assert decoded_memo["wandb_run_path"] == "test_entity/test_project/fetch_test"
        assert decoded_memo["schema_version"] == "a2a-weave-xrpl-0.2"


class TestVerificationRoundTrip:
    def test_verify_roundtrip(self, wallet_seed, node_url):
        """アンカー送信 → 取得 → 検証の E2E ラウンドトリップテスト。"""
        from wandb_xrpl_proof.verify import verify_anchor

        payload = {
            "schema_version": "wandb-xrpl-proof-0.2",
            "wandb_run_path": "test_entity/test_project/verify_test",
            "weave_op_name": "verify_op",
            "summary": {"loss": 0.25, "accuracy": 0.95},
        }
        canonical = canonicalize(payload)
        commit_hash = compute_hash(canonical)

        memo = {
            "schema_version": "a2a-weave-xrpl-0.2",
            "wandb_run_path": "test_entity/test_project/verify_test",
            "commit_hash": commit_hash,
        }

        tx_hash = submit_anchor(wallet_seed=wallet_seed, memo=memo, network_url=node_url)
        result = verify_anchor(tx_hash=tx_hash, payload=payload, xrpl_node=node_url)

        assert result.verified, f"Verification failed: {result.errors}"
        assert result.commit_hash_on_chain == commit_hash
        assert result.commit_hash_computed == commit_hash

    def test_tampered_payload_fails_verification(self, wallet_seed, node_url):
        """改ざんされたペイロードで検証が失敗することを確認する。"""
        from wandb_xrpl_proof.verify import verify_anchor

        payload = {
            "schema_version": "wandb-xrpl-proof-0.2",
            "wandb_run_path": "test_entity/test_project/tamper_test",
            "weave_op_name": "tamper_op",
            "summary": {"loss": 0.30},
        }
        canonical = canonicalize(payload)
        commit_hash = compute_hash(canonical)

        memo = {
            "schema_version": "a2a-weave-xrpl-0.2",
            "wandb_run_path": "test_entity/test_project/tamper_test",
            "commit_hash": commit_hash,
        }

        tx_hash = submit_anchor(wallet_seed=wallet_seed, memo=memo, network_url=node_url)

        # 改ざん: loss を変更
        tampered_payload = dict(payload)
        tampered_payload["summary"] = {"loss": 0.99}

        result = verify_anchor(tx_hash=tx_hash, payload=tampered_payload, xrpl_node=node_url)
        assert not result.verified
        assert result.commit_hash_on_chain != result.commit_hash_computed
