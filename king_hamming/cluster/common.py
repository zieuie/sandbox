"""Shared protocol and content-addressed storage helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from blob_store import store_blob


# Encode protocol objects identically on every component.
def canonical_json(value: Any) -> bytes:
    """Return a canonical UTF-8 JSON encoding of value."""

    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


# Give a mathematical request a stable identity independent of a particular run.
def calculation_id(specification: dict[str, Any]) -> str:
    """Return the SHA-256 identity of a canonical calculation specification."""

    return hashlib.sha256(canonical_json(specification)).hexdigest()

