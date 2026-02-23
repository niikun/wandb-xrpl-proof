"""
Canonicalization module for wandb-xrpl-proof.

Implements Section 5 of WandB_XRPL_Anchor_Spec_v0_2:
- JSON keys sorted lexicographically
- UTF-8 encoding
- No whitespace (separators: "," and ":" only)
- Unstable fields excluded
"""

import json

# 仕様 Section 5: 除外すべき不安定フィールド
_UNSTABLE_FIELDS = frozenset({"_timestamp", "_runtime"})


def canonicalize(obj: dict, exclude_keys: set[str] | None = None) -> str:
    """
    辞書を正規化 JSON 文字列に変換する。

    Args:
        obj: 正規化する辞書
        exclude_keys: 追加で除外するキー (secrets 等)

    Returns:
        正規化された JSON 文字列 (UTF-8, キーソート済み, 空白なし)
    """
    excluded = _UNSTABLE_FIELDS | (exclude_keys or set())
    filtered = _filter_recursive(obj, excluded)
    return json.dumps(filtered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _filter_recursive(obj: object, exclude_keys: frozenset[str] | set[str]) -> object:
    """再帰的に不安定フィールドを除外する。"""
    if isinstance(obj, dict):
        return {
            k: _filter_recursive(v, exclude_keys)
            for k, v in obj.items()
            if k not in exclude_keys
        }
    if isinstance(obj, list):
        return [_filter_recursive(item, exclude_keys) for item in obj]
    return obj
