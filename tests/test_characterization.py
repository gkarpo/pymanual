"""Characterisation tests: they pin what the resolver does *today*.

Written ahead of the ``find_spec`` migration (spec.md §4 Step 2) so the
behavioural diff lands in this file rather than being absorbed by rewritten
tests. Two kinds of test live here:

* plain assertions — current behaviour that the spec also wants, or that the
  spec explicitly accepts as a limitation. These must keep passing.
* ``xfail(strict=True)`` — the behaviour spec.md requires and the current
  implementation lacks. Each one fails the suite the moment it starts
  passing, which is the signal that the migration landed and the marker
  should come off.

Nothing here mocks the import machinery; every test drives real modules.
"""

from __future__ import annotations

import sys

import pytest

from pymanual import (
    STATUS_LOCAL,
    STATUS_NOT_FOUND,
    STATUS_UNRESOLVABLE_PATH,
    resolve_doc_url,
)

_PY_DOCS = "https://docs.python.org/3/library/{name}.html"


@pytest.fixture
def importable(tmp_path, monkeypatch):
    """Write modules into an importable tmp dir, cleaning up ``sys.modules`` after.

    Returns a ``write(dotted_name, source)`` callable. Intermediate packages
    are created with an empty ``__init__.py`` unless one is written explicitly;
    pass a name ending in ``.__init__`` to give a package a body.
    """
    created: list[str] = []

    def write(dotted: str, source: str = "") -> None:
        if dotted.endswith(".__init__"):
            dotted, leaf = dotted.rsplit(".", 1)
            target = tmp_path.joinpath(*dotted.split(".")) / f"{leaf}.py"
        else:
            parts = dotted.split(".")
            target = tmp_path.joinpath(*parts[:-1]) / f"{parts[-1]}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        for i in range(1, len(dotted.split("."))):
            pkg_init = tmp_path.joinpath(*dotted.split(".")[:i]) / "__init__.py"
            if not pkg_init.exists():
                pkg_init.write_text("")
        target.write_text(source)
        created.append(dotted.split(".")[0])

    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        yield write
    finally:
        for root in created:
            for name in [m for m in sys.modules if m == root or m.startswith(root + ".")]:
                sys.modules.pop(name, None)


def _side_effect_source(sentinel) -> str:
    return f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n"


# --------------------------------------------------------------------------
# Execution safety — the property the find_spec migration exists to provide
# --------------------------------------------------------------------------


def test_resolution_does_not_execute_module_code(tmp_path, importable):
    """spec.md §1: resolving a name must not run that module's code."""
    sentinel = tmp_path / "leaf-executed.flag"
    importable("sideeffect_mod", _side_effect_source(sentinel))

    assert resolve_doc_url("sideeffect_mod", network=False, use_cache=False) == STATUS_LOCAL
    assert not sentinel.exists(), "resolving the module executed its top-level code"


def test_dotted_leaf_module_is_not_executed(tmp_path, importable):
    """find_spec resolves the leaf from its parent's __path__ without running it."""
    sentinel = tmp_path / "dotted-leaf-executed.flag"
    importable("sepkg.leaf", _side_effect_source(sentinel))

    resolve_doc_url("sepkg.leaf", network=False, use_cache=False)
    assert not sentinel.exists(), "resolving a dotted name executed the leaf module"


def test_parent_packages_are_executed_residual_risk(tmp_path, importable):
    """Parents of a dotted name run, before *and* after the migration.

    spec.md §4 Step 2 records this under "Residual execution risk":
    ``find_spec`` imports parent packages to consult their ``__path__``. This
    test documents the exposure that survives the change — if it ever starts
    failing, the resolver got safer than the spec promises.
    """
    sentinel = tmp_path / "parent-executed.flag"
    importable("sepkg2.__init__", _side_effect_source(sentinel))
    importable("sepkg2.leaf", "VALUE = 1\n")

    resolve_doc_url("sepkg2.leaf", network=False, use_cache=False)
    assert sentinel.exists(), "parent package no longer executes — spec §4 Step 2 can be tightened"


# --------------------------------------------------------------------------
# Namespace packages — no __init__.py, so no file to classify
# --------------------------------------------------------------------------


def test_namespace_package_is_unresolvable(tmp_path, monkeypatch):
    (tmp_path / "nspkg").mkdir()
    (tmp_path / "nspkg" / "leaf.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("nspkg", None)
    try:
        assert (
            resolve_doc_url("nspkg", network=False, use_cache=False)
            == STATUS_UNRESOLVABLE_PATH
        )
    finally:
        for name in [m for m in sys.modules if m == "nspkg" or m.startswith("nspkg.")]:
            sys.modules.pop(name, None)


def test_namespace_package_leaf_resolves_to_its_own_file(tmp_path, monkeypatch):
    """The leaf has a real file even though its parent doesn't, so it classifies.

    This is the case that top-level-only spec lookup would give up on.
    """
    (tmp_path / "nspkg2").mkdir()
    (tmp_path / "nspkg2" / "leaf.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert (
            resolve_doc_url("nspkg2.leaf", network=False, use_cache=False) == STATUS_LOCAL
        )
    finally:
        for name in [m for m in sys.modules if m == "nspkg2" or m.startswith("nspkg2.")]:
            sys.modules.pop(name, None)


def test_module_whose_file_was_deleted_is_unresolvable(tmp_path, importable):
    """A path that no longer exists on disk is not a usable classification input.

    Reached by importing the module and then deleting its source, which leaves
    a stale ``__file__``/``spec.origin`` behind in ``sys.modules``. Survives the
    migration: ``find_spec`` consults ``sys.modules`` first and returns the same
    stale origin.
    """
    importable("vanishing_mod", "VALUE = 1\n")
    import importlib

    importlib.import_module("vanishing_mod")
    (tmp_path / "vanishing_mod.py").unlink()

    assert (
        resolve_doc_url("vanishing_mod", network=False, use_cache=False)
        == STATUS_UNRESOLVABLE_PATH
    )


# --------------------------------------------------------------------------
# Built-in and frozen modules — no ordinary file path
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["sys", "gc", "errno", "time", "builtins", "marshal"])
def test_builtin_modules_resolve_to_stdlib_docs(name):
    assert name in sys.builtin_module_names, f"{name} is not built-in on this build"
    assert resolve_doc_url(name, network=False, use_cache=False) == _PY_DOCS.format(name=name)


@pytest.mark.parametrize("name", ["zipimport", "os", "io", "abc", "codecs", "stat"])
def test_frozen_modules_resolve_to_stdlib_docs(name):
    """CPython freezes a chunk of the bootstrap stdlib: ``spec.origin == 'frozen'``.

    These have no usable path at all, so the ``sys.stdlib_module_names`` check
    is the only thing that answers for them — without it every name here
    returns ``unresolvable_module_path``. ``os`` and ``io`` are ordinary
    lookups, so this is a load-bearing path, not an edge case.

    (Before the find_spec migration they resolved by path instead: the
    imported module object still carries a ``__file__``. The URL is the same
    either way, which is what this pins.)
    """
    assert resolve_doc_url(name, network=False, use_cache=False) == _PY_DOCS.format(name=name)


def test_frozen_modules_are_not_rescued_by_the_builtin_short_circuit():
    """Pins *which* mechanism answers for frozen modules.

    They are not in ``sys.builtin_module_names``, so the Step 1 fast path
    never sees them — if the name-based stdlib fallback regresses, this stops
    being a silent pass elsewhere.
    """
    for name in ("zipimport", "os", "io"):
        assert name not in sys.builtin_module_names
        assert name in sys.stdlib_module_names


@pytest.mark.parametrize("name", ["winreg", "tkinter"])
def test_stdlib_module_absent_from_this_build_still_resolves(name):
    """Unimportable stdlib modules resolve from the name alone.

    ``winreg`` is Windows-only and ``tkinter`` is missing from builds without
    Tk, yet both are documented. Without the name check these fall through to
    the distribution fallback and — with network enabled — a PyPI project
    sharing the name could answer instead.
    """
    assert name in sys.stdlib_module_names
    assert resolve_doc_url(name, network=False, use_cache=False) == _PY_DOCS.format(name=name)


def test_importable_stdlib_name_with_no_file_resolves_by_name(tmp_path, monkeypatch):
    """An importable stdlib name whose module has no file at all.

    A stray directory named ``winreg`` on the path imports as a namespace
    package with ``__file__ is None``. There is nothing to classify, so the
    name decides — the same route frozen modules take once the find_spec
    migration removes their paths.
    """
    (tmp_path / "winreg").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("winreg", None)
    try:
        assert resolve_doc_url("winreg", network=False, use_cache=False) == _PY_DOCS.format(
            name="winreg"
        )
    finally:
        sys.modules.pop("winreg", None)


def test_local_file_shadowing_a_stdlib_name_classifies_as_local(tmp_path, importable):
    """The name check must not pre-empt path classification.

    ``statistics`` is in ``sys.stdlib_module_names``, but a local file of that
    name is the user's own code and has no docs page.
    """
    importable("statistics", "VALUE = 1\n")
    sys.modules.pop("statistics", None)

    assert "statistics" in sys.stdlib_module_names
    assert resolve_doc_url("statistics", network=False, use_cache=False) == STATUS_LOCAL


# --------------------------------------------------------------------------
# The stdlib page rule (spec.md §4.1) — not implemented yet
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["_ast", "_ssl"])
@pytest.mark.xfail(
    strict=True,
    reason="spec.md §4.1: private stdlib modules have no docs page and must return "
    "STATUS_UNRESOLVABLE_PATH. Today they get a URL that 404s.",
)
def test_private_stdlib_modules_have_no_docs_page(name):
    assert resolve_doc_url(name, network=False, use_cache=False) == STATUS_UNRESOLVABLE_PATH


@pytest.mark.parametrize("name", ["_thread", "__future__"])
def test_documented_underscore_modules_keep_their_page(name):
    """The two hardcoded exceptions to §4.1's underscore rule."""
    assert resolve_doc_url(name, network=False, use_cache=False) == _PY_DOCS.format(name=name)


def test_undocumented_stdlib_module_still_gets_a_url():
    """``encodings`` has no docs page but no underscore either — accepted, spec.md §13.2."""
    assert resolve_doc_url("encodings", network=False, use_cache=False) == _PY_DOCS.format(
        name="encodings"
    )


# --------------------------------------------------------------------------
# Malformed and partially-valid names
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Raises ValueError('A distribution name is required.') from "
    "importlib.metadata; spec.md §1/§3.1 require a status token, never an "
    "exception. Logged as a bug, fix tracked separately.",
)
def test_empty_string_returns_status_token():
    assert resolve_doc_url("", network=False, use_cache=False) == STATUS_NOT_FOUND


def test_relative_name_returns_status_token():
    """``find_spec`` raises ImportError for a relative name, which we catch.

    ``import_module`` raised TypeError here, which escaped uncaught. The
    empty-string half of that bug is unfixed — see the test below.
    """
    assert resolve_doc_url(".foo", network=False, use_cache=False) == STATUS_NOT_FOUND


def test_missing_leaf_under_stdlib_parent_falls_back_to_parent():
    """spec.md §4 Step 2a, stdlib half — satisfied by the name-based fallback.

    ``json.nosuchthing`` is unimportable, but ``json`` is a stdlib name, so it
    answers from the parent's page instead of reaching PyPI (which carries a
    project named ``json``).
    """
    assert resolve_doc_url("json.nosuchthing", network=False, use_cache=False) == _PY_DOCS.format(
        name="json"
    )


def test_missing_leaf_under_third_party_parent_resolves_via_parent():
    url = resolve_doc_url("pytest.nosuchthing", network=False, use_cache=False)
    assert url.startswith("http") and "pytest" in url


def test_missing_leaf_uses_import_name_to_distribution_mapping():
    """spec.md §4 Step 2a, the case the distribution fallback can't handle.

    ``_pytest`` is installed but no distribution is *named* ``_pytest`` — the
    mapping to ``pytest`` lives in ``packages_distributions()``, which only the
    third-party path consults. Without the top-level retry this reaches the
    distribution fallback instead and returns ``module_not_found`` offline.
    """
    url = resolve_doc_url("_pytest.nosuchthing", network=False, use_cache=False)
    assert url.startswith("http") and "pytest" in url


def test_private_third_party_package_resolves_via_distribution():
    """``_pytest`` maps to the ``pytest`` distribution through packages_distributions()."""
    url = resolve_doc_url("_pytest.config", network=False, use_cache=False)
    assert url.startswith("http") and "pytest" in url


# --------------------------------------------------------------------------
# Resolving in another environment (python=...)
#
# These run the probe under a real, separate venv (``target_env``), the way
# a ``uv tool``/``pipx`` install of pymanual reaches the user's environment.
# --------------------------------------------------------------------------


def test_module_installed_only_in_target_resolves_there(target_env):
    """The reason targets exist: pymanual's own env can't see the module."""
    assert resolve_doc_url("targetonlypkg", network=False, use_cache=False) == STATUS_NOT_FOUND
    assert (
        resolve_doc_url("targetonlypkg", network=False, use_cache=False, python=target_env.python)
        == "https://target.example/docs/"
    )


def test_target_maps_import_name_to_its_distribution(target_env):
    """``targetonlypkg`` → ``target-only-dist`` via the target's packages_distributions()."""
    url = resolve_doc_url(
        "targetonlypkg.nosuchthing", network=False, use_cache=False, python=target_env.python
    )
    assert url == "https://target.example/docs/"


def test_distribution_name_installed_only_in_target(target_env):
    url = resolve_doc_url(
        "target-only-dist", network=False, use_cache=False, python=target_env.python
    )
    assert url == "https://target.example/docs/"


@pytest.mark.parametrize("name", ["json", "os", "sys", "winreg", "email.mime.text"])
def test_stdlib_resolves_through_target(target_env, name):
    top = name.split(".")[0]
    assert resolve_doc_url(
        name, network=False, use_cache=False, python=target_env.python
    ) == _PY_DOCS.format(name=top)


def test_local_module_in_target_classifies_as_local(target_env, tmp_path, monkeypatch):
    """Classification uses the target's own stdlib root and paths.

    ``statistics`` also checks that a local file shadowing a stdlib name still
    classifies by path when the name check runs on the target's list.
    """
    (tmp_path / "targetlocalmod.py").write_text("VALUE = 1\n")
    (tmp_path / "statistics.py").write_text("VALUE = 1\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    for name in ("targetlocalmod", "statistics"):
        assert (
            resolve_doc_url(name, network=False, use_cache=False, python=target_env.python)
            == STATUS_LOCAL
        )


def test_target_does_not_execute_the_leaf(target_env, tmp_path):
    sentinel = tmp_path / "target-leaf-executed.flag"
    target_env.write("tsidepkg/__init__.py")
    target_env.write("tsidepkg/leaf.py", _side_effect_source(sentinel))

    resolve_doc_url("tsidepkg.leaf", network=False, use_cache=False, python=target_env.python)
    assert not sentinel.exists(), "resolving through the target executed the leaf module"


def test_target_executes_parent_packages_residual_risk(target_env, tmp_path):
    """The parent-package exception carries over to the target environment."""
    sentinel = tmp_path / "target-parent-executed.flag"
    target_env.write("tsidepkg2/__init__.py", _side_effect_source(sentinel))
    target_env.write("tsidepkg2/leaf.py", "VALUE = 1\n")

    resolve_doc_url("tsidepkg2.leaf", network=False, use_cache=False, python=target_env.python)
    assert sentinel.exists()


def test_target_ignores_the_working_directory(target_env, tmp_path, monkeypatch):
    """``python -c`` would put the cwd on sys.path; the probe takes it off.

    That matches an installed ``pymanual`` command (whose sys.path[0] is its
    bin directory) and keeps a cwd package from running as a parent.
    """
    (tmp_path / "cwdonlymod.py").write_text("VALUE = 1\n")
    monkeypatch.chdir(tmp_path)
    assert (
        resolve_doc_url("cwdonlymod", network=False, use_cache=False, python=target_env.python)
        == STATUS_NOT_FOUND
    )


def test_parent_package_printing_does_not_corrupt_the_answer(target_env):
    """Output from importing a parent goes to stderr, not into the probe's JSON."""
    target_env.write(
        "tnoisypkg/__init__.py",
        "import os\nprint('hello from python')\nos.write(1, b'hello from the fd\\n')\n",
    )
    target_env.write("tnoisypkg/leaf.py")
    target_env.write(
        "tnoisy_dist-1.0.dist-info/METADATA",
        "Metadata-Version: 2.1\nName: tnoisy-dist\nVersion: 1.0\n"
        "Project-URL: Documentation, https://noisy.example/\n",
    )
    target_env.write("tnoisy_dist-1.0.dist-info/top_level.txt", "tnoisypkg\n")
    assert (
        resolve_doc_url("tnoisypkg.leaf", network=False, use_cache=False, python=target_env.python)
        == "https://noisy.example/"
    )


def test_parent_package_raising_in_target_propagates(target_env):
    """As in-process: a parent that fails to import is an error, not a status."""
    target_env.write("tbrokenpkg/__init__.py", "raise RuntimeError('broken parent')\n")
    target_env.write("tbrokenpkg/leaf.py")
    with pytest.raises(RuntimeError, match="broken parent"):
        resolve_doc_url("tbrokenpkg.leaf", network=False, use_cache=False, python=target_env.python)
