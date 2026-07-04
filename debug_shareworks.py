import os
from providers.shareworks import ShareworksProvider

# Accepts SHAREWORKS_USER / SHAREWORKS_PASS env vars for debugging
# without needing 1Password signed in. Remove before committing.
os.environ.setdefault("_SW_USER_OVERRIDE", os.environ.get("SHAREWORKS_USER", ""))
os.environ.setdefault("_SW_PASS_OVERRIDE", os.environ.get("SHAREWORKS_PASS", ""))

p = ShareworksProvider({
    "1password_item": "Shareworks",
    "url":            "https://shareworks.solium.com/solium/servlet/userLogin.do",
    "symbol":         os.environ.get("SHAREWORKS_SYMBOL", "ACME"),
    "debug":          True,
    "_user_override": os.environ.get("SHAREWORKS_USER", ""),
    "_pass_override": os.environ.get("SHAREWORKS_PASS", ""),
})

try:
    result = p.fetch()
    print("SUCCESS:", result)
except Exception as e:
    print("ERROR:", e)
