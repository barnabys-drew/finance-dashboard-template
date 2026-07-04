"""Parsers for downloaded bank/brokerage statements (PDF + CSV).

Legacy fallback path: when Plaid data isn't present, transactions and net-worth
are reconstructed from statement files in PDF_DIR. Each parser returns plain
dicts in the same shape the Plaid loaders produce.
"""

import csv
import re
from datetime import datetime

import pdfplumber

from finance_config import categorize, load_finance_config


def norm_amount(s):
    return float(s.replace(',', ''))


_MERCHANT_ALIASES = None

def merchant_name(desc):
    global _MERCHANT_ALIASES
    d = desc.upper()
    # Per-user display aliases from config ("merchant_aliases": {"PREFIX": "Label"}),
    # cached so the per-transaction loop doesn't re-read config.json.
    if _MERCHANT_ALIASES is None:
        _MERCHANT_ALIASES = {k.upper(): v for k, v in
                             load_finance_config().get("merchant_aliases", {}).items()}
    for prefix, label in _MERCHANT_ALIASES.items():
        if d.startswith(prefix):
            return label
    if 'DOORDASH' in d:           return 'DoorDash'
    if d.startswith('ANTHROPIC'): return 'Anthropic'
    if 'STEAM' in d:              return 'Steam'
    if d.startswith('TARGET'):    return 'Target'
    if d.startswith('METRA'):     return 'Metra'
    if 'SQUARE ENIX' in d:       return 'Square Enix'
    if d.startswith('ALLY BANK'): return 'Ally Bank (Savings)'
    if d.startswith('NIKE'):      return 'Nike'
    if d.startswith('LEGO'):      return 'LEGO'
    if 'STATE FARM' in d:         return 'State Farm Insurance'
    if 'NON-CHASE ATM WITHDRAW' in d: return 'ATM Withdrawal'
    # Generic cleanup
    out = re.sub(r'\s+(Web ID|PPD ID|ACH)[\s:]\S+', '', desc)
    out = re.sub(r'\s+[\d\-]{7,}', '', out)          # phone/ID numbers
    out = re.sub(r'\s+\w+\.(COM|NET|ORG|IO)\b', '', out, flags=re.I)
    out = re.sub(r'\s+(Purchase|Payment|Payroll|Transfer)\b.*$', '', out, flags=re.I)
    out = re.sub(r'\s+[A-Z]{2}\s*$', '', out.strip()).strip()
    return out[:32]


def parse_checking(path):
    txns = []
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    m = re.search(r'(\w+ \d+, \d{4})through(\w+ \d+, \d{4})', text)
    if not m:
        return []
    period_start = datetime.strptime(m.group(1), "%B %d, %Y")

    amt_pat = r'(-?\d{1,3}(?:,\d{3})*\.\d{2})'
    tx_re = re.compile(
        r'^(\d{2}/\d{2})\s+(?:\d{2}/\d{2}\s+)?(.+?)\s+' + amt_pat + r'\s+' + amt_pat + r'\s*$'
    )
    skip_re = re.compile(r'^(DATE|TRANSACTION|Beginning|Ending|AMOUNT|BALANCE)\b')

    in_txn = False
    for line in text.split('\n'):
        line = line.strip()
        if '*start*transaction detail' in line:
            in_txn = True
            continue
        if '*start*' in line and 'transaction detail' not in line:
            in_txn = False
            continue
        if not in_txn or not line or skip_re.match(line):
            continue

        m2 = tx_re.match(line)
        if not m2:
            continue

        date_str, desc, amt_str, _ = m2.groups()
        if 'Payment To Chase Card' in desc:
            continue

        mo, dy = map(int, date_str.split('/'))
        amount = norm_amount(amt_str)
        is_income = amount > 0

        txns.append({
            'date': f'{period_start.year}-{mo:02d}-{dy:02d}',
            'description': desc,
            'amount': abs(amount),
            'is_income': is_income,
            'category': 'Income' if is_income else categorize(desc),
            'account': 'Checking (5969)',
        })
    return txns


def parse_cc(path):
    txns = []
    m = re.search(r'statements-(\d+)', path.stem)
    acct = m.group(1) if m else 'CC'

    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    year_m = re.search(r'\b(20\d{2})\b', text)
    year = int(year_m.group(1)) if year_m else datetime.now().year

    amt_pat = r'(-?\d{1,3}(?:,\d{3})*\.\d{2})'
    tx_re = re.compile(r'^(\d{2}/\d{2})\s+(.+?)\s+' + amt_pat + r'\s*$')
    skip_lines = {
        'Date of',
        'Transaction Merchant Name or Transaction Description $ Amount',
        'Merchant Name or Transaction Description $ Amount',
    }

    in_purchases = False
    for line in text.split('\n'):
        line = line.strip()
        if not line or line in skip_lines:
            continue
        if line == 'PURCHASE':
            in_purchases = True
            continue
        if 'PAYMENTS AND OTHER CREDITS' in line:
            in_purchases = False
            continue
        if any(x in line for x in ('Totals Year-to-Date', 'Total fees charged', 'INTEREST CHARGES')):
            in_purchases = False
            continue
        if not in_purchases:
            continue

        m2 = tx_re.match(line)
        if not m2:
            continue

        date_str, desc, amt_str = m2.groups()
        amount = norm_amount(amt_str)
        if amount <= 0:
            continue

        mo, dy = map(int, date_str.split('/'))
        txns.append({
            'date': f'{year}-{mo:02d}-{dy:02d}',
            'description': desc,
            'amount': amount,
            'is_income': False,
            'category': categorize(desc),
            'account': f'Sapphire ({acct})',
        })
    return txns


def parse_fidelity_csv(path):
    accounts = {}
    date_m = re.search(r'(\w{3}-\d{2}-\d{4})', path.stem)
    snapshot_date = date_m.group(1) if date_m else path.stem

    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            acct_num = (row.get('Account Number') or '').strip()
            acct_name = (row.get('Account Name') or '').strip()
            if not acct_num or not acct_name or acct_num.startswith('"'):
                continue
            val_str = row.get('Current Value', '').replace('$', '').replace(',', '').strip()
            gain_str = row.get('Total Gain/Loss Dollar', '').replace('$', '').replace(',', '').replace('+', '').strip()
            cost_str = row.get('Cost Basis Total', '').replace('$', '').replace(',', '').strip()
            try:
                val = float(val_str)
            except ValueError:
                continue

            key = acct_num
            if key not in accounts:
                accounts[key] = {
                    'account_number': acct_num,
                    'account_name': acct_name,
                    'current_value': 0.0,
                    'total_gain_loss': 0.0,
                    'cost_basis': 0.0,
                    'positions': [],
                    'snapshot_date': snapshot_date,
                    'source': 'Fidelity',
                }
            accounts[key]['current_value'] += val
            try:
                accounts[key]['total_gain_loss'] += float(gain_str)
            except ValueError:
                pass
            try:
                accounts[key]['cost_basis'] += float(cost_str)
            except ValueError:
                pass
            sym = row.get('Symbol', '').replace('*', '').strip()
            desc = row.get('Description', '').strip()
            if sym and val > 0:
                accounts[key]['positions'].append({
                    'symbol': sym,
                    'description': desc,
                    'value': round(val, 2),
                })

    return list(accounts.values())


def parse_ally_csv(path):
    balance = 0.0
    interest = 0.0
    last_date = None
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = (row.get('Date') or '').strip()
            amt_str = (row.get(' Amount') or row.get('Amount') or '').strip()
            desc = (row.get(' Description') or row.get('Description') or '').strip()
            tx_type = (row.get(' Type') or row.get('Type') or '').strip()
            try:
                amt = float(amt_str)
            except ValueError:
                continue
            if tx_type == 'Deposit':
                balance += amt
                if 'Interest' in desc:
                    interest += amt
            elif tx_type == 'Withdrawal':
                balance -= amt
            if date_str and (last_date is None or date_str > last_date):
                last_date = date_str
    return {
        'account_name': 'Ally HYSA',
        'account_number': 'Ally',
        'current_value': round(balance, 2),
        'total_gain_loss': round(interest, 2),
        'cost_basis': round(balance - interest, 2),
        'positions': [{'symbol': 'HYSA', 'description': 'High-Yield Savings', 'value': round(balance, 2)}],
        'snapshot_date': last_date or 'unknown',
        'source': 'Ally',
    }


def parse_principal_pdf(path):
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    # Statement period end date
    period_m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,\s+(\d{4})\s*_\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d+),\s+(\d{4})', text)
    if period_m:
        end_date = f"{period_m.group(3)} {period_m.group(4)}, {period_m.group(5)}"
    else:
        end_date = "unknown"

    # Ending balance
    bal_m = re.search(r'Ending balance\s+\$([0-9,]+\.\d{2})', text)
    if not bal_m:
        bal_m = re.search(r'Account snapshot.*?\$([0-9,]+\.\d{2})', text, re.DOTALL)
    balance = float(bal_m.group(1).replace(',', '')) if bal_m else 0.0

    # Beginning balance for gain/loss calc
    beg_m = re.search(r'Beginning balance\s+\$([0-9,]+\.\d{2})', text)
    beginning = float(beg_m.group(1).replace(',', '')) if beg_m else 0.0
    gain_m = re.search(r'Gain/Loss\s+-?\$([0-9,]+\.\d{2})', text)
    gain = float(gain_m.group(1).replace(',', '')) if gain_m else 0.0

    # Plan name — anchored on the employer name when configured (employer_keywords)
    plan_name = "401(k) Plan"
    _emp = load_finance_config().get("employer_keywords", [])
    if _emp:
        plan_m = re.search(rf'({re.escape(_emp[0])}.*?(?:401\(K\)|PLAN))', text, re.I)
        if plan_m:
            plan_name = plan_m.group(1).title()

    return {
        'account_name': '401(k) — Principal',
        'account_number': 'Principal',
        'current_value': round(balance, 2),
        'total_gain_loss': round(gain, 2),
        'cost_basis': round(balance - gain, 2),
        'positions': [{'symbol': '401k', 'description': plan_name, 'value': round(balance, 2)}],
        'snapshot_date': end_date,
        'source': 'Principal',
        'note': 'Quarterly PDF — may lag current value',
    }
