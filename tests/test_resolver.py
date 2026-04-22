from __future__ import annotations

import sys
from unittest import mock


from pymanual import (
    STATUS_LOCAL,
    STATUS_NOT_FOUND,
    STATUS_UNRESOLVABLE_PATH,
    resolve_doc_url,
)


def test_stdlib_module_returns_python_docs_url():
    assert resolve_doc_url("json") == "https://docs.python.org/3/library/json.html"


def test_stdlib_dotted_uses_top_level():
    assert resolve_doc_url("email.mime.text") == "https://docs.python.org/3/library/email.html"


def test_builtin_module_treated_as_stdlib():
    assert resolve_doc_url("sys") == "https://docs.python.org/3/library/sys.html"


def test_unknown_module_returns_not_found():
    assert resolve_doc_url(
        "definitely_no_such_module_xyz_123", network=False
    ) == STATUS_NOT_FOUND


def test_distribution_name_with_dash_resolves_via_metadata():
    # pytest-cov is installed as a dev dep; import name is pytest_cov.
    # import_module("pytest-cov") raises ModuleNotFoundError (dashes invalid
    # in module names), so this exercises the distribution-name fallback.
    url = resolve_doc_url("pytest-cov", network=False)
    assert url != STATUS_NOT_FOUND
    assert url.startswith("http")


def test_unknown_distribution_with_network_off_returns_not_found():
    assert resolve_doc_url(
        "definitely-no-such-dist-xyz-123", network=False
    ) == STATUS_NOT_FOUND


def test_third_party_routes_through_metadata_resolver():
    with mock.patch(
        "pymanual._resolver.resolve_third_party_url",
        return_value="https://example.com/docs",
    ) as m:
        url = resolve_doc_url("pytest", network=False)
    assert url == "https://example.com/docs"
    m.assert_called_once_with("pytest", network=False)


def test_local_module_returns_local_status(tmp_path, monkeypatch):
    pkg = tmp_path / "mylocalmod.py"
    pkg.write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    # Ensure a fresh import
    sys.modules.pop("mylocalmod", None)
    try:
        assert resolve_doc_url("mylocalmod") == STATUS_LOCAL
    finally:
        sys.modules.pop("mylocalmod", None)


def test_unresolvable_path_when_getfile_fails():
    fake = object()
    with mock.patch("pymanual._resolver.import_module", return_value=fake):
        assert resolve_doc_url("whatever") == STATUS_UNRESOLVABLE_PATH


def test_cache_hit_short_circuits_pipeline():
    from pymanual import _cache

    _cache.store("anything-at-all", "https://cached.example/")
    with mock.patch("pymanual._resolver._resolve") as resolve_mock:
        url = resolve_doc_url("anything-at-all")
    assert url == "https://cached.example/"
    resolve_mock.assert_not_called()


def test_cache_miss_writes_successful_url():
    from pymanual import _cache

    with mock.patch("pymanual._resolver._resolve", return_value="https://resolved/"):
        url = resolve_doc_url("some-module")
    assert url == "https://resolved/"
    assert _cache.lookup("some-module") == "https://resolved/"


def test_cache_does_not_store_status_tokens():
    from pymanual import _cache

    with mock.patch("pymanual._resolver._resolve", return_value=STATUS_NOT_FOUND):
        url = resolve_doc_url("ghost-module")
    assert url == STATUS_NOT_FOUND
    assert _cache.lookup("ghost-module") is None


def test_use_cache_false_bypasses_read_and_write():
    from pymanual import _cache

    _cache.store("seeded", "https://stale/")
    with mock.patch(
        "pymanual._resolver._resolve", return_value="https://fresh/"
    ) as resolve_mock:
        url = resolve_doc_url("seeded", use_cache=False)
    assert url == "https://fresh/"
    resolve_mock.assert_called_once()
    # Original cache value untouched.
    assert _cache.lookup("seeded") == "https://stale/"
