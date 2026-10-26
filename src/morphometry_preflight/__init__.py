"""Read-only checks for microscopy inputs before morphometric analysis."""

__version__ = "0.1.0"

from .audit import audit_manifest

__all__ = ["audit_manifest", "__version__"]
