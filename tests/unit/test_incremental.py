"""
Unit tests for IncrementalAnchor, verify_chain, DEFAULT_PII_KEYS, and compute_chain_step.

XRPL ネットワーク呼び出しはすべて unittest.mock.patch でモック。
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from wandb_xrpl_proof.canonicalize import DEFAULT_PII_KEYS, canonicalize
from wandb_xrpl_proof.hash import compute_chain_step, compute_hash


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_run(entity="ent", project="proj", run_id="run1"):
    run = MagicMock()
    run.entity = entity
    run.project = project
    run.id = run_id
    run.summary = {}
    return run


def _make_rows(n: int) -> list[dict]:
    return [{"step": i, "loss": 1.0 / (i + 1)} for i in range(n)]


def _memo_to_hex(memo: dict) -> str:
    return json.dumps(memo, sort_keys=True, separators=(",", ":")).encode().hex()


# ---------------------------------------------------------------------------
# TestComputeChainStep
# ---------------------------------------------------------------------------

class TestComputeChainStep:
    def test_first_step_no_prev(self):
        """prev=None のとき sha256("" + chunk_hash) を返す。"""
        import hashlib
        chunk_hash = "ab" * 32
        expected = hashlib.sha256(chunk_hash.encode()).hexdigest()
        assert compute_chain_step(None, chunk_hash) == expected

    def test_subsequent_step(self):
        """prev がある場合 sha256(prev + chunk_hash) を返す。"""
        import hashlib
        prev = "cc" * 32
        chunk_hash = "dd" * 32
        expected = hashlib.sha256((prev + chunk_hash).encode()).hexdigest()
        assert compute_chain_step(prev, chunk_hash) == expected

    def test_deterministic(self):
        h1 = compute_chain_step("aa" * 32, "bb" * 32)
        h2 = compute_chain_step("aa" * 32, "bb" * 32)
        assert h1 == h2

    def test_output_is_64_char_lowercase_hex(self):
        result = compute_chain_step(None, "ee" * 32)
        assert len(result) == 64
        assert result == result.lower()
        int(result, 16)  # hex として解釈できること

    def test_changing_chunk_changes_result(self):
        h1 = compute_chain_step("aa" * 32, "bb" * 32)
        h2 = compute_chain_step("aa" * 32, "cc" * 32)
        assert h1 != h2

    def test_chain_propagates_tamper(self):
        """chunk_0 を変えると以降の chain_hash がすべて変わる。"""
        chunk_hashes_orig = ["aa" * 32, "bb" * 32, "cc" * 32]
        chunk_hashes_tampered = ["XX" * 32, "bb" * 32, "cc" * 32]

        def build_chain(chunks):
            h = None
            for c in chunks:
                h = compute_chain_step(h, c)
            return h

        assert build_chain(chunk_hashes_orig) != build_chain(chunk_hashes_tampered)


# ---------------------------------------------------------------------------
# TestDefaultPiiKeys
# ---------------------------------------------------------------------------

class TestDefaultPiiKeys:
    def test_is_frozenset(self):
        assert isinstance(DEFAULT_PII_KEYS, frozenset)

    def test_contains_common_pii_fields(self):
        for key in ("email", "name", "phone", "ip_address", "password", "ssn"):
            assert key in DEFAULT_PII_KEYS, f"{key!r} should be in DEFAULT_PII_KEYS"

    def test_excludes_pii_from_canonicalize(self):
        obj = {"loss": 0.5, "email": "user@example.com", "name": "Alice"}
        result = json.loads(canonicalize(obj, exclude_keys=DEFAULT_PII_KEYS))
        assert "loss" in result
        assert "email" not in result
        assert "name" not in result

    def test_nested_pii_excluded(self):
        obj = {"metrics": {"loss": 0.5, "email": "x@y.com"}}
        result = json.loads(canonicalize(obj, exclude_keys=DEFAULT_PII_KEYS))
        assert "email" not in result["metrics"]
        assert result["metrics"]["loss"] == 0.5

    def test_can_extend_with_custom_keys(self):
        extended = DEFAULT_PII_KEYS | {"custom_secret"}
        obj = {"loss": 0.1, "custom_secret": "val", "email": "a@b.com"}
        result = json.loads(canonicalize(obj, exclude_keys=extended))
        assert "custom_secret" not in result
        assert "email" not in result
        assert result["loss"] == 0.1


# ---------------------------------------------------------------------------
# TestExtractTraceFields
# ---------------------------------------------------------------------------

class TestExtractTraceFields:
    """_extract_trace_fields のユニットテスト。"""

    def _make_call(self, **kwargs):
        """Weave Call を模したモックを返す。"""
        call = MagicMock()
        call.id = kwargs.get("id", "abc-123")
        call.func_name = kwargs.get("func_name", "evaluate_response")
        call.op_name = kwargs.get("op_name", "weave:///proj/evaluate_response:v0")
        call.inputs = kwargs.get("inputs", {"prompt": "hello", "reference": "hi"})
        call.output = kwargs.get("output", {"f1": 0.5})
        call._children = kwargs.get("children", [])
        return call

    def test_extracts_call_id(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call = self._make_call(id="test-uuid-001")
        fields = _extract_trace_fields(call)
        assert fields["weave_call_id"] == "test-uuid-001"

    def test_extracts_func_name(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call = self._make_call(func_name="my_eval_op")
        fields = _extract_trace_fields(call)
        assert fields["weave_op_name"] == "my_eval_op"

    def test_input_hash_is_deterministic(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call = self._make_call(inputs={"prompt": "hello", "ref": "hi"})
        h1 = _extract_trace_fields(call)["weave_input_hash"]
        h2 = _extract_trace_fields(call)["weave_input_hash"]
        assert h1 == h2
        assert len(h1) == 64

    def test_input_hash_changes_with_different_inputs(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call_a = self._make_call(inputs={"prompt": "hello"})
        call_b = self._make_call(inputs={"prompt": "world"})
        assert _extract_trace_fields(call_a)["weave_input_hash"] != \
               _extract_trace_fields(call_b)["weave_input_hash"]

    def test_output_hash_included(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call = self._make_call(output={"f1": 0.9})
        fields = _extract_trace_fields(call)
        assert "weave_output_hash" in fields
        assert len(fields["weave_output_hash"]) == 64

    def test_no_output_hash_when_output_is_none(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call = self._make_call(output=None)
        fields = _extract_trace_fields(call)
        assert "weave_output_hash" not in fields

    def test_tool_calls_extracted_from_children(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        child_a = MagicMock()
        child_a.func_name = "search_web"
        child_b = MagicMock()
        child_b.func_name = "calculator"
        call = self._make_call(children=[child_a, child_b])
        fields = _extract_trace_fields(call)
        assert fields["weave_tool_calls"] == ["search_web", "calculator"]

    def test_no_tool_calls_when_no_children(self):
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        call = self._make_call(children=[])
        fields = _extract_trace_fields(call)
        assert "weave_tool_calls" not in fields

    def test_graceful_on_broken_call(self):
        """フィールド取得が全て失敗しても空 dict を返し例外を出さない。"""
        from wandb_xrpl_proof.incremental import _extract_trace_fields
        broken = MagicMock(spec=[])  # 何も属性を持たないモック
        fields = _extract_trace_fields(broken)
        assert isinstance(fields, dict)

    def test_record_includes_trace_fields(self):
        """record(data, weave_call=call) でトレースフィールドがバッファに積まれる。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        call = self._make_call(id="trace-42", func_name="my_op",
                               inputs={"x": 1}, output={"y": 2})
        anchor = IncrementalAnchor(run=run, chunk_size=10)
        anchor.record({"loss": 0.3}, weave_call=call)

        with anchor._lock:
            row = anchor._buffer[0]

        assert row["loss"] == 0.3
        assert row["weave_call_id"] == "trace-42"
        assert row["weave_op_name"] == "my_op"
        assert "weave_input_hash" in row
        assert "weave_output_hash" in row


# ---------------------------------------------------------------------------
# TestIncrementalAnchorInit
# ---------------------------------------------------------------------------

class TestIncrementalAnchorInit:
    def test_raises_when_run_is_none(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        with pytest.raises(ValueError, match="None"):
            IncrementalAnchor(run=None)

    def test_initial_state(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=10)
        assert anchor.seq == 0
        assert anchor.tx_hashes == []
        assert anchor._chain_hash is None
        assert anchor._prev_tx_hash is None
        assert anchor._closed is False

    def test_default_chunk_size(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run)
        assert anchor._chunk_size == 1000


# ---------------------------------------------------------------------------
# TestRecord
# ---------------------------------------------------------------------------

class TestRecord:
    def test_below_chunk_size_returns_none(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=10)
        for row in _make_rows(9):
            result = anchor.record(row)
            assert result is None

    def test_exact_chunk_triggers_submit(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        fake_tx = "A" * 64
        anchor = IncrementalAnchor(run=run, chunk_size=10)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(9):
                    anchor.record(row)
                result = anchor.record({"step": 9, "loss": 0.1})
        assert result == fake_tx

    def test_seq0_memo_has_run_path_no_prev(self):
        """seq=0 の memo は wandb_run_path を含み prev を含まない。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        from wandb_xrpl_proof.anchor import SCHEMA_VERSION as MEMO_SCHEMA_VERSION
        run = _make_run(entity="ent", project="proj", run_id="run1")
        captured = {}

        def fake_submit(**kwargs):
            memo = kwargs["memo"]
            captured["memo"] = dict(memo)
            return "B" * 64

        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=fake_submit):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)

        memo = captured["memo"]
        assert memo["seq"] == 0
        assert memo["wandb_run_path"] == "ent/proj/run1"
        assert "prev" not in memo
        assert memo["schema_version"] == MEMO_SCHEMA_VERSION

    def test_seq1_memo_has_prev_no_run_path(self):
        """seq=1 の memo は prev を含み wandb_run_path を含まない。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        tx0 = "C" * 64
        tx1 = "D" * 64
        memos = []

        def fake_submit(**kwargs):
            memo = kwargs["memo"]
            memos.append(dict(memo))
            return tx0 if len(memos) == 1 else tx1

        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=fake_submit):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(10):
                    anchor.record(row)

        assert memos[1]["seq"] == 1
        assert memos[1]["prev"] == tx0
        assert "wandb_run_path" not in memos[1]

    def test_chain_hash_accumulates_correctly(self):
        """commit_hash は hash chain の期待値と一致する。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        rows = _make_rows(10)
        chunk0 = rows[:5]
        chunk1 = rows[5:]
        chunk0_hash = compute_hash(canonicalize(chunk0))
        chunk1_hash = compute_hash(canonicalize(chunk1))
        expected_chain0 = compute_chain_step(None, chunk0_hash)
        expected_chain1 = compute_chain_step(expected_chain0, chunk1_hash)

        captured_commit_hashes = []

        def fake_submit(**kwargs):
            memo = kwargs["memo"]
            captured_commit_hashes.append(memo["commit_hash"])
            return "E" * 64

        anchor = IncrementalAnchor(run=_make_run(), chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=fake_submit):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in rows:
                    anchor.record(row)

        assert captured_commit_hashes[0] == expected_chain0
        assert captured_commit_hashes[1] == expected_chain1

    def test_pii_excluded_from_chunk_hash(self):
        """exclude_keys 指定時、PII フィールドが除外されたハッシュになる。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        rows = [{"step": i, "loss": 0.1, "email": "user@test.com"} for i in range(5)]

        chunk0_hash_with_pii = compute_hash(canonicalize(rows))
        chunk0_hash_no_pii = compute_hash(canonicalize(rows, exclude_keys={"email"}))
        # PII あり/なしでハッシュが異なることを事前確認
        assert chunk0_hash_with_pii != chunk0_hash_no_pii

        captured = {}

        def fake_submit(**kwargs):
            memo = kwargs["memo"]
            captured["commit_hash"] = memo["commit_hash"]
            return "F" * 64

        anchor = IncrementalAnchor(run=_make_run(), chunk_size=5, exclude_keys={"email"})
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=fake_submit):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in rows:
                    anchor.record(row)

        expected_chain = compute_chain_step(None, chunk0_hash_no_pii)
        assert captured["commit_hash"] == expected_chain

    def test_state_advances_after_successful_submit(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        fake_tx = "G" * 64
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)
        assert anchor.seq == 1
        assert anchor._prev_tx_hash == fake_tx
        assert anchor.tx_hashes == [fake_tx]
        assert len(anchor._buffer) == 0

    def test_summary_updated_with_tx_hash(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        fake_tx = "H" * 64
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)
        assert fake_tx in run.summary.get("xrpl_checkpoint_txs", [])

    def test_missing_seed_returns_none(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch.dict("os.environ", {}, clear=True):
            for row in _make_rows(4):
                anchor.record(row)
            result = anchor.record({"step": 4, "loss": 0.1})
        assert result is None
        assert "xrpl_anchor_error" in run.summary

    def test_submit_failure_returns_none(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=Exception("net error")):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)
        assert anchor.seq == 0  # 状態は進まない

    def test_submit_failure_does_not_advance_state(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=Exception("net")):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)
        assert anchor._chain_hash is None
        assert anchor._prev_tx_hash is None
        assert anchor.tx_hashes == []

    def test_returns_none_when_closed(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        anchor = IncrementalAnchor(run=_make_run(), chunk_size=5)
        anchor._closed = True
        result = anchor.record({"step": 0})
        assert result is None

    def test_chunk_hashes_populated_on_success(self):
        """チェックポイント成功時に chunk_hashes が蓄積される。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        rows = _make_rows(5)
        fake_tx = "E" * 64

        anchor = IncrementalAnchor(run=run, chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in rows:
                    anchor.record(row)

        assert len(anchor.chunk_hashes) == 1
        # chunk_hash は canonicalize(rows) の SHA-256 と一致する
        expected = compute_hash(canonicalize(rows))
        assert anchor.chunk_hashes[0] == expected

    def test_chunk_hashes_len_matches_tx_hashes(self):
        """chunk_hashes と tx_hashes の長さが常に一致する。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        call_count = 0

        def fake_submit(**kwargs):
            nonlocal call_count
            call_count += 1
            return "F" * 64

        anchor = IncrementalAnchor(run=run, chunk_size=3)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=fake_submit):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(9):  # 3 チェックポイント
                    anchor.record(row)

        assert len(anchor.chunk_hashes) == len(anchor.tx_hashes) == 3

    def test_chunk_hashes_empty_on_submit_failure(self):
        """XRPL 送信失敗時は chunk_hashes に追加しない。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        anchor = IncrementalAnchor(run=_make_run(), chunk_size=5)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=Exception("net")):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)
        assert anchor.chunk_hashes == []

    def test_chunk_hashes_consistent_with_chain(self):
        """chunk_hashes[i] から chain_hash を再計算すると on-chain 値と一致する。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        from wandb_xrpl_proof.hash import compute_chain_step
        run = _make_run()
        committed_memos = []

        def fake_submit(**kwargs):
            committed_memos.append(dict(kwargs["memo"]))
            return ("G" * 64) if len(committed_memos) == 1 else ("H" * 64)

        anchor = IncrementalAnchor(run=run, chunk_size=4)
        with patch("wandb_xrpl_proof.incremental.submit_anchor", side_effect=fake_submit):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(8):  # 2 チェックポイント
                    anchor.record(row)

        chunk_hashes = anchor.chunk_hashes
        assert len(chunk_hashes) == 2

        rolling = None
        for i, memo in enumerate(committed_memos):
            rolling = compute_chain_step(rolling, chunk_hashes[i])
            assert rolling == memo["commit_hash"], f"chain mismatch at seq={i}"


# ---------------------------------------------------------------------------
# TestLog
# ---------------------------------------------------------------------------

class TestLog:
    def test_log_calls_wandb_log(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        anchor = IncrementalAnchor(run=_make_run(), chunk_size=10)
        with patch("wandb_xrpl_proof.incremental.wandb") as mock_wandb:
            anchor.log({"loss": 0.5}, step=1)
            mock_wandb.log.assert_called_once_with({"loss": 0.5}, step=1)

    def test_log_returns_same_as_record(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        fake_tx = "I" * 64
        with patch("wandb_xrpl_proof.incremental.wandb"):
            with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
                with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                    for i in range(4):
                        anchor.log({"step": i})
                    result = anchor.log({"step": 4})
        assert result == fake_tx


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_flushes_partial_chunk(self):
        """chunk_size 未満の残余行も close() でフラッシュされる。"""
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=10)
        # 3 行だけ追加（chunk_size=10 未満）
        for row in _make_rows(3):
            anchor.record(row)
        fake_tx = "J" * 64
        with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                result = anchor.close()
        assert result == fake_tx
        assert anchor.tx_hashes == [fake_tx]

    def test_close_with_empty_buffer_returns_none(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        anchor = IncrementalAnchor(run=_make_run(), chunk_size=10)
        result = anchor.close()
        assert result is None

    def test_close_writes_checkpoint_txs_to_summary(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        run = _make_run()
        anchor = IncrementalAnchor(run=run, chunk_size=5)
        fake_tx = "K" * 64
        with patch("wandb_xrpl_proof.incremental.submit_anchor", return_value=fake_tx):
            with patch.dict("os.environ", {"XRPL_WALLET_SEED": "sTest"}):
                for row in _make_rows(5):
                    anchor.record(row)
                anchor.close()
        assert run.summary["xrpl_checkpoint_txs"] == [fake_tx]

    def test_close_is_idempotent(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        anchor = IncrementalAnchor(run=_make_run(), chunk_size=10)
        anchor.close()
        anchor.close()  # 2 回目も例外にならない


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------

class TestContextManager:
    def test_enter_returns_self(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        anchor = IncrementalAnchor(run=_make_run(), chunk_size=10)
        assert anchor.__enter__() is anchor
        anchor.close()

    def test_exit_calls_close(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        with patch.object(IncrementalAnchor, "close") as mock_close:
            with IncrementalAnchor(run=_make_run(), chunk_size=10):
                pass
        mock_close.assert_called_once()

    def test_exception_in_block_still_calls_close(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        with patch.object(IncrementalAnchor, "close") as mock_close:
            with pytest.raises(RuntimeError):
                with IncrementalAnchor(run=_make_run(), chunk_size=10):
                    raise RuntimeError("training failed")
        mock_close.assert_called_once()

    def test_exception_is_not_suppressed(self):
        from wandb_xrpl_proof.incremental import IncrementalAnchor
        with pytest.raises(RuntimeError, match="training failed"):
            with IncrementalAnchor(run=_make_run(), chunk_size=10):
                raise RuntimeError("training failed")


# ---------------------------------------------------------------------------
# TestVerifyChain
# ---------------------------------------------------------------------------

def _build_fake_chain(n: int, run_path: str = "ent/proj/run1") -> tuple[list[str], list[dict]]:
    """n チェックポイントの (tx_hashes, memos) を生成するヘルパー。"""
    tx_hashes = [f"{i:0>64}" for i in range(n)]
    memos = []
    for seq in range(n):
        memo = {
            "schema_version": "wandb-xrpl-proof-0.2",
            "commit_hash": "a" * 64,
            "seq": seq,
        }
        if seq == 0:
            memo["wandb_run_path"] = run_path
        else:
            memo["prev"] = tx_hashes[seq - 1]
        memos.append(memo)
    return tx_hashes, memos


def _make_fetch_side_effect(tx_hashes, memos):
    """fetch_transaction のモックを返す（XRPL のレスポンス形式）。"""
    mapping = {
        tx: {"tx_json": {"Memos": [{"Memo": {"MemoData": _memo_to_hex(m)}}]}}
        for tx, m in zip(tx_hashes, memos)
    }

    def side_effect(tx_hash, **_):
        if tx_hash not in mapping:
            raise KeyError(f"Unknown tx_hash: {tx_hash}")
        return mapping[tx_hash]

    return side_effect


class TestVerifyChain:
    def test_single_checkpoint_verified(self):
        from wandb_xrpl_proof.verify import verify_chain
        tx_hashes, memos = _build_fake_chain(1)
        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[0])
        assert len(results) == 1
        assert results[0].verified is True
        assert results[0].wandb_run_path == "ent/proj/run1"
        assert results[0].tx_hash == tx_hashes[0]

    def test_multi_checkpoint_all_verified(self):
        from wandb_xrpl_proof.verify import verify_chain
        n = 4
        tx_hashes, memos = _build_fake_chain(n)
        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[-1])
        assert len(results) == n
        assert all(r.verified for r in results)
        # 時系列順（seq=0 が先頭）
        for i, r in enumerate(results):
            assert r.tx_hash == tx_hashes[i]

    def test_all_results_share_run_path_from_genesis(self):
        from wandb_xrpl_proof.verify import verify_chain
        tx_hashes, memos = _build_fake_chain(3, run_path="my-org/proj/abc")
        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[-1])
        assert all(r.wandb_run_path == "my-org/proj/abc" for r in results)

    def test_tampered_prev_link_fails(self):
        """prev 改ざんで chain walk が途中で止まり、chain 全体が verified=False になる。"""
        from wandb_xrpl_proof.verify import verify_chain
        tx_hashes, memos = _build_fake_chain(3)
        # seq=2 の prev を存在しない tx_hash に改ざん
        memos[2]["prev"] = "bad" + "0" * 61
        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[-1])
        # prev を辿った先がフェッチできないため chain walk が途中で終わる
        # → 収集できたチェックポイントが 1 件だけ、かつ検証失敗
        assert len(results) >= 1
        assert not all(r.verified for r in results)

    def test_missing_seq_field_stops_and_fails(self):
        from wandb_xrpl_proof.verify import verify_chain
        bad_memo = {"schema_version": "wandb-xrpl-proof-0.2", "commit_hash": "a" * 64}
        bad_tx = "E" * 64

        def fake_fetch(tx_hash, **_):
            return {"tx_json": {"Memos": [{"Memo": {"MemoData": _memo_to_hex(bad_memo)}}]}}

        with patch("wandb_xrpl_proof.verify.fetch_transaction", side_effect=fake_fetch):
            results = verify_chain(bad_tx)

        assert len(results) == 1
        assert not results[0].verified

    def test_hash_chain_verification_with_chunk_hashes(self):
        """chunk_hashes 指定時に hash chain が正しく再計算される。"""
        from wandb_xrpl_proof.verify import verify_chain

        # チャンクハッシュを用意
        chunk_hashes = ["aa" * 32, "bb" * 32, "cc" * 32]
        # 正しい commit_hash を事前計算
        h = None
        expected_commit_hashes = []
        for ch in chunk_hashes:
            h = compute_chain_step(h, ch)
            expected_commit_hashes.append(h)

        tx_hashes, memos = _build_fake_chain(3)
        for i, memo in enumerate(memos):
            memo["commit_hash"] = expected_commit_hashes[i]

        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[-1], chunk_hashes=chunk_hashes)

        assert all(r.verified for r in results)
        for i, r in enumerate(results):
            assert r.commit_hash_computed == expected_commit_hashes[i]

    def test_hash_chain_mismatch_detected(self):
        """commit_hash が期待値と異なる場合に verified=False になる。"""
        from wandb_xrpl_proof.verify import verify_chain

        chunk_hashes = ["aa" * 32, "bb" * 32]
        tx_hashes, memos = _build_fake_chain(2)
        # seq=1 の commit_hash を改ざん
        memos[1]["commit_hash"] = "ff" * 32

        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[-1], chunk_hashes=chunk_hashes)

        assert not results[1].verified
        assert any("hash chain" in e for e in results[1].errors)

    def test_no_chunk_hashes_structure_only(self):
        """chunk_hashes 未指定時は構造検証のみ (commit_hash_computed は空文字)。"""
        from wandb_xrpl_proof.verify import verify_chain
        tx_hashes, memos = _build_fake_chain(2)
        with patch("wandb_xrpl_proof.verify.fetch_transaction",
                   side_effect=_make_fetch_side_effect(tx_hashes, memos)):
            results = verify_chain(tx_hashes[-1])
        assert all(r.commit_hash_computed == "" for r in results)
        assert all(r.verified for r in results)
