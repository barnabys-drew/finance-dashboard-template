"""Shared paths, category rules, and configuration loading.

Base module with no internal dependencies — everything else imports from here.
Personal merchant/keyword lists belong in config.json (`categories`,
`utility_keywords`); the defaults below are generic national merchants so a
fresh clone categorizes sensibly with zero configuration.
"""

import json
import os
from pathlib import Path

# Data locations, overridable via env vars — used for tests/screenshots against
# a throwaway fixture dir, and so no machine-specific path is hardcoded.
PDF_DIR   = Path(os.environ.get("FINANCE_VIZ_PDF_DIR", Path.home() / "Downloads"))
PLAID_DIR = Path(os.environ.get("FINANCE_VIZ_DIR", Path.home() / ".finance-viz"))

# Keyword → category rules (first match wins; substring match on the lowercased
# description). Replace wholesale via a "categories" object in config.json:
#   "categories": {"Category Name": ["keyword", ...], ...}   — order is precedence.
_DEFAULT_CATEGORIES = [
    ("Utilities",      ["comcast", "xfinity", "verizon", "at&t", "t-mobile"]),
    ("Insurance",      ["state farm", "geico", "allstate", "progressive"]),
    ("Food & Dining",  ["doordash", "dd *", "grubhub", "uber eats", "tst*",
                        "mcdonald", "chipotle", "chick-fil-a", "starbucks"]),
    ("Transportation", ["uber", "lyft", "shell", "exxon", "chevron", "amtrak", "gas &"]),
    ("Shopping",       ["amazon", "amzn", "nike", "best buy", "etsy"]),
    ("Subscriptions",  ["netflix", "spotify", "hulu", "claude.ai", "anthropic",
                        "apple.com/bill", "youtube", "1password"]),
    ("Entertainment",  ["steam", "playstation", "nintendo", "square enix"]),
    ("Cash / ATM",     ["atm withdraw"]),
    ("Bank Fees",      ["atm fee", "overdraft", "fee-with"]),
    ("Savings",        ["ally bank", "marcus", "transfer to savings"]),
    ("Investments",    ["interactive brok", "vanguard", "schwab"]),
]

COLORS = {
    "Housing": "#FF6B6B", "Utilities": "#4ECDC4", "Insurance": "#E17055",
    "Food & Dining": "#FF9F43", "Groceries": "#A8E063", "Transportation": "#54A0FF",
    "Shopping": "#F9CA24", "Subscriptions": "#9B59B6", "Entertainment": "#FFC312",
    "Health": "#00D2D3", "Cash / ATM": "#778CA3", "Bank Fees": "#EA2027",
    "Savings": "#00B894", "Investments": "#2ECC71", "Income": "#27AE60", "Other": "#95A5A6",
}

PLAID_CAT_MAP = {
    "FOOD_AND_DRINK":          "Food & Dining",
    "GROCERIES":               "Groceries",
    "GAS_STATIONS":            "Transportation",
    "TRANSPORTATION":          "Transportation",
    "MEDICAL":                 "Health",
    "PERSONAL_CARE":           "Health",
    "ENTERTAINMENT":           "Entertainment",
    "GENERAL_MERCHANDISE":     "Shopping",
    "CLOTHING_AND_APPAREL":    "Shopping",
    "HOME_IMPROVEMENT":        "Shopping",
    "RENT_AND_UTILITIES":      "Utilities",
    "INSURANCE":               "Insurance",
    "SUBSCRIPTION":            "Subscriptions",
    "BANK_FEES":               "Bank Fees",
}

# Plaid categories that represent internal transfers — exclude from expenses
SKIP_PLAID_CATS = {"TRANSFER_OUT", "TRANSFER_IN", "LOAN_PAYMENTS", "LOAN_DISBURSEMENTS"}


def load_finance_config():
    path = PLAID_DIR / "config.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _categories_from_config():
    """User-defined category rules from config.json, else the generic defaults.
    JSON objects preserve insertion order, so config order = match precedence."""
    user = load_finance_config().get("categories")
    if isinstance(user, dict) and user:
        return [(name, [k.lower() for k in kws]) for name, kws in user.items()]
    return _DEFAULT_CATEGORIES


CATEGORIES = _categories_from_config()


def categorize(desc):
    d = desc.lower()
    for cat, kws in CATEGORIES:
        if any(kw in d for kw in kws):
            return cat
    return "Other"


def _categorize_plaid(raw_name, plaid_cat):
    cat = categorize(raw_name)
    if cat != "Other":
        return cat
    return PLAID_CAT_MAP.get(plaid_cat, "Other")


# ---------------------------------------------------------------------------
# Insight-bucket assignment — the SINGLE source of truth for the dashboard's
# spending tiles. app.py (badge totals) and detect_subscriptions.py (recurring
# scan) both call assign_bucket so the two views can never drift apart: a charge
# that lands in the Insurance tile (e.g. State Farm) is the same charge the
# recurring scan attributes to Insurance, not "Subscriptions". The frontend reads
# the stamped t['bucket'] rather than re-deriving it (see static/app.js).
# ---------------------------------------------------------------------------

VALID_BUCKETS = {'baby', 'pet', 'ai', 'health', 'household', 'cash', 'electricity',
                 'food', 'subs', 'shopping', 'insurance', 'rent', 'transport',
                 'dining', 'entertainment', 'personal', 'other'}

# Human-readable tile label per bucket (mirrors the badge labels in app.js).
BUCKET_LABELS = {
    'food': 'Food Delivery', 'subs': 'Subscriptions', 'shopping': 'Shopping',
    'baby': 'Baby', 'ai': 'AI', 'cash': 'Cash Withdrawn', 'electricity': 'Utilities',
    'health': 'Health', 'pet': 'Pet', 'household': 'Household', 'dining': 'Dining',
    'rent': 'Rent', 'insurance': 'Insurance', 'transport': 'Transportation',
    'entertainment': 'Entertainment', 'personal': 'Personal Care', 'other': 'Other',
}

# Utility merchants peeled off before the broad RENT_AND_UTILITIES → rent rule,
# because Plaid lumps utility bills and rent under one primary category.
# Override with "utility_keywords" in config.json (add your local providers).
_DEFAULT_UTIL_KW = ('comed', 'con edison', 'duke energy', 'pg&e', 'national grid',
                    'verizon', 'comcast', 'xfinity', 'at&t', 't-mobile')


def bucket_keyword_lists(cfg):
    """Pre-lowercase the per-bucket keyword lists once so assign_bucket stays cheap
    in a hot loop. Returns the dict assign_bucket expects as its `kw` argument."""
    return {
        'baby':      [k.lower() for k in cfg.get('baby_keywords', [])],
        'pet':       [k.lower() for k in cfg.get('pet_keywords', [])],
        'ai':        [k.lower() for k in cfg.get('ai_keywords', [])],
        'household': [k.lower() for k in cfg.get('household_keywords', [])],
        'health':    [k.lower() for k in cfg.get('health_keywords', [])],
        'transport': [k.lower() for k in cfg.get('transport_keywords', [])],
        'utility':   [k.lower() for k in cfg.get('utility_keywords', _DEFAULT_UTIL_KW)],
        'overrides': cfg.get('bucket_overrides', {}),
    }


def assign_bucket(t, kw):
    """Assign one expense to a SINGLE insight bucket (most specific wins) so the
    tiles never double-count and every dollar lands somewhere (the 'other'
    catch-all makes the buckets exhaustive). `kw` comes from bucket_keyword_lists.

    Order is specificity, high → low. Notes on the tricky ones:
      • Household before Health so Walgreens (a household keyword Plaid tags
        MEDICAL) lands in Household; Pet before Health for the same reason (vet
        bills tag MEDICAL but belong in Pet).
      • Utility keywords before the RENT_AND_UTILITIES → rent rule so utilities
        peel off first, leaving rent.
      • DoorDash (delivery) is its own 'food' bucket, before the generic
        FOOD_AND_DRINK → dining rule so sit-down/pickup dining is separate.
    """
    low = t['description'].lower()
    pc  = t.get('plaid_category')
    cat = t.get('category')
    ov  = kw['overrides'].get(t.get('transaction_id'))
    if   ov in VALID_BUCKETS:                  return ov
    elif any(k in low for k in kw['baby']):    return 'baby'
    elif any(k in low for k in kw['pet']):     return 'pet'
    elif any(k in low for k in kw['ai']):      return 'ai'          # all AI spend (subscriptions + ad-hoc usage) — before subs so every AI dollar lands in the AI tile
    elif any(k in low for k in kw['household']): return 'household'  # groceries, Costco, Amazon, Walgreens
    elif pc == 'MEDICAL' or any(k in low for k in kw['health']):    return 'health'
    elif any(k in low for k in kw.get('utility', _DEFAULT_UTIL_KW)): return 'electricity'
    elif cat == 'Insurance':                   return 'insurance'
    elif cat == 'Subscriptions':               return 'subs'
    elif pc == 'RENT_AND_UTILITIES':           return 'rent'        # utilities already peeled off above
    elif 'doordash' in low or 'dd *' in low:   return 'food'        # delivery
    elif pc == 'FOOD_AND_DRINK':               return 'dining'      # restaurants / fast food
    elif pc in ('TRANSPORTATION', 'TRAVEL') or any(k in low for k in kw['transport']):
        return 'transport'   # transport_kws catch gas stations Plaid mislabels (Casey's, etc.)
    elif pc == 'ENTERTAINMENT':                return 'entertainment'
    elif pc == 'PERSONAL_CARE':                return 'personal'
    elif 'atm withdraw' in low:                return 'cash'   # matches "non-chase atm withdraw" etc.
    elif pc == 'GENERAL_MERCHANDISE' or cat == 'Shopping':  return 'shopping'
    else:                                       return 'other'
