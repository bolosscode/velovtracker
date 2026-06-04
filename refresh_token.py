#!/usr/bin/env python3
"""
refresh_token.py — Récupère JWT + refresh_token depuis velov.grandlyon.com via Playwright.
Stocke dans data/token.json.
Lancé 1x/heure via refresh_token.yml.
"""
import json, sys, time, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

TOKEN_F = Path("data/token.json")
TOKEN_F.parent.mkdir(parents=True, exist_ok=True)

IAM_URL = "https://iam.cyclocity.fr/realms/vls-default/protocol/openid-connect/token"
CLIENT_ID = "vls-web-lyon"

def refresh_with_token(refresh_token):
    """Tente de renouveler le JWT via le refresh_token."""
    try:
        data = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': CLIENT_ID,
        }).encode()
        req = urllib.request.Request(IAM_URL, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            if 'access_token' in resp:
                print("  Token renouvelé via refresh_token", flush=True)
                return resp
    except Exception as e:
        print(f"  Refresh échoué: {e}", flush=True)
    return None

def get_token_playwright():
    """Récupère JWT + refresh_token via Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERREUR: playwright non installé"); sys.exit(1)

    state = {'access_token': None, 'refresh_token': None, 'token_type': 'Taknv1'}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )
        page = ctx.new_page()

        def on_response(r):
            if 'openid-connect/token' in r.url or 'cyclocity.fr' in r.url:
                try:
                    # Pour les appels token IAM
                    if 'openid-connect/token' in r.url:
                        body = r.json()
                        if 'access_token' in body:
                            state['access_token'] = body['access_token']
                            state['refresh_token'] = body.get('refresh_token')
                            print(f"  Token IAM capturé", flush=True)
                except: pass

        def on_request(r):
            auth = r.headers.get('authorization', '')
            if auth and 'cyclocity.fr' in r.url:
                # Extraire le token depuis le header Authorization
                parts = auth.split(' ', 1)
                if len(parts) == 2:
                    state['access_token'] = parts[1]
                    state['token_type'] = parts[0]
                    print(f"  Token capturé depuis requête cyclocity", flush=True)

        page.on('response', on_response)
        page.on('request', on_request)
        print("  Chargement velov.grandlyon.com…", flush=True)
        page.goto('https://velov.grandlyon.com', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(8000)
        browser.close()

    return state

def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] refresh_token", flush=True)
    t0 = time.time()

    # Essayer d'abord le refresh_token existant
    if TOKEN_F.exists():
        try:
            existing = json.loads(TOKEN_F.read_text())
            rt = existing.get('refresh_token')
            if rt:
                resp = refresh_with_token(rt)
                if resp:
                    token_data = {
                        'access_token': resp['access_token'],
                        'refresh_token': resp.get('refresh_token', rt),
                        'token_type': 'Taknv1',
                        'updated_at': datetime.now().isoformat(timespec='seconds'),
                        'expires_in': resp.get('expires_in', 3600),
                    }
                    TOKEN_F.write_text(json.dumps(token_data, ensure_ascii=False, indent=2))
                    print(f"  ✓ Token.json mis à jour ({time.time()-t0:.1f}s)", flush=True)
                    return
        except Exception as e:
            print(f"  Refresh_token existant invalide: {e}", flush=True)

    # Fallback: Playwright
    print("  Lancement Playwright…", flush=True)
    state = get_token_playwright()

    if not state['access_token']:
        print("ERREUR: token non trouvé"); sys.exit(1)

    token_data = {
        'access_token': state['access_token'],
        'refresh_token': state['refresh_token'],
        'token_type': state.get('token_type', 'Taknv1'),
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'expires_in': 3600,
    }
    TOKEN_F.write_text(json.dumps(token_data, ensure_ascii=False, indent=2))
    print(f"  ✓ Token.json créé ({time.time()-t0:.1f}s)", flush=True)

if __name__ == '__main__':
    main()
