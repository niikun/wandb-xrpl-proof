"""Unit tests for hash module (Section 6)."""

import hashlib

import pytest

from wandb_xrpl_proof.hash import compute_hash


class TestHashFormat:
    def test_output_is_lowercase_hex(self):
        result = compute_hash('{"key":"value"}')
        assert result == result.lower()
        assert all(c in "0123456789abcdef" for c in result)

    def test_output_length_is_64_chars(self):
        result = compute_hash('{"key":"value"}')
        assert len(result) == 64

    def test_sha256_algorithm(self):
        canonical = '{"a":1,"b":2}'
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert compute_hash(canonical) == expected


class TestDeterminism:
    def test_same_input_same_hash(self):
        canonical = '{"schema_version":"wandb-xrpl-proof-0.2"}'
        assert compute_hash(canonical) == compute_hash(canonical)

    def test_different_input_different_hash(self):
        h1 = compute_hash('{"a":1}')
        h2 = compute_hash('{"a":2}')
        assert h1 != h2


class TestKnownValues:
    def test_empty_object(self):
        result = compute_hash("{}")
        expected = hashlib.sha256(b"{}").hexdigest()
        assert result == expected

    def test_spec_example(self):
        # 仕様の正規化サンプルを使った確認
        canonical = '{"schema_version":"wandb-xrpl-proof-0.2","wandb_run_path":"entity/project/run_id","weave_op_name":"my_func"}'
        result = compute_hash(canonical)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert result == expected
        assert len(result) == 64
