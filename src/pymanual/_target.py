"""Choose which Python environment to resolve in, and run the probe there.

pymanual is usually installed in an isolated environment (``uv tool``,
``pipx``) that holds nothing but pymanual. Locating modules there would miss
everything the user actually works with, so the CLI resolves in the user's
environment instead: it runs ``_probe.py`` under that environment's
interpreter and reads back the facts as JSON.

Library callers keep the old behaviour by default (``python=None``, resolve
in this process) and opt in with an interpreter path or ``"auto"``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import _probe

log = logging.getLogger("pymanual")

AUTO = "auto"
ENV_VAR = "PYMANUAL_PYTHON"

# Below the VS Code extension's 15 s limit on the whole CLI call, so a slow
# target (a dotted name under a heavy parent package) falls back in time to
# still produce an answer.
PROBE_TIMEOUT = 10.0


class TargetError(RuntimeError):
    """The target environment could not answer.

    Raised as-is when a parent package of a dotted name fails to import in
    the target, mirroring the exception that would propagate in-process.
    """


class InterpreterNotFound(ValueError):
    """An explicitly requested interpreter doesn't exist or isn't runnable."""


class _Unavailable(TargetError):
    """The probe couldn't run at all: missing, crashed, timed out, garbled."""


@dataclass(frozen=True)
class Target:
    """Where to resolve: ``python=None`` means this process.

    ``strict`` targets were named by the caller and surface failures;
    auto-detected ones fall back to this process instead.
    """

    python: str | None
    strict: bool = True

    def cache_key(self, name: str) -> str:
        # The same import name can be a third-party package in one
        # environment and the user's own module in another, so cached URLs
        # are kept per interpreter. In-process keeps the bare name, which is
        # what cache files written before targets existed contain.
        return name if self.python is None else f"{self.python}\0{name}"


IN_PROCESS = Target(None)


def select(python: str | os.PathLike[str] | None) -> Target:
    """Turn the caller's ``python`` argument into a :class:`Target`.

    ``None`` → this process. ``"auto"`` → :func:`_detect`. Anything else is
    an interpreter path, a virtual environment directory, or a command name
    on ``PATH``; :class:`InterpreterNotFound` if it is none of those.
    """
    if python is None:
        return IN_PROCESS
    if python == AUTO:
        return _detect()
    exe = _interpreter_path(python)
    if exe is None:
        raise InterpreterNotFound(
            f"not a Python interpreter or virtual environment: {os.fspath(python)}"
        )
    return _target_for(exe, strict=True)


def gather(name: str, target: Target) -> tuple[dict[str, Any], Target]:
    """Return the probe facts for ``name`` and the target that produced them.

    The returned target differs from the one passed in only when a
    non-strict target couldn't run and resolution fell back to this process;
    callers use it to cache the result under the right key.
    """
    if target.python is None:
        return _probe.gather(name), target
    try:
        return _run_probe(target.python, name), target
    except _Unavailable as exc:
        if target.strict:
            raise
        log.debug("%s; falling back to pymanual's own environment", exc)
        return _probe.gather(name), IN_PROCESS


def _detect() -> Target:
    """Pick the interpreter the user is most likely working with."""
    candidates: list[tuple[str, str]] = []
    if override := os.environ.get(ENV_VAR):
        candidates.append((override, f"${ENV_VAR}"))
    if venv := os.environ.get("VIRTUAL_ENV"):
        candidates.append((venv, "$VIRTUAL_ENV"))
    for command in ("python3", "python"):
        if found := shutil.which(command, path=_path_without_own_bin()):
            candidates.append((found, f"{command} on PATH"))
            break

    for value, source in candidates:
        exe = _interpreter_path(value)
        if exe is None:
            log.debug("ignoring %s (%s): not a Python interpreter", value, source)
            continue
        target = _target_for(exe, strict=False)
        if target.python is None:
            log.debug("%s (%s) is pymanual's own environment", exe, source)
        else:
            log.debug("resolving with %s (%s)", exe, source)
        return target

    log.debug("no interpreter found; resolving in pymanual's own environment")
    return IN_PROCESS


def _path_without_own_bin() -> str:
    """``$PATH`` minus the directory holding pymanual's own interpreter.

    ``uvx pymanual`` and ``pipx run pymanual`` put their throwaway venv's
    ``bin`` first on ``PATH``, so a plain ``which python3`` would find
    pymanual's own environment — the one place the user's modules aren't.
    """
    own_bin = os.path.realpath(Path(sys.executable).parent)
    entries = os.environ.get("PATH", os.defpath).split(os.pathsep)
    return os.pathsep.join(e for e in entries if e and os.path.realpath(e) != own_bin)


def _interpreter_path(value: str | os.PathLike[str]) -> str | None:
    """Absolute path of the interpreter ``value`` names, or ``None``.

    Deliberately ``abspath``, not ``realpath``: every venv's ``bin/python``
    is a symlink to the same base interpreter, and following it would lose
    which environment was meant.
    """
    path = Path(value)
    if path.is_dir():
        for rel in ("bin/python", "Scripts/python.exe"):
            exe = path / rel
            if exe.is_file():
                return os.path.abspath(exe)
        return None
    if path.is_file() and os.access(path, os.X_OK):
        return os.path.abspath(path)
    if os.sep not in str(value) and (found := shutil.which(str(value))):
        return os.path.abspath(found)
    return None


def _target_for(exe: str, *, strict: bool) -> Target:
    return IN_PROCESS if _is_this_environment(exe) else Target(exe, strict=strict)


def _is_this_environment(exe: str) -> bool:
    """True when ``exe`` would see exactly the modules this process sees.

    Needs both checks: venv interpreters share their base's real path, and
    ``/usr/bin/python3.11`` shares ``/usr`` with ``/usr/bin/python3.12``.
    """
    same_binary = os.path.realpath(exe) == os.path.realpath(sys.executable)
    same_prefix = os.path.realpath(Path(exe).parent.parent) == os.path.realpath(sys.prefix)
    return same_binary and same_prefix


def _probe_source() -> str:
    return resources.files(__package__).joinpath("_probe.py").read_text(encoding="utf-8")


def _run_probe(python: str, name: str) -> dict[str, Any]:
    """Run the probe under ``python``; :class:`_Unavailable` if it can't answer.

    The source goes in through ``-c`` rather than as a file path, so
    pymanual's own package directory never lands on the target's
    ``sys.path``.
    """
    try:
        proc = subprocess.run(
            [python, "-c", _probe_source(), name],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise _Unavailable(f"{python} did not answer within {PROBE_TIMEOUT:g}s") from None
    except OSError as exc:
        raise _Unavailable(f"cannot run {python}: {exc}") from None

    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1:] or ["no error output"]
        raise _Unavailable(f"{python} exited with status {proc.returncode}: {detail[0]}")
    try:
        facts = json.loads(proc.stdout)
    except ValueError:
        raise _Unavailable(f"{python} returned unreadable output") from None
    if not isinstance(facts, dict) or facts.get("probe") != _probe.PROTOCOL:
        raise _Unavailable(f"{python} returned output from an incompatible probe")
    if "error" in facts:
        raise TargetError(f"locating {name!r} with {python} failed:\n{facts['error']}")
    return facts
