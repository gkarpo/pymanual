"""Command-line entry point.

Prints the resolved URL (or a ``STATUS_*`` sentinel) on stdout and exits
non-zero when the result is a sentinel, so shell pipelines can branch on
``pymanual foo || ...``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import _cache, _target
from ._resolver import _STATUS_TOKENS, resolve_doc_url


def _enable_verbose() -> None:
    """Send pymanual's debug messages to stderr, keeping stdout for the result."""
    logger = logging.getLogger("pymanual")
    logger.setLevel(logging.DEBUG)
    if not any(getattr(h, "_pymanual_cli", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("pymanual: %(message)s"))
        handler._pymanual_cli = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pymanual",
        description="Resolve the best available documentation URL for a Python module.",
    )
    parser.add_argument(
        "module",
        nargs="?",
        help="Module name (e.g. 'json', 'requests', 'email.mime.text')",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="Disable the PyPI HTTP fallback for third-party modules.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the local cache (don't read from or write to it).",
    )
    parser.add_argument(
        "--python",
        metavar="PATH",
        default=_target.AUTO,
        help=(
            "Python interpreter or virtual environment to locate the module in. "
            f"Default: ${_target.ENV_VAR}, then the active virtual environment "
            "($VIRTUAL_ENV), then python3 on PATH."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Explain on stderr which interpreter was used and why.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete the local cache file and exit.",
    )
    args = parser.parse_args(argv)

    if args.clear_cache:
        removed = _cache.clear()
        print(f"Cleared cache at {_cache.cache_path()}" if removed else "Cache was already empty.")
        return 0

    if not args.module:
        parser.error("the following arguments are required: module")

    if args.verbose:
        _enable_verbose()

    try:
        result = resolve_doc_url(
            args.module,
            network=not args.no_network,
            use_cache=not args.no_cache,
            python=args.python,
        )
    except _target.InterpreterNotFound as exc:
        parser.error(f"--python: {exc}")
    except _target.TargetError as exc:
        print(f"pymanual: error: {exc}", file=sys.stderr)
        return 2
    print(result)
    return 1 if result in _STATUS_TOKENS else 0


if __name__ == "__main__":
    sys.exit(main())
