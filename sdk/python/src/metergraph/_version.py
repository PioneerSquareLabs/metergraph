"""Installed SDK version, resolved once from package metadata."""

from __future__ import annotations

import importlib.metadata


try:
    SDK_VERSION = importlib.metadata.version("metergraph")
except Exception:
    SDK_VERSION = "0.2.0"
