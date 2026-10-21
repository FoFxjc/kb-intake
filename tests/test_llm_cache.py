"""llm_cache: content-addressed JSON files, atomic writes, corrupt files read as misses."""

import json
import threading

import pytest

from kb_intake.llm_cache import Cache, cache_key

INPUT = "a" * 64


def test_key_is_deterministic_and_depends_on_every_part():
    k = cache_key("openai_compatible", "MiniMax-M3", "describe-1", INPUT)
    assert k == cache_key("openai_compatible", "MiniMax-M3", "describe-1", INPUT)
    assert len(k) == 64 and all(c in "0123456789abcdef" for c in k)
    others = {cache_key("other", "MiniMax-M3", "describe-1", INPUT),
              cache_key("openai_compatible", "other", "describe-1", INPUT),
              cache_key("openai_compatible", "MiniMax-M3", "describe-2", INPUT),
              cache_key("openai_compatible", "MiniMax-M3", "describe-1", "b" * 64)}
    assert k not in others and len(others) == 4


def test_parts_cannot_be_shifted_across_fields():
    assert cache_key("a", "bc", "d", INPUT) != cache_key("ab", "c", "d", INPUT)


def test_round_trip(tmp_path):
    cache = Cache(tmp_path / "llm")
    key = cache_key("p", "m", "v", INPUT)
    assert cache.get(key) is None
    value = {"text": "A bar chart.", "usage": {"total_tokens": 3}, "n": 1.5}
    cache.put(key, value)
    assert cache.get(key) == value
    assert (tmp_path / "llm" / key[:2] / f"{key}.json").is_file()
    assert [p.name for p in (tmp_path / "llm" / key[:2]).iterdir()] == [f"{key}.json"]  # no temp file left


def test_put_overwrites(tmp_path):
    cache = Cache(tmp_path)
    key = cache_key("p", "m", "v", INPUT)
    cache.put(key, {"text": "old"})
    cache.put(key, {"text": "new"})
    assert cache.get(key) == {"text": "new"}


@pytest.mark.parametrize("data", [b"{not json", b"", b"[1, 2]", b"\xff\xfe"])
def test_corrupt_file_is_a_miss(tmp_path, data):
    cache = Cache(tmp_path)
    key = cache_key("p", "m", "v", INPUT)
    path = tmp_path / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert cache.get(key) is None


@pytest.mark.parametrize("key", ["", "../x", "ab/cd", "ABCDEF"])
def test_bad_keys_are_refused(tmp_path, key):
    with pytest.raises(ValueError):
        Cache(tmp_path).get(key)
    with pytest.raises(ValueError):
        Cache(tmp_path).put(key, {})


def test_concurrent_put_leaves_one_valid_file(tmp_path):
    cache = Cache(tmp_path)
    key = cache_key("p", "m", "v", INPUT)
    big = "x" * 200_000
    barrier = threading.Barrier(8)
    errors = []

    def put(i):
        try:
            barrier.wait()
            cache.put(key, {"i": i, "pad": big})
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=put, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    files = list((tmp_path / key[:2]).iterdir())
    assert [p.name for p in files] == [f"{key}.json"]
    value = json.loads(files[0].read_text(encoding="utf-8"))
    assert value["i"] in range(8) and value["pad"] == big
    assert cache.get(key) == value
