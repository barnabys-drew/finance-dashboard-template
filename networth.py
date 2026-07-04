"""Net-worth assembly: prefer Plaid balances, else rebuild from statement files.

load_networth() is the single entry point used by the /api/networth route.
"""

import json
import re

from finance_config import PDF_DIR
from plaid_data import load_plaid_networth
from statements import parse_ally_csv, parse_fidelity_csv, parse_principal_pdf


def load_manual_balances():
    path = PDF_DIR / "manual_balances.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_networth():
    plaid = load_plaid_networth()
    if plaid is not None:
        return plaid

    snapshots = []
    # Fidelity — pick the most recent CSV
    seen_dates = set()
    for path in sorted(PDF_DIR.glob("Portfolio_Positions_*.csv"), reverse=True):
        date_m = re.search(r'(\w{3}-\d{2}-\d{4})', path.stem)
        key = date_m.group(1) if date_m else path.stem
        if key in seen_dates:
            continue
        seen_dates.add(key)
        snapshots.extend(parse_fidelity_csv(path))

    # Ally HYSA — transactions.csv
    ally_path = PDF_DIR / "transactions.csv"
    if ally_path.exists():
        snapshots.append(parse_ally_csv(ally_path))

    manual = load_manual_balances()

    # Principal 401(k) — prefer manual_balances.json (live); fall back to quarterly PDF
    if 'principal' in manual:
        p = manual['principal']
        snapshots.append({
            'account_name': '401(k) — Principal',
            'account_number': 'Principal',
            'current_value': p['balance'],
            'total_gain_loss': 0.0,
            'cost_basis': p['balance'],
            'positions': [{'symbol': '401k', 'description': 'Employer 401(k)', 'value': p['balance']}],
            'snapshot_date': p.get('as_of', 'unknown'),
            'source': 'Principal',
        })
    else:
        principal_path = PDF_DIR / "DEFINED_CONTRIBUTION_STATEMENTS.pdf"
        if principal_path.exists():
            snapshots.append(parse_principal_pdf(principal_path))

    # Shareworks (employer RSUs) — from manual_balances.json; replace with Playwright scrape
    if 'shareworks' in manual:
        sw = manual['shareworks']
        available = sw.get('available', 0)
        unavailable = sw.get('unavailable', 0)
        total = available + unavailable
        snapshots.append({
            'account_name': 'Shareworks — Employer RSUs',
            'account_number': 'Shareworks',
            'current_value': round(total, 2),
            'total_gain_loss': 0.0,
            'cost_basis': round(total, 2),
            'positions': [
                {'symbol': 'RSU', 'description': 'Common Stock (available)', 'value': round(available, 2), 'vested': True},
                {'symbol': 'RSU', 'description': 'Unvested RSUs (pre-tax)', 'value': round(unavailable, 2), 'vested': False},
            ],
            'snapshot_date': sw.get('as_of', 'unknown'),
            'source': 'Shareworks',
            'note': sw.get('note', ''),
        })

    return snapshots
