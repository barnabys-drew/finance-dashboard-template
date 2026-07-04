"""
Manual entry provider — for assets Plaid and Playwright can't reach.

Config block in ~/.finance-viz/config.json:
  {
    "type": "manual",
    "account_name": "Stock Options — MYCO",
    "symbol": "MYCO",
    "positions": [
      {"description": "Vested options",   "value": 12000.00, "vested": true},
      {"description": "Unvested options", "value": 8000.00,  "vested": false}
    ]
  }

Values must be updated manually in config.json when they change.
"""

from datetime import date
from .base import BaseProvider


class ManualProvider(BaseProvider):
    name = "manual"

    def fetch(self) -> dict:
        positions = self.config.get("positions", [])
        total = sum(float(p.get("value", 0)) for p in positions)
        return {
            "account_name":  self.config.get("account_name", "Manual Entry"),
            "current_value": round(total, 2),
            "positions": [
                {
                    "symbol":      self.config.get("symbol", "MANUAL"),
                    "description": p.get("description", ""),
                    "value":       round(float(p.get("value", 0)), 2),
                    "vested":      bool(p.get("vested", True)),
                }
                for p in positions
            ],
            "as_of": str(date.today()),
            "note":  "Manually maintained — update values in config.json",
        }
