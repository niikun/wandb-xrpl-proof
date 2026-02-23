"""
Unit tests for anchor.py: build_payload() and @xrpl_anchor weave_input/output_hash.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from wandb_xrpl_proof.anchor import build_payload, SCHEMA_VERSION
from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_run(entity="ent", project="proj", run_id="run1"):
    run = MagicMock()
    run.entity = entity
    run.project = project
    run.id = run_id
    run.summary = {}
    run.config = {}
    return run


# ---------------------------------------------------------------------------
# TestBuildPayload — weave_input_hash / weave_output_hash
# ---------------------------------------------------------------------------

class TestBuildPayloadWeaveHashes:
    def test_weave_input_hash_included(self):
        run = _make_run()
        payload = build_payload(
            run=run,
            op_name="evaluate",
            include_summary=False,
            include_config=False,
            weave_input_hash="abc123",
        )
        assert payload["weave_input_hash"] == "abc123"

    def test_weave_output_hash_included(self):
        run = _make_run()
        payload = build_payload(
            run=run,
            op_name="evaluate",
            include_summary=False,
            include_config=False,
            weave_output_hash="def456",
        )
        assert payload["weave_output_hash"] == "def456"

    def test_both_hashes_included(self):
        run = _make_run()
        payload = build_payload(
            run=run,
            op_name="evaluate",
            include_summary=False,
            include_config=False,
            weave_input_hash="aaa",
            weave_output_hash="bbb",
        )
        assert payload["weave_input_hash"] == "aaa"
        assert payload["weave_output_hash"] == "bbb"

    def test_hashes_absent_when_not_provided(self):
        run = _make_run()
        payload = build_payload(
            run=run,
            op_name="evaluate",
            include_summary=False,
            include_config=False,
        )
        assert "weave_input_hash" not in payload
        assert "weave_output_hash" not in payload

    def test_hashes_affect_commit_hash(self):
        """weave_input_hash が違うと commit_hash も変わる（改ざん検出）。"""
        run = _make_run()
        p1 = build_payload(run=run, op_name="op", include_summary=False,
                           include_config=False, weave_input_hash="h1")
        p2 = build_payload(run=run, op_name="op", include_summary=False,
                           include_config=False, weave_input_hash="h2")
        assert compute_hash(canonicalize(p1)) != compute_hash(canonicalize(p2))


# ---------------------------------------------------------------------------
# TestXrplAnchorWrapper — weave_input/output_hash の抽出
# ---------------------------------------------------------------------------

class TestXrplAnchorWrapperWeaveHashes:
    def _make_weave_call(self, inputs: dict, output):
        call_obj = MagicMock()
        call_obj.id = "call-id-123"
        call_obj.ui_url = "https://wandb.ai/weave/call/call-id-123"
        call_obj.inputs = inputs
        call_obj.output = output
        return call_obj

    def _make_weave_op(self, call_obj):
        """call() メソッドを持つ Weave op 風関数を返す。"""
        def func(x):
            return {"result": x}
        func.call = MagicMock(return_value=({"result": "ok"}, call_obj))
        func.__name__ = "my_op"
        func.__wrapped__ = func
        return func

    @patch("wandb_xrpl_proof.anchor._anchor_current_run")
    @patch("wandb_xrpl_proof.anchor.wandb")
    def test_input_hash_extracted_and_passed(self, mock_wandb, mock_anchor):
        from wandb_xrpl_proof.anchor import xrpl_anchor

        inputs = {"prompt": "hello", "n": 3}
        output = {"result": "world"}
        call_obj = self._make_weave_call(inputs, output)
        func = self._make_weave_op(call_obj)

        decorated = xrpl_anchor()(func)
        decorated(x="hello")

        _, kwargs = mock_anchor.call_args
        expected_input_hash = compute_hash(canonicalize(inputs))
        assert kwargs["weave_input_hash"] == expected_input_hash

    @patch("wandb_xrpl_proof.anchor._anchor_current_run")
    @patch("wandb_xrpl_proof.anchor.wandb")
    def test_output_hash_extracted_and_passed(self, mock_wandb, mock_anchor):
        from wandb_xrpl_proof.anchor import xrpl_anchor

        inputs = {"prompt": "hello"}
        output = {"score": 0.95, "label": "positive"}
        call_obj = self._make_weave_call(inputs, output)
        func = self._make_weave_op(call_obj)

        decorated = xrpl_anchor()(func)
        decorated(x="hello")

        _, kwargs = mock_anchor.call_args
        expected_output_hash = compute_hash(canonicalize(output))
        assert kwargs["weave_output_hash"] == expected_output_hash

    @patch("wandb_xrpl_proof.anchor._anchor_current_run")
    @patch("wandb_xrpl_proof.anchor.wandb")
    def test_output_none_gives_no_hash(self, mock_wandb, mock_anchor):
        """output=None のとき weave_output_hash は None。"""
        from wandb_xrpl_proof.anchor import xrpl_anchor

        call_obj = self._make_weave_call({"x": 1}, None)
        func = self._make_weave_op(call_obj)

        decorated = xrpl_anchor()(func)
        decorated(x=1)

        _, kwargs = mock_anchor.call_args
        assert kwargs["weave_output_hash"] is None

    @patch("wandb_xrpl_proof.anchor._anchor_current_run")
    @patch("wandb_xrpl_proof.anchor.wandb")
    def test_hashes_none_for_non_weave_op(self, mock_wandb, mock_anchor):
        """@weave.op() でない通常関数では hash は None になる。"""
        from wandb_xrpl_proof.anchor import xrpl_anchor

        def plain_func(x):
            return x
        plain_func.__name__ = "plain_func"

        decorated = xrpl_anchor()(plain_func)
        decorated(x=42)

        _, kwargs = mock_anchor.call_args
        assert kwargs["weave_input_hash"] is None
        assert kwargs["weave_output_hash"] is None

    @patch("wandb_xrpl_proof.anchor._anchor_current_run")
    @patch("wandb_xrpl_proof.anchor.wandb")
    def test_inputs_error_gives_none(self, mock_wandb, mock_anchor):
        """call.inputs アクセスで例外が出ても hash は None でクラッシュしない。"""
        from wandb_xrpl_proof.anchor import xrpl_anchor

        call_obj = MagicMock()
        call_obj.id = "cid"
        call_obj.ui_url = "https://example.com"
        type(call_obj).inputs = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        func = MagicMock()
        func.__name__ = "op"
        func.__wrapped__ = func
        func.call = MagicMock(return_value=(None, call_obj))

        decorated = xrpl_anchor()(func)
        decorated()

        _, kwargs = mock_anchor.call_args
        assert kwargs["weave_input_hash"] is None


# ---------------------------------------------------------------------------
# TestSavePayloadPath
# ---------------------------------------------------------------------------

class TestSavePayloadPath:
    def test_payload_written_to_file(self, tmp_path):
        """save_payload_path を指定するとペイロード JSON が書き込まれる。"""
        import json
        from wandb_xrpl_proof.anchor import _anchor_current_run

        run = MagicMock()
        run.entity = "e"
        run.project = "p"
        run.id = "r"
        run.summary = {}
        run.config = {}

        out = tmp_path / "payload.json"

        with patch("wandb_xrpl_proof.anchor.submit_anchor", return_value="TX" * 32):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                _anchor_current_run(
                    op_name="test_op",
                    include_summary=False,
                    include_config=False,
                    use_ipfs=False,
                    summary_allowlist=None,
                    config_allowlist=None,
                    xrpl_seed_env="XRPL_WALLET_SEED",
                    xrpl_node_env="XRPL_NODE_URL",
                    ipfs_api_env="IPFS_API_URL",
                    run=run,
                    save_payload_path=str(out),
                )

        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["weave_op_name"] == "test_op"
        assert payload["wandb_run_path"] == "e/p/r"

    def test_no_file_when_path_not_given(self, tmp_path):
        """save_payload_path を省略するとファイルが作られない。"""
        from wandb_xrpl_proof.anchor import _anchor_current_run

        run = MagicMock()
        run.entity = "e"
        run.project = "p"
        run.id = "r"
        run.summary = {}
        run.config = {}

        with patch("wandb_xrpl_proof.anchor.submit_anchor", return_value="TX" * 32):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                _anchor_current_run(
                    op_name="test_op",
                    include_summary=False,
                    include_config=False,
                    use_ipfs=False,
                    summary_allowlist=None,
                    config_allowlist=None,
                    xrpl_seed_env="XRPL_WALLET_SEED",
                    xrpl_node_env="XRPL_NODE_URL",
                    ipfs_api_env="IPFS_API_URL",
                    run=run,
                )

        assert list(tmp_path.iterdir()) == []
