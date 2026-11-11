"""Choosing the target interpreter, falling back, and the probe's own contract."""

from __future__ import annotations

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

from pymanual import _cache, _probe, _target, resolve_doc_url
from pymanual._cli import main

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as a fake interpreter")


def _fake_interpreter(tmp_path: Path, body: str) -> Path:
    """An executable that isn't a working Python: it runs ``body`` under sh."""
    script = tmp_path / "fakepython"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


# --------------------------------------------------------------------------
# select()
# --------------------------------------------------------------------------


def test_none_selects_this_process():
    assert _target.select(None) is _target.IN_PROCESS


def test_own_interpreter_selects_this_process():
    assert _target.select(sys.executable) is _target.IN_PROCESS


def test_venv_directory_and_its_interpreter_select_the_same_target(target_env):
    by_dir = _target.select(target_env.root)
    by_exe = _target.select(target_env.python)
    assert by_dir == by_exe == _target.Target(os.path.abspath(target_env.python), strict=True)


def test_venv_interpreter_symlink_is_not_followed(target_env):
    """Every venv python links to the same base binary; the link is the identity."""
    assert os.path.realpath(target_env.python) != _target.select(target_env.python).python


def test_unknown_interpreter_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not a Python interpreter"):
        _target.select(tmp_path / "nope")


# --------------------------------------------------------------------------
# Auto-detection order
# --------------------------------------------------------------------------


def test_auto_prefers_the_override_variable(monkeypatch, target_env, tmp_path):
    monkeypatch.setenv(_target.ENV_VAR, str(target_env.python))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path))
    assert _target.select("auto").python == os.path.abspath(target_env.python)


def test_auto_uses_the_active_virtual_environment(monkeypatch, target_env):
    monkeypatch.setenv("VIRTUAL_ENV", str(target_env.root))
    target = _target.select("auto")
    assert target.python == os.path.abspath(target_env.python)
    assert not target.strict


def test_auto_skips_a_bad_override(monkeypatch, target_env, tmp_path):
    monkeypatch.setenv(_target.ENV_VAR, str(tmp_path / "missing"))
    monkeypatch.setenv("VIRTUAL_ENV", str(target_env.root))
    assert _target.select("auto").python == os.path.abspath(target_env.python)


def test_auto_falls_back_to_python3_on_path(monkeypatch, target_env):
    monkeypatch.delenv("VIRTUAL_ENV")
    monkeypatch.setenv("PATH", str(target_env.python.parent))
    if not (target_env.python.parent / "python3").exists():
        pytest.skip("venv has no python3 entry point")
    assert _target.select("auto").python == os.path.abspath(target_env.python.parent / "python3")


def test_auto_skips_pymanuals_own_bin_on_path(monkeypatch, target_env):
    """``uvx pymanual`` puts its own venv's bin first on PATH; look past it."""
    monkeypatch.delenv("VIRTUAL_ENV")
    own_bin = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", os.pathsep.join([own_bin, str(target_env.python.parent)]))
    found = _target.select("auto").python
    assert found is not None and Path(found).parent == target_env.python.parent


def test_auto_with_nothing_found_resolves_in_process(monkeypatch, tmp_path):
    monkeypatch.delenv("VIRTUAL_ENV")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _target.select("auto") is _target.IN_PROCESS


# --------------------------------------------------------------------------
# Falling back when the target can't answer
# --------------------------------------------------------------------------


@posix_only
@pytest.mark.parametrize(
    "body",
    ["exit 3", "echo not-json", 'echo \'{"probe": 999}\'', "sleep 5"],
    ids=["crashes", "garbled", "wrong-protocol", "hangs"],
)
def test_auto_target_that_cannot_answer_falls_back(monkeypatch, tmp_path, body):
    monkeypatch.setattr(_target, "PROBE_TIMEOUT", 0.5)
    monkeypatch.setenv(_target.ENV_VAR, str(_fake_interpreter(tmp_path, body)))
    assert resolve_doc_url("json", network=False, python="auto").endswith("/json.html")


@posix_only
def test_explicit_target_that_cannot_answer_raises(tmp_path):
    with pytest.raises(RuntimeError, match="exited with status 3"):
        resolve_doc_url("json", network=False, python=_fake_interpreter(tmp_path, "exit 3"))


# --------------------------------------------------------------------------
# Cache keys
# --------------------------------------------------------------------------


def test_cache_entries_are_kept_per_interpreter(target_env):
    url = resolve_doc_url("targetonlypkg", network=False, python=target_env.python)
    key = _target.select(target_env.python).cache_key("targetonlypkg")
    assert _cache.lookup(key) == url
    assert _cache.lookup("targetonlypkg") is None


def test_fallback_result_is_cached_under_the_in_process_key(monkeypatch, tmp_path):
    if sys.platform == "win32":
        pytest.skip("uses a shell script as a fake interpreter")
    monkeypatch.setenv(_target.ENV_VAR, str(_fake_interpreter(tmp_path, "exit 3")))
    url = resolve_doc_url("json", network=False, python="auto")
    assert _cache.lookup("json") == url


# --------------------------------------------------------------------------
# The probe's own contract
# --------------------------------------------------------------------------


def test_probe_source_parses_as_python_3_9():
    """The target may be older than pymanual's own 3.12 floor."""
    ast.parse(_target._probe_source(), feature_version=(3, 9))


def test_probe_source_imports_only_the_standard_library():
    tree = ast.parse(_target._probe_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the probe is shipped as source; relative imports break it"
            roots = [node.module.split(".")[0]] if node.module else []
        elif isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        else:
            continue
        for root in roots:
            assert root == "__future__" or root in sys.stdlib_module_names, root


def test_probe_reports_facts_without_decisions():
    facts = _probe.gather("json")
    assert facts["kind"] == "stdlib" and facts["located"] and facts["origin"]
    assert not any("http" in str(v) for v in facts.values())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_python_flag_reaches_the_target(capsys, target_env):
    code = main(["targetonlypkg", "--no-network", "--python", str(target_env.python)])
    assert capsys.readouterr().out == "https://target.example/docs/\n"
    assert code == 0


def test_cli_verbose_explains_the_choice_on_stderr(capsys, monkeypatch, target_env):
    monkeypatch.setenv("VIRTUAL_ENV", str(target_env.root))
    code = main(["targetonlypkg", "--no-network", "--no-cache", "-v"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == "https://target.example/docs/\n"
    assert f"resolving with {os.path.abspath(target_env.python)} ($VIRTUAL_ENV)" in err


def test_cli_unknown_python_is_a_usage_error(capsys, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(["json", "--python", str(tmp_path / "nope")])
    assert excinfo.value.code == 2
    assert "--python" in capsys.readouterr().err


def test_cli_target_failure_exits_two_with_nothing_on_stdout(capsys, target_env):
    target_env.write("tclibrokenpkg/__init__.py", "raise RuntimeError('broken parent')\n")
    target_env.write("tclibrokenpkg/leaf.py")
    code = main(["tclibrokenpkg.leaf", "--no-network", "--python", str(target_env.python)])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "broken parent" in err
