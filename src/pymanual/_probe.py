"""Collect the facts resolution needs from one Python environment.

This module runs in two ways:

* in-process, via :func:`gather`, when resolving in pymanual's own
  environment;
* as a script, ``python -c <this file's source> <name>``, under *another*
  interpreter, so that a ``uv tool``/``pipx`` install of pymanual can see
  the modules of the environment the user is actually working in.

The second use sets the rules for this file: standard library only, no
imports from the rest of pymanual (it is shipped as source, not as a
package), and syntax that Python 3.9 can parse, because the target may be
older than the 3.12 pymanual itself requires.

It reports facts and makes no decisions: no URLs, no status tokens, and no
network access. Those belong to ``_resolver`` and ``_metadata``, which read
the dict returned here.
"""

from __future__ import annotations

import json
import os
import sys
import sysconfig
import traceback
from importlib import metadata
from importlib.util import find_spec
from typing import Any, Dict, List, Optional

# Bumped whenever the shape of the facts dict changes, so a host never
# misreads output from a probe it doesn't understand.
PROTOCOL = 1

_VENDOR_MARKERS = ("site-packages", "dist-packages")
_VENV_MARKERS = (
    os.sep + ".venv" + os.sep,
    os.sep + "venv" + os.sep,
    os.sep + ".tox" + os.sep,
)


def classify(path: str, stdlib_root: Optional[str] = None) -> str:
    """Return ``"stdlib"``, ``"third_party"``, or ``"local"`` for ``path``.

    Classification is by file path only, never by ``sys.path`` order. Paths
    are resolved via ``realpath`` first so that symlinked venvs or
    ``/usr/lib`` → ``/usr/lib64`` indirection don't hide the real location.
    ``stdlib_root`` defaults to the running interpreter's.
    """
    p = os.path.realpath(path)
    if stdlib_root is None:
        stdlib_root = os.path.realpath(sysconfig.get_paths()["stdlib"])

    if p == stdlib_root or p.startswith(stdlib_root + os.sep):
        # Some distros nest ``site-packages`` *inside* the stdlib root, so a
        # path being under ``stdlib_root`` isn't sufficient — exclude anything
        # that also looks vendored, otherwise third-party libs would be
        # misclassified as stdlib and get a broken ``docs.python.org`` link.
        if not any(m in p for m in _VENDOR_MARKERS):
            return "stdlib"

    if any(m in p for m in _VENDOR_MARKERS):
        return "third_party"
    if any(m in p for m in _VENV_MARKERS):
        return "third_party"

    return "local"


def _locate(name: str) -> Any:
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


def _distribution_for(top_level: str) -> Optional[str]:
    """The installed distribution providing import name ``top_level``, if known."""
    lookup = getattr(metadata, "packages_distributions", None)  # Python 3.10+
    if lookup is None:
        return None
    try:
        dists = lookup().get(top_level)
    except Exception:
        return None
    return dists[0] if dists else None


def _read_metadata(dist_name: str) -> Optional[Dict[str, Any]]:
    """The URL-bearing fields of an installed distribution, or ``None``."""
    try:
        meta = metadata.metadata(dist_name)
    except metadata.PackageNotFoundError:
        return None
    return {
        "project_urls": list(meta.get_all("Project-URL") or []),
        "home_page": meta.get("Home-page"),
    }


def gather(name: str) -> Dict[str, Any]:
    """Locate ``name`` here and report what the resolver needs to decide.

    The returned dict is JSON-serialisable:

    * ``builtin`` — the top-level name is compiled into the interpreter.
    * ``stdlib_name`` — the top-level name is in ``sys.stdlib_module_names``,
      or ``None`` on an interpreter too old to have that list.
    * ``located`` — ``find_spec`` found the name, or its top-level package.
    * ``origin`` — the located file, ``None`` when there is no usable path.
    * ``kind`` — :func:`classify` of ``origin``.
    * ``dist`` — the distribution providing the top-level name (third-party
      only).
    * ``metadata`` — installed metadata, keyed by every distribution name the
      resolver may ask about; ``None`` values mean "not installed here".
    """
    top_level = name.split(".", 1)[0]
    stdlib_names = getattr(sys, "stdlib_module_names", None)  # Python 3.10+
    facts: Dict[str, Any] = {
        "probe": PROTOCOL,
        "executable": sys.executable,
        "builtin": top_level in sys.builtin_module_names,
        "stdlib_name": None if stdlib_names is None else top_level in stdlib_names,
        "located": False,
        "origin": None,
        "kind": None,
        "dist": None,
        "metadata": {},
    }
    if facts["builtin"]:
        # Built-ins have no path to classify, and the resolver answers for
        # them from the name before looking at anything else.
        return facts

    spec = _locate(name)
    if spec is None and top_level != name:
        # The leaf may be missing while the package itself is real. Retrying
        # the top level keeps the answer derived from what's installed here.
        spec = _locate(top_level)

    wanted: List[str] = []
    if spec is None:
        # Candidates for the distribution-name fallback: the input as typed,
        # then its top-level segment (absent for a relative name like ".foo").
        wanted = [name, top_level] if top_level else [name]
    else:
        facts["located"] = True
        origin = spec.origin
        # ``origin`` is None for namespace packages and a sentinel string
        # ('frozen', 'built-in') for modules with no file at all; it can also
        # name a source file deleted since import.
        if origin and os.path.exists(origin):
            facts["origin"] = origin
            facts["kind"] = classify(origin)
            if facts["kind"] == "third_party":
                facts["dist"] = _distribution_for(top_level)
                wanted = [facts["dist"] or top_level]

    for dist_name in dict.fromkeys(wanted):
        facts["metadata"][dist_name] = _read_metadata(dist_name)
    return facts


def _main(argv: List[str]) -> int:
    """Script entry point: print the facts for ``argv[1]`` as JSON on stdout."""
    # Keep stdout for the JSON alone. Parent packages of a dotted name get
    # imported, and anything they print — from Python or from C — would
    # otherwise corrupt the output. Their noise goes to stderr instead.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)

    # ``-c`` puts the working directory at the front of sys.path. Drop it, so
    # the probe sees the same modules an installed ``pymanual`` command does,
    # and never imports a parent package that merely sits in the cwd.
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    sys.path[:] = [p for p in sys.path if p not in ("", cwd)]

    try:
        facts = gather(argv[1])
    except Exception:
        facts = {"probe": PROTOCOL, "error": traceback.format_exc()}
    json.dump(facts, out)
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
