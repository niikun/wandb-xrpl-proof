"""Unit tests for merkle module (Section 7)."""

import hashlib

import pytest

from wandb_xrpl_proof.canonicalize import canonicalize
from wandb_xrpl_proof.hash import compute_hash
from wandb_xrpl_proof.merkle import (
    DEFAULT_CHUNK_SIZE,
    _compute_merkle_root,
    build_merkle_tree,
    split_history,
)


class TestBuildMerkleTree:
    def test_returns_history_root_and_chunk_count(self):
        chunks = [{"step": 0, "loss": 0.5}, {"step": 1, "loss": 0.4}]
        result = build_merkle_tree(chunks)
        assert "history_root" in result
        assert "chunk_count" in result
        assert result["chunk_count"] == 2

    def test_single_chunk_root_equals_leaf_hash(self):
        chunks = [{"step": 0, "loss": 0.5}]
        result = build_merkle_tree(chunks)
        expected_leaf = compute_hash(canonicalize(chunks[0]))
        assert result["history_root"] == expected_leaf

    def test_empty_chunks_raises(self):
        with pytest.raises(ValueError, match="empty"):
            build_merkle_tree([])

    def test_root_is_lowercase_hex_64_chars(self):
        chunks = [{"a": 1}, {"b": 2}]
        result = build_merkle_tree(chunks)
        root = result["history_root"]
        assert len(root) == 64
        assert root == root.lower()
        assert all(c in "0123456789abcdef" for c in root)

    def test_deterministic(self):
        chunks = [{"step": i, "loss": i * 0.1} for i in range(5)]
        r1 = build_merkle_tree(chunks)
        r2 = build_merkle_tree(chunks)
        assert r1 == r2


class TestOddLeafHandling:
    def test_odd_number_of_chunks(self):
        chunks = [{"step": i} for i in range(3)]
        result = build_merkle_tree(chunks)
        assert result["chunk_count"] == 3
        # ルートが計算できること (奇数でも例外にならない)
        assert len(result["history_root"]) == 64

    def test_odd_vs_even_chunks_different_root(self):
        chunks_3 = [{"step": i} for i in range(3)]
        chunks_4 = [{"step": i} for i in range(4)]
        r3 = build_merkle_tree(chunks_3)
        r4 = build_merkle_tree(chunks_4)
        assert r3["history_root"] != r4["history_root"]


class TestComputeMerkleRoot:
    def test_two_leaves(self):
        h1 = "a" * 64
        h2 = "b" * 64
        combined = bytes.fromhex(h1) + bytes.fromhex(h2)
        expected = hashlib.sha256(combined).hexdigest()
        assert _compute_merkle_root([h1, h2]) == expected

    def test_single_leaf_returns_itself(self):
        leaf = "ab" * 32  # 64 hex chars
        assert _compute_merkle_root([leaf]) == leaf

    def test_odd_leaves_duplicates_last(self):
        h1 = "aa" * 32
        h2 = "bb" * 32
        h3 = "cc" * 32

        # 奇数 → h3 複製後 [h1,h2,h3,h3]
        p1 = hashlib.sha256(bytes.fromhex(h1) + bytes.fromhex(h2)).hexdigest()
        p2 = hashlib.sha256(bytes.fromhex(h3) + bytes.fromhex(h3)).hexdigest()
        expected = hashlib.sha256(bytes.fromhex(p1) + bytes.fromhex(p2)).hexdigest()

        assert _compute_merkle_root([h1, h2, h3]) == expected


class TestSplitHistory:
    def test_default_chunk_size(self):
        history = [{"step": i} for i in range(2500)]
        chunks = split_history(history)
        assert len(chunks) == 3
        assert len(chunks[0]) == DEFAULT_CHUNK_SIZE
        assert len(chunks[1]) == DEFAULT_CHUNK_SIZE
        assert len(chunks[2]) == 500

    def test_custom_chunk_size(self):
        history = [{"step": i} for i in range(10)]
        chunks = split_history(history, chunk_size=3)
        assert len(chunks) == 4
        assert len(chunks[-1]) == 1

    def test_empty_history(self):
        assert split_history([]) == []

    def test_exactly_one_chunk(self):
        history = [{"step": i} for i in range(5)]
        chunks = split_history(history, chunk_size=10)
        assert len(chunks) == 1
        assert chunks[0] == history
