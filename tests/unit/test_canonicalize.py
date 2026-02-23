"""Unit tests for canonicalize module (Section 5)."""

import json

import pytest

from wandb_xrpl_proof.canonicalize import canonicalize


class TestKeyOrdering:
    def test_keys_sorted_lexicographically(self):
        obj = {"z": 1, "a": 2, "m": 3}
        result = canonicalize(obj)
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["a", "m", "z"]

    def test_nested_keys_sorted(self):
        obj = {"outer": {"z": 1, "a": 2}}
        result = canonicalize(obj)
        parsed = json.loads(result)
        assert list(parsed["outer"].keys()) == ["a", "z"]


class TestWhitespace:
    def test_no_whitespace_in_output(self):
        obj = {"key": "value", "num": 42}
        result = canonicalize(obj)
        assert " " not in result
        assert "\n" not in result
        assert "\t" not in result

    def test_uses_compact_separators(self):
        obj = {"a": 1, "b": 2}
        result = canonicalize(obj)
        assert result == '{"a":1,"b":2}'


class TestUnstableFieldExclusion:
    def test_excludes_timestamp(self):
        obj = {"_timestamp": 1234567890, "value": 42}
        result = canonicalize(obj)
        parsed = json.loads(result)
        assert "_timestamp" not in parsed
        assert parsed["value"] == 42

    def test_excludes_runtime(self):
        obj = {"_runtime": 3.14, "model": "gpt"}
        result = canonicalize(obj)
        parsed = json.loads(result)
        assert "_runtime" not in parsed

    def test_excludes_custom_keys(self):
        obj = {"secret_key": "abc123", "data": "hello"}
        result = canonicalize(obj, exclude_keys={"secret_key"})
        parsed = json.loads(result)
        assert "secret_key" not in parsed
        assert parsed["data"] == "hello"

    def test_nested_unstable_fields_excluded(self):
        obj = {"summary": {"loss": 0.5, "_timestamp": 1234}}
        result = canonicalize(obj)
        parsed = json.loads(result)
        assert "_timestamp" not in parsed["summary"]
        assert parsed["summary"]["loss"] == 0.5


class TestDeterminism:
    def test_same_input_same_output(self):
        obj = {"b": [1, 2, 3], "a": {"x": 1, "y": 2}}
        assert canonicalize(obj) == canonicalize(obj)

    def test_different_insertion_order_same_output(self):
        obj1 = {"a": 1, "b": 2}
        obj2 = {"b": 2, "a": 1}
        assert canonicalize(obj1) == canonicalize(obj2)


class TestDataTypes:
    def test_integer(self):
        result = canonicalize({"n": 42})
        assert json.loads(result)["n"] == 42

    def test_float(self):
        result = canonicalize({"f": 3.14})
        assert json.loads(result)["f"] == 3.14

    def test_string(self):
        result = canonicalize({"s": "hello"})
        assert json.loads(result)["s"] == "hello"

    def test_list(self):
        result = canonicalize({"l": [1, 2, 3]})
        assert json.loads(result)["l"] == [1, 2, 3]

    def test_null(self):
        result = canonicalize({"n": None})
        assert json.loads(result)["n"] is None

    def test_boolean(self):
        result = canonicalize({"t": True, "f": False})
        parsed = json.loads(result)
        assert parsed["t"] is True
        assert parsed["f"] is False

    def test_unicode_preserved(self):
        obj = {"name": "テスト"}
        result = canonicalize(obj)
        parsed = json.loads(result)
        assert parsed["name"] == "テスト"
