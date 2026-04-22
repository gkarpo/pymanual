from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the cache to a per-test tmp dir so tests never touch the user's real cache."""
    monkeypatch.setenv("PYMANUAL_CACHE_DIR", str(tmp_path / "cache"))
    yield
