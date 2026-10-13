"""
One-time Plaid setup: run this to link each financial institution.
After linking, access_tokens are saved to ~/.finance-viz/plaid_tokens.json (chmod 600).
The refresh script reads tokens from there; credentials never touch disk.

Usage:
    python3 plaid_setup.py
Then open http://localhost:5001 in your browser and link each account.
"""

import json, os, stat, logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string

LOG_FILE = Path.home() / ".finance-viz" / "plaid_setup.log"

# First run on a fresh machine: the data dir doesn't exist yet, and the
# FileHandler below would crash at import time without it.
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

TOKEN_FILE = Path.home() / ".finance-viz" / "plaid_tokens.json"
SECRET_CACHE = Path.home() / ".finance-viz" / ".plaid_secret"

LINK_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Finance Viz — Link Accounts</title>
  <style>
    body { font-family: system-ui; background: #0f1117; color: #e1e4e8; display: flex;
           flex-direction: column; align-items: center; padding: 60px 20px; }
    h1 { margin-bottom: 6px; }
    .sub { color: #8b949e; margin-bottom: 28px; text-align: center; max-width: 680px; line-height: 1.5; }
    .section { background:#1c1f26; border:1px solid #2d3139; border-radius:12px; padding:20px; width:100%; max-width:720px; margin:0 0 18px; }
    .section h2 { font-size:0.95rem; margin:0 0 6px; }
    .section p { color:#8b949e; font-size:0.86rem; margin:0 0 14px; }
    button { background: #238636; color: #fff; border: none; padding: 14px 28px;
             border-radius: 8px; font-size: 1rem; cursor: pointer; margin: 8px; }
    button:hover { background: #2ea043; }
    button.secondary { background:#1f6feb; }
    button.secondary:hover { background:#388bfd; }
    .token-list { margin-top: 40px; width: 100%; max-width: 600px; }
    .token-item { background: #1c1f26; border: 1px solid #2d3139; border-radius: 8px;
                  padding: 12px 16px; margin-bottom: 8px; font-size: 0.85rem; }
    .token-item strong { color: #58a6ff; }
    .done { color: #3fb950; font-weight: 600; margin-top: 20px; }
  </style>
</head>
<body>
  <h1>Link Financial Accounts</h1>
  <p class="sub">
    Tokens are saved locally. Fidelity and Principal need investment holdings consent
    so the dashboard can refresh positions automatically at startup.
  </p>

  <div class="section">
    <h2>New / replacement links</h2>
    <p>Use these if an account is not linked yet or you want to replace its token.</p>
    <button onclick="launchLink('Chase Checking')">Link Chase</button>
    <button onclick="launchLink('Fidelity')">Link Fidelity</button>
    <button onclick="launchLink('Ally HYSA')">Link Ally</button>
    <button onclick="launchLink('Principal 401k')">Link Principal</button>
  </div>

  <div class="section">
    <h2>Grant holdings access to existing links</h2>
    <p>
      Use these once if Plaid says additional consent is required for investment holdings.
      This keeps the existing token and adds holdings permission.
    </p>
    <button class="secondary" onclick="launchUpdate('Fidelity')">Grant Fidelity holdings access</button>
    <button class="secondary" onclick="launchUpdate('Principal 401k')">Grant Principal holdings access</button>
  </div>

  <div class="token-list" id="linked"></div>

  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <script>
    async function launchLink(label) {
      const resp = await fetch('/plaid/create_link_token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label })
      });
      const { link_token } = await resp.json();

      const handler = Plaid.create({
        token: link_token,
        onSuccess: async (public_token, metadata) => {
          const res = await fetch('/plaid/exchange_token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ public_token, label,
                                   institution: metadata.institution.name })
          });
          const data = await res.json();
          if (data.ok) {
            const el = document.getElementById('linked');
            el.innerHTML += `<div class="token-item">
              ✓ <strong>${data.institution}</strong> linked as "${data.label}"
            </div>`;
          }
        },
        onExit: (err) => { if (err) console.error(err); }
      });
      handler.open();
    }

    async function launchUpdate(label) {
      const resp = await fetch('/plaid/create_link_token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label, mode: 'update' })
      });
      const data = await resp.json();
      if (!data.link_token) {
        const el = document.getElementById('linked');
        el.innerHTML += `<div class="token-item">✗ <strong>${label}</strong>: ${data.error || 'Could not create update token'}</div>`;
        return;
      }

      const handler = Plaid.create({
        token: data.link_token,
        onSuccess: async () => {
          const el = document.getElementById('linked');
          el.innerHTML += `<div class="token-item">
            ✓ <strong>${label}</strong> holdings access granted. Run <code>./start.sh</code> again to refresh positions.
          </div>`;
        },
        onExit: (err) => { if (err) console.error(err); }
      });
      handler.open();
    }
  </script>
</body>
</html>"""

CONFIG_FILE = Path.home() / ".finance-viz" / "config.json"

def get_plaid_credentials():
    if not CONFIG_FILE.exists() or CONFIG_FILE.stat().st_size == 0:
        raise RuntimeError(f"No config found at {CONFIG_FILE}")
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    client_id = cfg.get('plaid_client_id', '').strip()
    if not client_id:
        raise RuntimeError("plaid_client_id missing from config.json")

    if SECRET_CACHE.exists():
        secret = SECRET_CACHE.read_text().strip()
        if secret:
            return client_id, secret

    # Pull secret from 1Password if configured
    op_item  = cfg.get('plaid_secret_1password_item')
    op_field = cfg.get('plaid_secret_1password_field', 'secret')
    if op_item:
        import subprocess
        try:
            result = subprocess.run(
                ['op', 'item', 'get', op_item, '--field', op_field],
                capture_output=True, text=True, check=True,
            )
            secret = result.stdout.strip()
            log.info("Plaid secret loaded from 1Password item '%s'", op_item)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs a name, path or id, never the credential value
        except Exception as e:
            log.warning("1Password fetch failed: %s — falling back to config value", e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs a name, path or id, never the credential value
            secret = cfg.get('plaid_secret', '')
    else:
        secret = cfg.get('plaid_secret', '').strip()

    if not secret:
        raise RuntimeError("Plaid secret not found in 1Password or config.json")
    return client_id, secret

def load_tokens():
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600 — owner read/write only

app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string(LINK_HTML)  # nosemgrep: python.flask.security.audit.render-template-string.render-template-string -- constant template, no user input

@app.route('/plaid/create_link_token', methods=['POST'])
def create_link_token():
    from plaid.api import plaid_api
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.products import Products
    from plaid.model.country_code import CountryCode
    import plaid

    client_id, secret = get_plaid_credentials()
    log.info("create_link_token called")
    configuration = plaid.Configuration(
        host=plaid.Environment.Production,
        api_key={'clientId': client_id, 'secret': secret},
    )
    api_client = plaid.ApiClient(configuration)
    client = plaid_api.PlaidApi(api_client)

    data = request.get_json(silent=True) or {}
    label = data.get("label", "")
    mode = data.get("mode", "")
    products = [Products('transactions')]
    if label in ("Fidelity", "Principal 401k"):
        products.append(Products('investments'))

    req_kwargs = {
        "user": LinkTokenCreateRequestUser(client_user_id='drew-pilarski'),
        "client_name": 'Finance Viz',
        "country_codes": [CountryCode('US')],
        "language": 'en',
    }
    if mode == "update":
        tokens = load_tokens()
        token_info = tokens.get(label)
        if not token_info:
            return jsonify({"error": f"No existing token for {label}; use Link {label} first."}), 400
        req_kwargs["access_token"] = token_info["access_token"]
        req_kwargs["additional_consented_products"] = [Products('investments')]
    else:
        req_kwargs["products"] = products

    req = LinkTokenCreateRequest(**req_kwargs)
    try:
        resp = client.link_token_create(req)
        log.info("link_token created OK")
        return jsonify({'link_token': resp.link_token})
    except Exception as e:
        log.error("link_token_create failed: %s", e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs a name, path or id, never the credential value
        return jsonify({'error': str(e)}), 500

@app.route('/plaid/exchange_token', methods=['POST'])
def exchange_token():
    from plaid.api import plaid_api
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    import plaid

    data = request.get_json()
    log.info("exchange_token called for label=%s institution=%s", data.get('label'), data.get('institution'))  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs a name, path or id, never the credential value
    client_id, secret = get_plaid_credentials()
    configuration = plaid.Configuration(
        host=plaid.Environment.Production,
        api_key={'clientId': client_id, 'secret': secret},
    )
    api_client = plaid.ApiClient(configuration)
    client = plaid_api.PlaidApi(api_client)

    try:
        resp = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=data['public_token'])
        )
        access_token = resp.access_token
        item_id = resp.item_id

        tokens = load_tokens()
        tokens[data['label']] = {
            'access_token': access_token,
            'item_id': item_id,
            'institution': data['institution'],
            'linked_at': datetime.utcnow().isoformat(),
        }
        save_tokens(tokens)
        log.info("token exchange OK — label=%s item_id=%s", data['label'], item_id)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs a name, path or id, never the credential value
        return jsonify({'ok': True, 'label': data['label'], 'institution': data['institution']})
    except Exception as e:
        log.error("exchange_token failed: %s", e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs a name, path or id, never the credential value
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Plaid account setup running at http://localhost:5001")
    print(f"Tokens will be saved to: {TOKEN_FILE}")
    print()
    try:
        get_plaid_credentials()
        print("✓ 1Password credentials found")
    except RuntimeError as e:
        print(f"✗ {e}")
        print("  Make sure you're signed into 1Password CLI: op signin")
    print()
    app.run(port=5001, debug=False)
