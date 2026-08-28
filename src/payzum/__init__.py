"""Placeholder for the official Payzum Python SDK.

This 0.0.1 release exists to reserve the package name. It intentionally ships
no client: publishing a half-working payment client would be worse than
publishing none. See the README for what v1 will contain.
"""

__version__ = "0.0.1"

#: Production API host. ``api.payzum.com`` does NOT serve the API.
BASE_URL = "https://merchant.payzum.com"

#: Sandbox host. Isolated data, separate API keys.
SANDBOX_URL = "https://staging.payzum.com"

__all__ = ["__version__", "BASE_URL", "SANDBOX_URL", "is_ready"]


def is_ready() -> bool:
    """Return ``False`` — this release is a name reservation, not a client."""
    return False
