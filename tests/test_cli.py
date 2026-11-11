"""CLI surface tests: stdout contract, exit codes, and flag plumbing.

The exit code and the exact text on stdout are the CLI's public contract —
shell pipelines branch on both — so they are asserted literally.
"""

from __future__ import annotations

from unittest import mock

import pytest

from pymanual import _cache
from pymanual._cli import main


def test_resolved_url_printed_and_exit_zero(capsys):
    code = main(["json"])
    assert capsys.readouterr().out == "https://docs.python.org/3/library/json.html\n"
    assert code == 0


def test_status_token_printed_and_exit_one(capsys):
    code = main(["definitely_no_such_module_xyz_123", "--no-network"])
    assert capsys.readouterr().out == "module_not_found\n"
    assert code == 1


def test_local_module_exits_one(capsys, tmp_path, monkeypatch):
    (tmp_path / "clilocalmod.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    import sys

    sys.modules.pop("clilocalmod", None)
    try:
        code = main(["clilocalmod"])
    finally:
        sys.modules.pop("clilocalmod", None)
    assert capsys.readouterr().out == "local_module_no_external_docs\n"
    assert code == 1


def test_no_network_flag_reaches_resolver():
    with mock.patch("pymanual._cli.resolve_doc_url", return_value="https://x/") as r:
        main(["somemod", "--no-network"])
    r.assert_called_once_with("somemod", network=False, use_cache=True, python="auto")


def test_no_cache_flag_reaches_resolver():
    with mock.patch("pymanual._cli.resolve_doc_url", return_value="https://x/") as r:
        main(["somemod", "--no-cache"])
    r.assert_called_once_with("somemod", network=True, use_cache=False, python="auto")


def test_defaults_enable_network_and_cache():
    with mock.patch("pymanual._cli.resolve_doc_url", return_value="https://x/") as r:
        main(["somemod"])
    r.assert_called_once_with("somemod", network=True, use_cache=True, python="auto")


def test_clear_cache_removes_file_and_reports(capsys):
    _cache.store("json", "https://docs.python.org/3/library/json.html")
    assert _cache.cache_path().exists()

    code = main(["--clear-cache"])

    out = capsys.readouterr().out
    assert code == 0
    assert not _cache.cache_path().exists()
    assert str(_cache.cache_path()) in out


def test_clear_cache_when_empty_still_exits_zero(capsys):
    code = main(["--clear-cache"])
    assert code == 0
    assert capsys.readouterr().out == "Cache was already empty.\n"


def test_clear_cache_ignores_module_argument(capsys):
    """``--clear-cache`` short-circuits before resolution, so no URL is printed."""
    with mock.patch("pymanual._cli.resolve_doc_url") as r:
        code = main(["--clear-cache", "json"])
    assert code == 0
    r.assert_not_called()
    assert "docs.python.org" not in capsys.readouterr().out


def test_missing_module_argument_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    assert "required" in capsys.readouterr().err


def test_unknown_flag_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main(["json", "--no-such-flag"])
    assert excinfo.value.code == 2


def test_cli_result_is_cached_between_invocations(capsys):
    main(["json"])
    with mock.patch("pymanual._resolver._resolve") as resolve_mock:
        code = main(["json"])
    assert code == 0
    assert capsys.readouterr().out.endswith("json.html\n")
    resolve_mock.assert_not_called()
