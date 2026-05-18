"""LLMCache key stability and round-trip."""

from __future__ import annotations

from pathlib import Path

from signalyze.storage.llm_cache import LLMCache


def test_make_key_is_stable_across_whitespace(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path / "cache.sqlite")
    a = cache.make_key("gpt-4o-mini", "v1", "Hello   World\n")
    b = cache.make_key("gpt-4o-mini", "v1", "hello world")
    assert a == b


def test_get_set_roundtrip(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path / "cache.sqlite")
    key = cache.make_key("gpt-4o-mini", "v1", "test")
    assert cache.get(key) is None
    cache.set(
        key,
        model="gpt-4o-mini",
        prompt_version="v1",
        content_hash="abc",
        response={"result": 42},
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0001,
    )
    cached = cache.get(key)
    assert cached == {"result": 42}
    assert cache.total_cost_usd() > 0
