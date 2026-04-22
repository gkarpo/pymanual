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


def test_resolve_third_party_uses_installed_metadata_first():
    with mock.patch.object(
        _metadata, "_from_installed_metadata", return_value="https://installed"
    ) as inst, mock.patch.object(_metadata, "_fetch_pypi_info") as fetch:
        url = _metadata.resolve_third_party_url("anything", network=True)
    assert url == "https://installed"
    inst.assert_called_once()
    fetch.assert_not_called()


def test_resolve_third_party_falls_back_to_pypi_when_metadata_missing():
    info = {"project_urls": {"Documentation": "https://pypi-docs"}}
    with mock.patch.object(_metadata, "_from_installed_metadata", return_value=None), \
         mock.patch.object(_metadata, "_fetch_pypi_info", return_value=info) as fetch:
        url = _metadata.resolve_third_party_url("anything", network=True)
    assert url == "https://pypi-docs"
    fetch.assert_called_once()


def test_resolve_third_party_skips_pypi_when_network_disabled():
    with mock.patch.object(_metadata, "_from_installed_metadata", return_value=None), \
         mock.patch.object(_metadata, "_fetch_pypi_info") as fetch:
        url = _metadata.resolve_third_party_url("ghostpkg_xyz", network=False)
    assert url == "https://pypi.org/project/ghostpkg_xyz/"
    fetch.assert_not_called()


def test_resolve_third_party_heuristic_when_pypi_returns_none():
    with mock.patch.object(_metadata, "_from_installed_metadata", return_value=None), \
         mock.patch.object(_metadata, "_fetch_pypi_info", return_value=None):
        url = _metadata.resolve_third_party_url("ghostpkg_xyz", network=True)
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
    assert _metadata._from_installed_metadata("definitely_not_a_real_dist_xyz_123") is None


def test_resolve_distribution_url_installed_uses_metadata():
    # pytest-cov is installed as a dev dep; looked up directly by dist name.
    url = _metadata.resolve_distribution_url("pytest-cov", network=False)
    assert url is not None and url.startswith("http")


def test_resolve_distribution_url_not_installed_network_off_returns_none():
    assert _metadata.resolve_distribution_url(
        "ghost-dist-xyz-123", network=False
    ) is None


def test_resolve_distribution_url_falls_through_to_pypi_project_when_info_has_no_urls():
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value={}):
        url = _metadata.resolve_distribution_url("some-real-pkg", network=True)
    assert url == "https://pypi.org/project/some-real-pkg/"


def test_resolve_distribution_url_returns_none_when_pypi_404():
    with mock.patch.object(_metadata, "_fetch_pypi_info", return_value=None):
        assert _metadata.resolve_distribution_url("ghost-xyz", network=True) is None


def test_resolve_distribution_url_falls_back_to_top_level_for_dotted_name():
    # First candidate (full dotted name) misses; second (top-level) hits installed metadata.
    def fake_try(name, *, network):
        return "https://docs/installed" if name == "pytest" else None

    with mock.patch.object(_metadata, "_try_distribution", side_effect=fake_try) as m:
        url = _metadata.resolve_distribution_url("pytest.fixtures", network=False)
    assert url == "https://docs/installed"
    assert [c.args[0] for c in m.call_args_list] == ["pytest.fixtures", "pytest"]


def test_resolve_distribution_url_dotted_name_real_installed_pkg():
    # End-to-end: pytest is installed, so "pytest.unknown_sub" should still resolve via "pytest".
    url = _metadata.resolve_distribution_url("pytest.unknown_sub", network=False)
    assert url is not None and url.startswith("http")


def test_resolve_distribution_url_dotted_name_does_not_double_call_when_undotted():
    with mock.patch.object(_metadata, "_try_distribution", return_value=None) as m:
        assert _metadata.resolve_distribution_url("ghost", network=False) is None
    assert m.call_count == 1


class _FakeCM:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self._body

    def __exit__(self, *args):
        return False
