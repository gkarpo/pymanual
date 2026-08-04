"""Core resolution logic.

Resolution strategy, in order:

1. Built-in modules (``sys.builtin_module_names``) → ``docs.python.org`` page.
2. Locatable modules → classify ``spec.origin`` and map:
   stdlib → ``docs.python.org``, third-party → installed or PyPI metadata,
   local → ``STATUS_LOCAL``.
3. Names with no usable file path, and stdlib names that can't be located in
   this environment → ``sys.stdlib_module_names`` decides. Consulted only
   after a path lookup fails, so a local file shadowing a stdlib name still
   classifies by its path.
4. Anything left → try treating the name as a PyPI distribution.

Modules are *located*, never imported: a documentation lookup must not run
arbitrary code from the environment, or ``pymanual foo`` would execute
whatever ``foo.py`` happens to sit in the working directory.

Outcomes that aren't URLs surface as ``STATUS_*`` tokens so callers can
distinguish them from real URLs without exceptions.
"""

from __future__ import annotations

import os
import sys
from importlib.machinery import ModuleSpec
from importlib.util import find_spec

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


def _locate(name: str) -> ModuleSpec | None:
    """``find_spec(name)`` with "can't be located here" folded into ``None``.

    ``ImportError`` (which subsumes ``ModuleNotFoundError``, raised when a
    parent package doesn't exist) and ``ValueError`` (empty string, or a
    ``sys.modules`` entry whose ``__spec__`` is ``None``) all mean the same
    thing to us. Anything else propagates: a parent package that raises at
    import time is a real problem in the environment, not a lookup miss.
    """
    try:
        return find_spec(name)
    except (ImportError, ValueError):
        return None


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

    spec = _locate(module_name)

    if spec is None and top_level != module_name:
        # The leaf may be missing while the package itself is real. Retrying
        # the top level keeps the answer derived from what's installed here:
        # without it, yaml.nosuchthing would reach the distribution fallback,
        # which doesn't apply the import-name → distribution-name mapping.
        spec = _locate(top_level)

    if spec is None:
        # Not locatable *here* doesn't mean not stdlib: winreg on Linux and
        # tkinter on a build without Tk are both documented modules. Answer
        # from the name before falling through to PyPI, so a project that
        # happens to share a stdlib name can't hijack the result.
        if _is_stdlib_name(top_level):
            return _stdlib_page(top_level)
        # Otherwise it may still be a known PyPI distribution (e.g. the user
        # isn't in the env where it's installed).
        url = resolve_distribution_url(module_name, network=network)
        if url is not None:
            return url
        return STATUS_NOT_FOUND

    origin = spec.origin
    if not origin or not os.path.exists(origin):
        # No usable path to classify. `origin` is None for namespace packages
        # and a sentinel string ('frozen', 'built-in') for modules with no
        # file at all; it can also name a source file deleted since import.
        # Only the name is left to go on.
        if _is_stdlib_name(top_level):
            return _stdlib_page(top_level)
        return STATUS_UNRESOLVABLE_PATH

    kind = classify(origin)
    if kind == "stdlib":
        return _stdlib_page(top_level)
    if kind == "local":
        return STATUS_LOCAL
    return resolve_third_party_url(module_name, network=network)
