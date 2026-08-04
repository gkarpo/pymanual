"""Core resolution logic.

Resolution strategy, in order:

1. Built-in modules (``sys.builtin_module_names``) → ``docs.python.org`` page.
2. Importable modules → classify the file path and map:
   stdlib → ``docs.python.org``, third-party → installed or PyPI metadata,
   local → ``STATUS_LOCAL``.
3. Names with no usable file path, and stdlib names that aren't importable in
   this environment → ``sys.stdlib_module_names`` decides. Consulted only
   after a path lookup fails, so a local file shadowing a stdlib name still
   classifies by its path.
4. Anything left → try treating the name as a PyPI distribution.

Outcomes that aren't URLs surface as ``STATUS_*`` tokens so callers can
distinguish them from real URLs without exceptions.
"""

from __future__ import annotations

import inspect
import os
import sys
from importlib import import_module
from typing import Any, cast

from . import _cache
from ._classify import classify
from ._metadata import resolve_distribution_url, resolve_third_party_url

# Sentinel return values. Kept as public constants so CLI and library callers
# can pattern-match on them without hard-coding the strings.
STATUS_NOT_FOUND = "module_not_found"
STATUS_UNRESOLVABLE_PATH = "unresolvable_module_path"
STATUS_LOCAL = "local_module_no_external_docs"

_STATUS_TOKENS = frozenset([STATUS_NOT_FOUND, STATUS_UNRESOLVABLE_PATH, STATUS_LOCAL])
_STDLIB_DOC_URL = "https://docs.python.org/3/library/{name}.html"


def _stdlib_page(top_level: str) -> str:
    """Return the docs.python.org page for a name already known to be stdlib."""
    return _STDLIB_DOC_URL.format(name=top_level)


def _is_stdlib_name(top_level: str) -> bool:
    """True when the *name* is stdlib, independent of any file path.

    ``sys.stdlib_module_names`` is generated at build time and lists every
    stdlib top-level name unconditionally — including modules belonging to
    other platforms (``winreg`` on Linux) and ones this build omitted
    (``tkinter`` without the Tk headers). That makes it the right answer for
    modules with no usable path, but the *wrong* thing to consult first: a
    local ``statistics.py`` shadowing a stdlib name must classify by its path.
    Callers only reach this after a path lookup has come up empty.
    """
    return top_level in sys.stdlib_module_names


def resolve_doc_url(
    module_name: str,
    *,
    network: bool = True,
    use_cache: bool = True,
) -> str:
    """Return a docs URL for ``module_name`` or a ``STATUS_*`` sentinel.

    With ``use_cache=True`` (default) the on-disk cache is consulted first
    and successful lookups are persisted. Only real URLs are cached — status
    tokens are re-computed each call so transient failures don't stick.

    With ``network=False`` the PyPI HTTP fallback is skipped; a third-party
    module with no local metadata may then return ``STATUS_NOT_FOUND``.
    """
    if use_cache:
        cached = _cache.lookup(module_name)
        if cached is not None:
            return cached

    result = _resolve(module_name, network=network)

    if use_cache and result not in _STATUS_TOKENS:
        _cache.store(module_name, result)

    return result


def _resolve(module_name: str, *, network: bool) -> str:
    top_level = module_name.split(".", 1)[0]

    # Built-ins are checked before import_module() because inspect.getfile raises
    # TypeError on them — there's no path to classify.
    if top_level in sys.builtin_module_names:
        return _stdlib_page(top_level)

    # import_module is the documented way to import by dotted name. It raises
    # ImportError (incl. ModuleNotFoundError) for unknown/invalid names and
    # ValueError for empty strings; both just mean "not importable here."
    try:
        obj: Any = import_module(module_name)
    except (ImportError, ValueError):
        # Not importable *here* doesn't mean not stdlib: winreg on Linux and
        # tkinter on a build without Tk are both documented modules. Answer
        # from the name before falling through to PyPI, so a project that
        # happens to share a stdlib name can't hijack the result. This also
        # covers a missing leaf under a real stdlib parent — json.nosuchthing
        # yields json's page rather than reaching the distribution fallback.
        if _is_stdlib_name(top_level):
            return _stdlib_page(top_level)
        # Otherwise it may still be a known PyPI distribution (e.g. the user
        # isn't in the env where it's installed).
        url = resolve_distribution_url(module_name, network=network)
        if url is not None:
            return url
        return STATUS_NOT_FOUND

    try:
        path = inspect.getfile(cast(Any, obj))
    except TypeError:
        path = ""

    if not path or not os.path.exists(path):
        # No usable path to classify: built-ins, frozen modules (zipimport),
        # namespace packages, and modules whose source was deleted after
        # import. Only the name is left to go on.
        if _is_stdlib_name(top_level):
            return _stdlib_page(top_level)
        return STATUS_UNRESOLVABLE_PATH

    kind = classify(path)
    if kind == "stdlib":
        return _stdlib_page(top_level)
    if kind == "local":
        return STATUS_LOCAL
    return resolve_third_party_url(module_name, network=network)
