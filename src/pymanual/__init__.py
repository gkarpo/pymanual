"""pymanual: resolve the canonical documentation URL for a Python module.

Public API is :func:`resolve_doc_url`. When no URL can be determined the
function returns one of the ``STATUS_*`` sentinel strings rather than raising,
so callers can distinguish "resolved" from "deliberately un-resolvable" without
exception handling.
"""

from ._resolver import (
    STATUS_LOCAL,
    STATUS_NOT_FOUND,
    STATUS_UNRESOLVABLE_PATH,
    resolve_doc_url,
)

__all__ = [
    "resolve_doc_url",
    "STATUS_NOT_FOUND",
    "STATUS_UNRESOLVABLE_PATH",
    "STATUS_LOCAL",
]
