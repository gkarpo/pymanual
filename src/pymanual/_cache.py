"""On-disk LRU cache of resolved documentation URLs.

Stored as a JSON object in the user's XDG cache directory (overridable via
the ``PYMANUAL_CACHE_DIR`` environment variable). Insertion order in the
JSON file doubles as the LRU recency signal — the oldest entry is at the
start and gets evicted once the cache grows past ``MAX_ENTRIES``. Reads and
writes silently tolerate a missing, unreadable, or malformed file, because
a broken cache should never break resolution.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ENV_VAR = "PYMANUAL_CACHE_DIR"
MAX_ENTRIES = 100


def cache_dir() -> Path:
    """Directory used to store the cache file.

    Resolution order: ``PYMANUAL_CACHE_DIR`` env var → ``XDG_CACHE_HOME`` →
    ``~/.cache``; the ``pymanual`` subdirectory is appended in all cases.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pymanual"


def cache_path() -> Path:
    """Full path to the JSON cache file inside :func:`cache_dir`."""
    return cache_dir() / "cache.json"


def lookup(name: str) -> str | None:
    """Return the cached URL for ``name`` or ``None`` if absent."""
    return _read().get(name)


def store(name: str, url: str) -> None:
    """Insert or refresh ``name``'s entry, evicting the oldest if over cap."""
    data = _read()
    # Fast path: if the entry already exists with the same value AND is
    # already the most-recent (last) key, skip the rewrite — the on-disk
    # ordering and value are both already correct.
    if data.get(name) == url and data and next(reversed(data)) == name:
        return
    data.pop(name, None)
    data[name] = url
    while len(data) > MAX_ENTRIES:
        del data[next(iter(data))]
    _write(data)


def clear() -> bool:
    """Delete the cache file. Returns True if a file was removed."""
    try:
        cache_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _read() -> dict[str, str]:
    try:
        with cache_path().open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Defensive filter: a hand-edited or corrupted file could contain
    # non-string entries, which would blow up downstream consumers.
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _write(data: dict[str, str]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a sibling tempfile then os.replace() into place
    # so a crash mid-write can't leave a truncated or partially-written file.
    fd, tmp = tempfile.mkstemp(prefix=".cache.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
