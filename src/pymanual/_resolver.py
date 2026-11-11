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

Locating happens in ``_probe``, possibly under another interpreter (see
``_target``); this module only decides, from the facts the probe returns.

Outcomes that aren't URLs surface as ``STATUS_*`` tokens so callers can
distinguish them from real URLs without exceptions.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from . import _cache, _target
from ._metadata import resolve_distribution_url, resolve_third_party_url

log = logging.getLogger("pymanual")

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


def _is_stdlib_name(top_level: str, facts: dict[str, Any]) -> bool:
    """True when the *name* is stdlib, independent of any file path.

    ``sys.stdlib_module_names`` is generated at build time and lists every
    stdlib top-level name unconditionally — including modules belonging to
    other platforms (``winreg`` on Linux) and ones this build omitted
    (``tkinter`` without the Tk headers). That makes it the right answer for
    modules with no usable path, but the *wrong* thing to consult first: a
    local ``statistics.py`` shadowing a stdlib name must classify by its path.
    Callers only reach this after a path lookup has come up empty.

    The target interpreter's list wins; pymanual's own is the stand-in for a
    target older than 3.10, which has no such list.
    """
    answer = facts.get("stdlib_name")
    if answer is None:
        return top_level in sys.stdlib_module_names
    return bool(answer)


def resolve_doc_url(
    module_name: str,
    *,
    network: bool = True,
    use_cache: bool = True,
    python: str | os.PathLike[str] | None = None,
) -> str:
    """Return a docs URL for ``module_name`` or a ``STATUS_*`` sentinel.

    With ``use_cache=True`` (default) the on-disk cache is consulted first
    and successful lookups are persisted. Only real URLs are cached — status
    tokens are re-computed each call so transient failures don't stick.

    With ``network=False`` the PyPI HTTP fallback is skipped; a third-party
    module with no local metadata may then return ``STATUS_NOT_FOUND``.

    ``python`` chooses the environment the module is located in:

    * ``None`` (default) — this process's own environment.
    * a path to an interpreter, a virtual environment directory, or a
      command name on ``PATH`` — that environment. Raises ``ValueError`` if
      it names no interpreter, and ``RuntimeError`` if it can't answer.
    * ``"auto"`` — ``$PYMANUAL_PYTHON``, else ``$VIRTUAL_ENV``, else
      ``python3`` on ``PATH``; falls back to this process if that
      interpreter can't run. This is what the CLI uses.
    """
    target = _target.select(python)

    if use_cache:
        cached = _cache.lookup(target.cache_key(module_name))
        if cached is not None:
            log.debug("cache hit for %r", module_name)
            return cached

    facts, used = _target.gather(module_name, target)
    result = _resolve(module_name, facts, network=network)

    if use_cache and result not in _STATUS_TOKENS:
        _cache.store(used.cache_key(module_name), result)

    return result


def _resolve(module_name: str, facts: dict[str, Any], *, network: bool) -> str:
    top_level = module_name.split(".", 1)[0]

    # Built-ins have no path to classify; the name alone settles them.
    if facts["builtin"]:
        return _stdlib_page(top_level)

    if not facts["located"]:
        # Not locatable *there* doesn't mean not stdlib: winreg on Linux and
        # tkinter on a build without Tk are both documented modules. Answer
        # from the name before falling through to PyPI, so a project that
        # happens to share a stdlib name can't hijack the result.
        if _is_stdlib_name(top_level, facts):
            return _stdlib_page(top_level)
        # Otherwise it may still be a known PyPI distribution (e.g. the user
        # isn't in the env where it's installed).
        url = resolve_distribution_url(module_name, facts, network=network)
        if url is not None:
            return url
        return STATUS_NOT_FOUND

    if facts["origin"] is None:
        # Located, but no usable path to classify: a namespace package, a
        # frozen module, or a source file deleted since import. Only the
        # name is left to go on.
        if _is_stdlib_name(top_level, facts):
            return _stdlib_page(top_level)
        return STATUS_UNRESOLVABLE_PATH

    kind = facts["kind"]
    if kind == "stdlib":
        return _stdlib_page(top_level)
    if kind == "local":
        return STATUS_LOCAL
    return resolve_third_party_url(module_name, facts, network=network)
