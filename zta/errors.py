"""Error types for the ZTA MVP.

ZTA's policy is deny-by-default. `ZTAError` is the base for every
control-plane error so callers can catch them uniformly. Library modules
extend this hierarchy; they do not invent new base classes.
"""

from __future__ import annotations


class ZTAError(Exception):
    """Base class for all ZTA control plane errors."""


class IdentityError(ZTAError):
    """Identity issuance, lookup, rotation, or revocation failed."""


class TokenError(ZTAError):
    """Token issuance, refresh, introspection, or revocation failed."""


class PolicyError(ZTAError):
    """Policy file could not be loaded or parsed."""


class AuditError(ZTAError):
    """Audit append or integrity verification failed."""


class ToolError(ZTAError):
    """Tool registry or invocation failed."""
