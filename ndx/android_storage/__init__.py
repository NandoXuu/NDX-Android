"""Android-specific storage integrations (safe to import on desktop)."""
from .saf import (
    AndroidSAF, SAFUnavailable,
    REQUEST_OPEN, REQUEST_CREATE, REQUEST_TREE,
)

__all__ = [
    "AndroidSAF", "SAFUnavailable",
    "REQUEST_OPEN", "REQUEST_CREATE", "REQUEST_TREE",
]
