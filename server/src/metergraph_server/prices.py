"""Server-side loader that resolves environment config and delegates to core.

The catalog data and parsing/pricing implementation live in ``metergraph_core``.
This module resolves the server's ``MG_PRICES_PATH`` and ``MG_REGION``
configuration and passes them to ``metergraph_core.load_catalog``. It retains
the historical ``load()`` three-item tuple and the ``PricesError`` / ``parse``
names for backwards compatibility. No catalog copy or pricing logic remains in
the server package.
"""

import os
from pathlib import Path
from typing import Any

from metergraph_core import (
    CatalogError,
    LoadedCatalog,
    load_catalog,
    parse_catalog,
)

# Backwards-compatible aliases for existing server imports.
PricesError = CatalogError
parse = parse_catalog

__all__ = ["PricesError", "load", "load_identity", "parse"]


def load_identity(
    path: str | Path | None = None, *, region: str | None = None
) -> LoadedCatalog:
    """Load the catalog, applying server environment configuration.

    ``MG_PRICES_PATH`` selects an alternate catalog file and ``MG_REGION``
    selects the pricing region. Explicit arguments take precedence over the
    environment. Core itself never reads these variables.
    """

    resolved_path = path if path is not None else (os.environ.get("MG_PRICES_PATH") or None)
    resolved_region = (
        region if region is not None else (os.environ.get("MG_REGION") or "global")
    )
    return load_catalog(resolved_path, region=resolved_region)


def load(
    path: str | Path | None = None, *, region: str | None = None
) -> tuple[str, dict[str, Any], Any]:
    """Return ``(version, document, snapshot)`` for backwards compatibility."""

    loaded = load_identity(path, region=region)
    return loaded.version, loaded.document, loaded.snapshot
