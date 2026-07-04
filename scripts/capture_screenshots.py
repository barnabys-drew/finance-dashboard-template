"""Capture dashboard screenshots from demo data for the README.

Spins up the Flask app against freshly generated sample data (never the real
~/.finance-viz), drives a headless Chromium with Playwright, and writes PNGs to
docs/screenshots/. Money concealment is turned off so the (fake) figures show.

Usage:
    python3 scripts/capture_screenshots.py
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gen_sample_data  # noqa: E402  (scripts/ on path via sys.path)

OUT_DIR = ROOT / "docs" / "screenshots"
VIEWPORT = {"width": 1480, "height": 1000}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for(url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="finance-viz-demo-"))
    gen_sample_data.main(data_dir)

    port = _free_port()
    env = {**os.environ, "FINANCE_VIZ_DIR": str(data_dir), "PYTHONUNBUFFERED": "1"}
    # app.py hardcodes port 5000 in its __main__ block, so serve via a tiny
    # runner that imports the app and binds the chosen free port instead.
    runner = f"from app import app; app.run(port={port}, host='127.0.0.1')"
    server = subprocess.Popen(
        [sys.executable, "-c", runner], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for(f"{base}/api/networth"):
            raise SystemExit("server did not come up")

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
            ctx.add_init_script("localStorage.setItem('concealMoney','0')")
            page = ctx.new_page()

            page.goto(base, wait_until="networkidle")
            page.wait_for_selector(".insight-block", timeout=15000)
            page.wait_for_timeout(1200)  # let charts/count-ups settle
            page.screenshot(path=str(OUT_DIR / "spending.png"), full_page=True)
            print("wrote", OUT_DIR / "spending.png")

            page.click("#tab-networth")
            page.wait_for_timeout(1800)  # net-worth fetch + chart render
            page.screenshot(path=str(OUT_DIR / "networth.png"), full_page=True)
            print("wrote", OUT_DIR / "networth.png")

            browser.close()
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
