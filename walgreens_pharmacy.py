"""
Walgreens pharmacy scraper — pulls Rx fill history (date + amount paid) so the
dashboard can split pharmacy spend (→ Health) from front-store spend (→
Household) at Walgreens. The bank feed can't tell them apart: every Walgreens
swipe arrives as merchant "Walgreens", category MEDICAL, with no line items.

Data source: the logged-in "Prescription Records" report at
  https://www.walgreens.com/rx-settings/print-rx
which lists, per fill: medication, quantity, fill date, amount **paid** (copay),
and the insurance savings. We keep {date, amount(paid), rx}.

Auth: connect to the user's REAL Chrome over CDP (port 9222) — same approach as
providers/shareworks.py. walgreens.com has aggressive bot detection that a real
logged-in session sidesteps; we never automate the login. When the session has
expired the scraper raises, and start.sh / the /finance flow prompts a manual
re-login (the records page bounces to a Sign In screen).

Output: ~/.finance-viz/walgreens_pharmacy.json
  { "as_of": "2026-06-27",
    "fills": [ {"date": "2026-06-24", "amount": 0.0, "rx": "Omeprazole 20mg Capsules"}, ... ] }

Usage:
    python3 walgreens_pharmacy.py            # scrape + write json
    python3 walgreens_pharmacy.py --debug    # + screenshot + verbose
    python3 walgreens_pharmacy.py --dry-run  # print, write nothing
"""

import re, sys, json, os, stat, time
from datetime import date, datetime
from pathlib import Path

BASE_DIR    = Path.home() / ".finance-viz"
OUTPUT_FILE = BASE_DIR / "walgreens_pharmacy.json"
DEBUG_SHOT  = BASE_DIR / "walgreens_records.png"
RECORDS_URL = "https://www.walgreens.com/rx-settings/print-rx"
CDP_PORT    = 9222

# One fill row in the Prescription Records table renders (via inner_text) as:
#   <med name>\nQty: <n>\n<MM/DD/YYYY>\nfill date\n$<paid>\nprice\n$<saved>\ninsurance saved you
# Capture the medication, fill date, and the amount paid (the copay that hits the
# bank). The trailing label text ("fill date"/"price") anchors the columns.
_ROW_RE = re.compile(
    r'(?P<name>[^\n]+?)\s*\n'
    r'Qty:\s*\d+\s*\n'
    r'(?P<date>\d{2}/\d{2}/\d{4})\s*\n'
    r'fill date\s*\n'
    r'\$(?P<paid>[\d,]+\.\d{2})\s*\n'
    r'price',
    re.IGNORECASE,
)


class WalgreensError(Exception):
    pass


def _connect(pw, debug=False):
    """Connect to the user's Chrome over CDP, trying the WSL2-friendly hosts."""
    candidates = ["localhost", "127.0.0.1"]
    host_ip = _wsl2_host_ip()
    if host_ip and host_ip not in candidates:
        candidates.append(host_ip)
    last_err = None
    for host in candidates:
        url = f"http://{host}:{CDP_PORT}"
        try:
            browser = pw.chromium.connect_over_cdp(url)
            if debug:
                print(f"[debug] connected via {url}")
            return browser
        except Exception as e:
            last_err = e
    raise WalgreensError(
        f"Could not connect to Chrome CDP on :{CDP_PORT}: {last_err}\n"
        "Launch Chrome with --remote-debugging-port=9222 and log into walgreens.com."
    )


def scrape(debug=False):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _connect(pw, debug)
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = None
            for p in ctx.pages:
                if "walgreens.com" in p.url.lower():
                    page = p
                    break
            if page is None:
                page = ctx.new_page()

            page.goto(RECORDS_URL, timeout=45_000)
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            time.sleep(5)  # records table renders client-side

            if "login" in page.url.lower() or "sign in" in page.title().lower():
                raise WalgreensError(
                    "Chrome is open but not logged into Walgreens.\n"
                    "Log in at walgreens.com (Pharmacy), then run again."
                )

            body = page.inner_text("body")
            if debug:
                try:
                    page.screenshot(path=str(DEBUG_SHOT), full_page=True)
                    print(f"[debug] screenshot saved to {DEBUG_SHOT}")
                except Exception:
                    pass

            fills = _parse_fills(body)
            if not fills:
                raise WalgreensError(
                    "Reached the records page but parsed 0 fills — the layout may "
                    "have changed. Run with --debug and inspect the screenshot."
                )
            return {"as_of": str(date.today()), "fills": fills}
        finally:
            # Never close — it's the user's real Chrome; closing drops the session.
            pass


def _parse_fills(body):
    fills = []
    for m in _ROW_RE.finditer(body):
        try:
            d = datetime.strptime(m.group("date"), "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        paid = float(m.group("paid").replace(",", ""))
        fills.append({"date": d, "amount": round(paid, 2), "rx": m.group("name").strip()})
    return fills


def _wsl2_host_ip():
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver"):
                    return line.split()[1].strip()
    except Exception:
        pass
    return ""


def _save(data):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(OUTPUT_FILE)
    os.chmod(OUTPUT_FILE, stat.S_IRUSR | stat.S_IWUSR)


def main():
    debug   = "--debug" in sys.argv
    dry_run = "--dry-run" in sys.argv
    data = scrape(debug=debug)
    paid = [f for f in data["fills"] if f["amount"] > 0]
    print(f"Walgreens — {len(data['fills'])} fills "
          f"({len(paid)} with a copay, total ${sum(f['amount'] for f in paid):,.2f})")
    if dry_run:
        for f in data["fills"]:
            print(f"  {f['date']}  ${f['amount']:>7.2f}  {f['rx']}")
    else:
        _save(data)
        print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
