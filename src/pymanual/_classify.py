"""Classify a module's filesystem path as stdlib, third-party, or local.

Used by the resolver to decide whether to point at ``docs.python.org``,
follow the PyPI metadata path, or return a "no external docs" sentinel for
the user's own code.
"""

from __future__ import annotations

import os
import sysconfig
from typing import Literal

Classification = Literal["stdlib", "third_party", "local"]

_VENDOR_MARKERS = ("site-packages", "dist-packages")
_VENV_MARKERS = (
    os.sep + ".venv" + os.sep,
    os.sep + "venv" + os.sep,
    os.sep + ".tox" + os.sep,
)


def classify(path: str) -> Classification:
    """Return ``"stdlib"``, ``"third_party"``, or ``"local"`` for ``path``.

    Paths are resolved via ``realpath`` first so that symlinked venvs or
    ``/usr/lib`` → ``/usr/lib64`` indirection don't hide the real location.
    """
    p = os.path.realpath(path)
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
