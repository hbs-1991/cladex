"""Errors the telegram module raises or catches across a port boundary."""
from __future__ import annotations

from claude_hermes.core.exceptions import InvalidInput


class SecretNameError(InvalidInput):
    """The name or the value would corrupt the secret store, or is not an
    environment-variable name.

    Declared here rather than imported from the extensions module: a port's
    failure mode is part of the contract the *consumer* states, and telegram may
    not import a sibling module. The adapter that satisfies `SecretStorePort`
    translates the provider's own error into this one.
    """
