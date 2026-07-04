"""Transaction assembly: prefer Plaid sync, else rebuild from Chase PDFs.

load_transactions() is the single entry point used by the /api/data route.
"""

import re

from finance_config import PDF_DIR
from plaid_data import load_plaid_transactions
from statements import parse_cc, parse_checking


def load_transactions():
    plaid = load_plaid_transactions()
    if plaid is not None:
        return plaid

    all_txns = []
    seen = set()
    for path in sorted(PDF_DIR.glob("*.pdf")):
        key = re.sub(r'\s*\(\d+\)\s*$', '', path.stem).strip()
        if '5969' in path.stem:
            if key in seen:
                continue
            seen.add(key)
            all_txns.extend(parse_checking(path))
        elif '6599' in path.stem:
            if key not in seen:
                seen.add(key)
                all_txns.extend(parse_cc(path))
    all_txns.sort(key=lambda x: x['date'])
    return all_txns
