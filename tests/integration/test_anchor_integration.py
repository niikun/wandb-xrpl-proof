"""
Integration tests: W&B + Weave + XRPL E2E テスト。

必要な環境変数:
    XRPL_WALLET_SEED: XRPL テストネットウォレットのシード
    WANDB_API_KEY: W&B API キー

実行方法:
    XRPL_WALLET_SEED=s... WANDB_API_KEY=... pytest tests/integration/test_anchor_integration.py -v
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# 両方の環境変数が必要
pytestmark = pytest.mark.skipif(
    not (os.environ.get("XRPL_WALLET_SEED") and os.environ.get("WANDB_API_KEY")),
    reason="XRPL_WALLET_SEED and WANDB_API_KEY must be set for anchor integration tests.",
)


@pytest.fixture
def mock_wandb_run():
    """W&B run のモックを返す。"""
    run = MagicMock()
    run.entity = "test_entity"
    run.project = "test_project"
    run.id = "integration_run_001"
    run.summary = {}
    run.config = {"learning_rate": 0.001, "epochs": 10}
    return run


class TestAnchorE2E:
    def test_anchor_writes_tx_hash_to_summary(self, mock_wandb_run):
        """アンカリング後に tx_hash が run.summary に書き込まれることを確認する。"""
        from wandb_xrpl_proof.anchor import _anchor_current_run

        with patch("wandb_xrpl_proof.anchor.wandb") as mock_wandb:
            mock_wandb.run = mock_wandb_run
            _anchor_current_run(
                op_name="e2e_test_op",
                include_summary=False,
                include_config=True,
                use_ipfs=False,
                summary_allowlist=None,
                config_allowlist=None,
                xrpl_seed_env="XRPL_WALLET_SEED",
                xrpl_node_env="XRPL_NODE_URL",
                ipfs_api_env="IPFS_API_URL",
            )

        assert "xrpl_tx_hash" in mock_wandb_run.summary
        assert "xrpl_commit_hash" in mock_wandb_run.summary
        tx_hash = mock_wandb_run.summary["xrpl_tx_hash"]
        assert len(tx_hash) == 64

    def test_anchor_failure_does_not_raise(self, mock_wandb_run):
        """XRPL 送信失敗時にクラッシュせず、エラーが summary に記録されることを確認する。"""
        from wandb_xrpl_proof.anchor import _anchor_current_run

        with patch("wandb_xrpl_proof.anchor.wandb") as mock_wandb, \
             patch("wandb_xrpl_proof.anchor.submit_anchor", side_effect=Exception("XRPL error")):
            mock_wandb.run = mock_wandb_run
            # 例外が伝播しないこと
            _anchor_current_run(
                op_name="failing_op",
                include_summary=False,
                include_config=False,
                use_ipfs=False,
                summary_allowlist=None,
                config_allowlist=None,
                xrpl_seed_env="XRPL_WALLET_SEED",
                xrpl_node_env="XRPL_NODE_URL",
                ipfs_api_env="IPFS_API_URL",
            )

        assert "xrpl_anchor_error" in mock_wandb_run.summary

    def test_build_payload_with_allowlist(self, mock_wandb_run):
        """アローリストが適切にフィルタリングされることを確認する。"""
        from wandb_xrpl_proof.anchor import build_payload

        mock_wandb_run.summary = {"loss": 0.5, "accuracy": 0.9, "private_field": "secret"}
        payload = build_payload(
            run=mock_wandb_run,
            op_name="allowlist_test_op",
            include_summary=True,
            include_config=False,
            summary_allowlist=["loss", "accuracy"],
        )

        assert "loss" in payload["summary"]
        assert "accuracy" in payload["summary"]
        assert "private_field" not in payload["summary"]
        assert "config" not in payload
