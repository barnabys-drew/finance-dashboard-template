"""
Base class for equity/asset providers.

To add a new provider:
  1. Create providers/yourplatform.py
  2. Subclass BaseProvider and implement fetch()
  3. Add an entry to the "providers" list in ~/.finance-viz/config.json:
       {"type": "yourplatform", ...config options...}
  4. Register it in providers/__init__.py REGISTRY dict

fetch() must return a dict matching RESULT_SCHEMA below, or raise
ProviderError on failure (the refresh script will log and skip it).

RESULT_SCHEMA = {
    "account_name": str,          # display label, e.g. "Shareworks — ACME"
    "current_value": float,       # total market value (vested + unvested if desired)
    "positions": [
        {
            "symbol":      str,   # ticker or short label
            "description": str,   # human-readable name
            "value":       float, # market value of this lot
            "vested":      bool,  # True = available to sell/exercise
        }
    ],
    "as_of":  str,                # ISO date string
    "note":   str,                # optional — blackout notice, data-lag warning, etc.
}
"""


class ProviderError(Exception):
    pass


class BaseProvider:
    """
    Subclass this and implement fetch().
    The constructor receives the provider's config block from config.json.
    """
    name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    def fetch(self) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__} must implement fetch()")

    # ------------------------------------------------------------------
    # Credential helpers (require 1Password CLI — `op` must be in PATH)
    # ------------------------------------------------------------------

    def op_field(self, item: str, field: str) -> str:
        """Return a field value from a 1Password item."""
        import subprocess
        result = subprocess.run(
            ["op", "item", "get", item, "--field", field],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    def op_otp(self, item: str) -> str:
        """Return a live TOTP code from a 1Password item."""
        import subprocess
        result = subprocess.run(
            ["op", "item", "get", item, "--otp"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
