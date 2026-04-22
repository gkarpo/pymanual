from __future__ import annotations

import json


from pymanual import _cache


def test_lookup_miss_returns_none():
    assert _cache.lookup("nothing-here") is None


def test_store_then_lookup_roundtrip():
    _cache.store("json", "https://docs.python.org/3/library/json.html")
    assert _cache.lookup("json") == "https://docs.python.org/3/library/json.html"


def test_store_persists_across_processes_via_file():
    _cache.store("foo", "https://foo.example/")
    raw = _cache.cache_path().read_text(encoding="utf-8")
    assert json.loads(raw) == {"foo": "https://foo.example/"}


def test_store_overwrites_existing_entry():
    _cache.store("foo", "https://old/")
    _cache.store("foo", "https://new/")
    assert _cache.lookup("foo") == "https://new/"


def test_store_does_not_rewrite_when_value_unchanged():
    _cache.store("foo", "https://foo/")
    mtime_before = _cache.cache_path().stat().st_mtime_ns
    _cache.store("foo", "https://foo/")
    assert _cache.cache_path().stat().st_mtime_ns == mtime_before


def test_clear_removes_file():
    _cache.store("foo", "https://foo/")
    assert _cache.cache_path().exists()
    assert _cache.clear() is True
    assert not _cache.cache_path().exists()


def test_clear_when_missing_returns_false():
    assert not _cache.cache_path().exists()
    assert _cache.clear() is False


def test_corrupt_cache_treated_as_empty():
    path = _cache.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json {", encoding="utf-8")
    assert _cache.lookup("anything") is None
    # Subsequent store overwrites cleanly
    _cache.store("foo", "https://foo/")
    assert _cache.lookup("foo") == "https://foo/"


def test_non_dict_cache_treated_as_empty():
    path = _cache.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert _cache.lookup("anything") is None


def test_cache_dir_respects_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere"
    monkeypatch.setenv(_cache.ENV_VAR, str(custom))
    assert _cache.cache_dir() == custom
    assert _cache.cache_path() == custom / "cache.json"


def test_cache_dir_falls_back_to_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.delenv(_cache.ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert _cache.cache_dir() == tmp_path / "pymanual"


def test_cache_dir_falls_back_to_dot_cache(tmp_path, monkeypatch):
    monkeypatch.delenv(_cache.ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _cache.cache_dir() == tmp_path / ".cache" / "pymanual"


def test_insertion_order_preserved_on_disk():
    _cache.store("first", "https://1/")
    _cache.store("second", "https://2/")
    _cache.store("third", "https://3/")
    raw = _cache.cache_path().read_text(encoding="utf-8")
    assert raw.index('"first"') < raw.index('"second"') < raw.index('"third"')


def test_restore_with_new_url_moves_key_to_end():
    _cache.store("first", "https://1/")
    _cache.store("second", "https://2/")
    _cache.store("first", "https://1-updated/")   # update bumps to end
    assert list(_cache._read()) == ["second", "first"]
    assert _cache.lookup("first") == "https://1-updated/"


def test_restore_same_url_but_not_last_bumps_to_end():
    _cache.store("first", "https://1/")
    _cache.store("second", "https://2/")
    _cache.store("first", "https://1/")           # same value, not last
    assert list(_cache._read()) == ["second", "first"]


def test_restore_same_url_when_already_last_is_noop():
    _cache.store("first", "https://1/")
    _cache.store("second", "https://2/")
    mtime_before = _cache.cache_path().stat().st_mtime_ns
    _cache.store("second", "https://2/")          # already last with same URL
    assert _cache.cache_path().stat().st_mtime_ns == mtime_before


def test_eviction_drops_oldest_when_cap_exceeded(monkeypatch):
    monkeypatch.setattr(_cache, "MAX_ENTRIES", 3)
    _cache.store("a", "https://a/")
    _cache.store("b", "https://b/")
    _cache.store("c", "https://c/")
    _cache.store("d", "https://d/")  # should evict "a"
    _cache.store("e", "https://e/")  # should evict "b"
    assert list(_cache._read()) == ["c", "d", "e"]
    assert _cache.lookup("a") is None
    assert _cache.lookup("b") is None


def test_bumping_existing_entry_does_not_evict_anything(monkeypatch):
    monkeypatch.setattr(_cache, "MAX_ENTRIES", 3)
    _cache.store("a", "https://a/")
    _cache.store("b", "https://b/")
    _cache.store("c", "https://c/")
    _cache.store("a", "https://a-new/")           # bump, not add
    assert list(_cache._read()) == ["b", "c", "a"]
    assert _cache.lookup("b") == "https://b/"     # nothing was evicted
