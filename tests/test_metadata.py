from __future__ import annotations

import io
import json
from unittest import mock


from pymanual import _metadata


def test_pick_best_prefers_documentation():
    pairs = [("Homepage", "https://home"), ("Documentation", "https://docs")]
    assert _metadata._pick_best(pairs) == "https://docs"


def test_pick_best_falls_back_to_homepage():
    pairs = [("Source", "https://src"), ("Homepage", "https://home")]
    assert _metadata._pick_best(pairs) == "https://home"


def test_pick_best_returns_first_when_no_preference_match():
    pairs = [("Source", "https://src"), ("Issues", "https://bug")]
    assert _metadata._pick_best(pairs) == "https://src"


def test_parse_project_urls_strips_and_drops_invalid():
    raw = ["Homepage, https://home", "  Docs ,  https://docs ", "MalformedNoComma"]
    assert _metadata._parse_project_urls(raw) == [
        ("Homepage", "https://home"),
        ("Docs", "https://docs"),
    ]


def _facts(dist=None, **installed):
    """A probe facts dict with ``installed`` as the target's metadata."""
    return {"dist": dist, "metadata": installed}


_DOCS_META = {"project_urls": ["Homepage, https://home", "Documentation, https://installed"]}


def test_url_from_metadata_falls_back_to_home_page():
    meta = {"project_urls": [], "home_page": " https://home "}
    assert _metadata._url_from_metadata(meta) == "https://home"


def test_url_from_metadata_ignores_unknown_home_page():
    assert _metadata._url_from_metadata({"project_urls": [], "home_page": "UNKNOWN"}) is None


def test_resolve_third_party_uses_installed_metadata_first():
    facts = _facts(dist="real-dist", **{"real-dist": _DOCS_META})
    with mock.patch.object(_metadata, "_fetch_pypi_info") as fetch:
        url = _metadata.resolve_third_party_url("realpkg", facts, network=True)
    assert url == "https://installed"
    fetch.assert_not_called()


def test_resolve_third_party_falls_back_to_pypi_when_metadata_missing():
    info = {"project_urls": {"Documentation": "https://pypi-docs"}}
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value=info) as fetch:
        url = _metadata.resolve_third_party_url("anything", _facts(), network=True)
    assert url == "https://pypi-docs"
    fetch.assert_called_once_with("anything")


def test_resolve_third_party_queries_pypi_by_distribution_name():
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value=None) as fetch:
        url = _metadata.resolve_third_party_url("yaml.loader", _facts(dist="PyYAML"), network=True)
    fetch.assert_called_once_with("PyYAML")
    assert url == "https://pypi.org/project/PyYAML/"


def test_resolve_third_party_skips_pypi_when_network_disabled():
    with mock.patch.object(_metadata, "_fetch_pypi_info") as fetch:
        url = _metadata.resolve_third_party_url("ghostpkg_xyz", _facts(), network=False)
    assert url == "https://pypi.org/project/ghostpkg_xyz/"
    fetch.assert_not_called()


def test_resolve_third_party_heuristic_when_pypi_returns_none():
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value=None):
        url = _metadata.resolve_third_party_url("ghostpkg_xyz", _facts(), network=True)
    assert url == "https://pypi.org/project/ghostpkg_xyz/"


def test_from_pypi_parses_response_and_picks_docs():
    payload = {
        "info": {
            "project_urls": {"Homepage": "https://home", "Documentation": "https://docs"},
            "home_page": "https://home",
        }
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))
    with mock.patch("urllib.request.urlopen", return_value=_FakeCM(body)):
        url = _metadata._from_pypi("anything")
    assert url == "https://docs"


def test_from_pypi_returns_none_on_network_error():
    import urllib.error
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        assert _metadata._from_pypi("anything") is None


def test_from_installed_metadata_unknown_package_returns_none():
    assert _metadata._from_installed_metadata("not-installed", _facts()) is None


def test_resolve_distribution_url_installed_uses_metadata():
    facts = _facts(**{"pytest-cov": _DOCS_META})
    assert _metadata.resolve_distribution_url("pytest-cov", facts, network=False) == "https://installed"


def test_resolve_distribution_url_installed_without_urls_uses_project_page():
    facts = _facts(**{"bare-dist": {"project_urls": [], "home_page": None}})
    url = _metadata.resolve_distribution_url("bare-dist", facts, network=False)
    assert url == "https://pypi.org/project/bare-dist/"


def test_resolve_distribution_url_not_installed_network_off_returns_none():
    facts = _facts(**{"ghost-dist-xyz-123": None})
    assert _metadata.resolve_distribution_url("ghost-dist-xyz-123", facts, network=False) is None


def test_resolve_distribution_url_falls_through_to_pypi_project_when_info_has_no_urls():
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value={}):
        url = _metadata.resolve_distribution_url("some-real-pkg", _facts(), network=True)
    assert url == "https://pypi.org/project/some-real-pkg/"


def test_resolve_distribution_url_returns_none_when_pypi_404():
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value=None):
        assert _metadata.resolve_distribution_url("ghost-xyz", _facts(), network=True) is None


def test_resolve_distribution_url_falls_back_to_top_level_for_dotted_name():
    # First candidate (full dotted name) misses; second (top-level) hits installed metadata.
    facts = _facts(**{"pytest.fixtures": None, "pytest": _DOCS_META})
    url = _metadata.resolve_distribution_url("pytest.fixtures", facts, network=False)
    assert url == "https://installed"


def test_resolve_distribution_url_dotted_name_does_not_double_call_when_undotted():
    with mock.patch.object(_metadata, "_try_distribution", return_value=None) as m:
        assert _metadata.resolve_distribution_url("ghost", _facts(), network=False) is None
    assert m.call_count == 1


class _FakeCM:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self._body

    def __exit__(self, *args):
        return False
