"""Resolve documentation URLs for installed distributions and PyPI projects.

Two entry points:

* :func:`resolve_third_party_url` — for modules we know are importable and
  classified as third-party. Always returns a URL (falls back to the generic
  PyPI project page).
* :func:`resolve_distribution_url` — for names that aren't importable locally
  but might still exist on PyPI. Returns ``None`` if nothing matches.

Both prefer installed distribution metadata (``Project-URL`` entries) over
the PyPI JSON API, since installed metadata is cheap and doesn't hit the
network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from importlib import metadata
from typing import Any, Iterable

_PYPI_JSON = "https://pypi.org/pypi/{name}/json"
_PYPI_PROJECT = "https://pypi.org/project/{name}/"
_PYPI_TIMEOUT = 5.0

# Label-substring preference order when picking a ``Project-URL``. Matches
# are case-insensitive substrings, so "Documentation" and "API docs" both
# hit the "doc*" tier before we fall back to a homepage-like entry.
_DOC_KEY_PREFERENCE = ("documentation", "docs", "doc")
_HOME_KEY_PREFERENCE = ("homepage", "home")


def resolve_third_party_url(module_name: str, *, network: bool = True) -> str:
    """Return a docs URL for a third-party ``module_name``; never ``None``.

    Maps the import name to a distribution name via
    ``importlib.metadata.packages_distributions``, then checks installed
    metadata and (optionally) the PyPI JSON API. Falls back to the generic
    ``pypi.org/project/<name>/`` page so the caller always gets *something*.
    """
    top_level = module_name.split(".", 1)[0]
    dist_name = _resolve_distribution_name(top_level)

    url = _from_installed_metadata(dist_name)
    if url:
        return url

    if network:
        info = _fetch_pypi_info(dist_name)
        if info is not None:
            url = _url_from_pypi_info(info)
            if url:
                return url

    return _PYPI_PROJECT.format(name=dist_name)


def resolve_distribution_url(dist_name: str, *, network: bool = True) -> str | None:
    """Resolve a docs URL for a name assumed to be a distribution (PyPI) name.

    Tries the input as-is, then falls back to its top-level segment so that
    dotted module names like ``playwright.sync_api`` still resolve via the
    ``playwright`` distribution.

    Returns None when no candidate is installed locally and (with network
    enabled) none can be found on PyPI.
    """
    candidates = [dist_name]
    top_level = dist_name.split(".", 1)[0]
    if top_level and top_level != dist_name:
        candidates.append(top_level)

    for candidate in candidates:
        url = _try_distribution(candidate, network=network)
        if url is not None:
            return url
    return None


def _try_distribution(dist_name: str, *, network: bool) -> str | None:
    try:
        meta = metadata.metadata(dist_name)
    except metadata.PackageNotFoundError:
        meta = None

    if meta is not None:
        url = _url_from_metadata(meta)
        return url or _PYPI_PROJECT.format(name=dist_name)

    if network:
        info = _fetch_pypi_info(dist_name)
        if info is not None:
            url = _url_from_pypi_info(info)
            return url or _PYPI_PROJECT.format(name=dist_name)

    return None


def _resolve_distribution_name(top_level: str) -> str:
    try:
        mapping = metadata.packages_distributions()
    except Exception:
        mapping = {}
    dists = mapping.get(top_level)
    if dists:
        return dists[0]
    return top_level


def _from_installed_metadata(dist_name: str) -> str | None:
    try:
        meta = metadata.metadata(dist_name)
    except metadata.PackageNotFoundError:
        return None
    return _url_from_metadata(meta)


def _url_from_metadata(meta: Any) -> str | None:
    project_urls = meta.get_all("Project-URL") or []
    parsed = _parse_project_urls(project_urls)
    url = _pick_best(parsed)
    if url:
        return url

    home = meta.get("Home-page")
    if home and home.strip() and home.strip().lower() != "unknown":
        return home.strip()
    return None


def _fetch_pypi_info(dist_name: str) -> dict[str, Any] | None:
    """Fetch the `info` dict for a package from PyPI. Returns None on 404 or network error."""
    url = _PYPI_JSON.format(name=dist_name)
    try:
        with urllib.request.urlopen(url, timeout=_PYPI_TIMEOUT) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    info = data.get("info")
    return info if isinstance(info, dict) else None


def _url_from_pypi_info(info: dict[str, Any]) -> str | None:
    project_urls = info.get("project_urls") or {}
    parsed = [(k, v) for k, v in project_urls.items() if v]
    best = _pick_best(parsed)
    if best:
        return best

    home = info.get("home_page")
    if home and str(home).strip() and str(home).strip().lower() != "unknown":
        return str(home).strip()
    return None


type LabelURLpairs = list[tuple[str, str]]


def _parse_project_urls(entries: Iterable[str]) -> LabelURLpairs:
    out: LabelURLpairs = []
    for entry in entries:
        if "," not in entry:
            continue
        label, _, url = entry.partition(",")
        label = label.strip()
        url = url.strip()
        if label and url:
            out.append((label, url))
    return out


def _pick_best(pairs: LabelURLpairs) -> str | None:
    # Preference: anything doc-shaped beats anything homepage-shaped beats
    # "the first entry we happened to see". The outer loop is over preference
    # tiers (not labels) so that a later label matching a stronger tier wins
    # over an earlier label in a weaker tier.
    if not pairs:
        return None
    lowered = [(label.lower(), url) for label, url in pairs]

    for pref in _DOC_KEY_PREFERENCE:
        for label, url in lowered:
            if pref in label:
                return url

    for pref in _HOME_KEY_PREFERENCE:
        for label, url in lowered:
            if pref in label:
                return url

    return lowered[0][1]


# Backwards-compatible shim kept for existing tests; delegates to the split helpers.
def _from_pypi(dist_name: str) -> str | None:
    info = _fetch_pypi_info(dist_name)
    if info is None:
        return None
    return _url_from_pypi_info(info)
