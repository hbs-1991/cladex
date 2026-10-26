"""Everything this module refuses, in one place.

The bases are the ones the callers catch (`except ValueError`, `except
ManifestError`), so they are kept exactly: changing a base here would silently
stop an `except` clause from matching.
"""
from __future__ import annotations


class ManifestError(ValueError):
    """A manifest is malformed, or asks for something we refuse to run."""


class DraftError(ValueError):
    """The drafted text is not a manifest we could even read."""


class ComposeError(ValueError):
    """This extension cannot be turned into a runnable spec right now."""


class SecretNameError(ValueError):
    """The name or the value would corrupt the file, or is not an env-var name."""


class InstallError(RuntimeError):
    """The plugin could not be installed. Nothing was left on disk."""
