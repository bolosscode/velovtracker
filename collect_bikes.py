#!/usr/bin/env python3
"""
collect_bikes.py — Collecte l'état de tous les vélos Vélo'v depuis l'API JCDecaux.
Écrit data/bikes/today.json (format événementiel) et data/bikes/latest.json.
Lancé toutes les minutes via cron-job.org.
"""
import os, sys, json, asyncio, time
from datetime import datetime, timezone, date
from pathlib import Path
import urllib.request, urllib.error

# ── Config ──────────────────────────────────────────────────────────────────
OUT_DIR   = Path("data/bikes")
TODAY_F   = OUT_DIR / "today.json"
LATEST_F  = OUT_DIR / "latest.json"
STATIONS_URL = "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?maxfeatures=-1&start=1"
BIKES_URL    = lambda n: f"https://api.cyclocity.fr/contracts/lyon/bikes?stationNumber={n}"
ACCEPT       = "application/vnd.bikes.v4+json"
BATCH_SIZE   = 20
TIMEOUT      = 10

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Token ────────────────────────────────────────────────────────────────────
def get_token():
    """Récupère le JWT depuis velov.grandlyon.com via Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERREUR: playwright non installé. pip install playwright && playwright install chromium")
        sys.exit(1)

    state = {'token': None}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )
        page = ctx.new_page()

        def on_req(r):
            auth = r.headers.get('authorization', '')
            if auth and 'cyclocity.fr' in r.url:
                state['token'] = auth

        page.on('request', on_req)
        page.goto('https://velov.grandlyon.com', wait_until='networkidle', timeout=30000)
        page.wait_for_timeout(3000)
        browser.close()

    if not state['token']:
        print("ERREUR: token non trouvé")
        sys.exit(1)
    return state['token']

# ── Fetch stations ────────────────────────────────────────────────────────────
def fetch_stations():
    req = urllib.request.Request(STATIONS_URL)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return [s['number'] for s in json.loads(r.read())['values']]

# ── Fetch bikes pour une station ─────────────────────────────────────────────
def fetch_station_bikes(station_number, token):
    req = urllib.request.Request(
        BIKES_URL(station_number),
        headers={'Authorization': token, 'Accept': ACCEPT, 'Content-Type': ACCEPT}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return []

# ── Normalize bike ────────────────────────────────────────────────────────────
def normalize(b):
    out = {
        'id':     b['number'],
        'type':   'E' if b['type'] == 'ELECTRICAL' else 'M',
        'st':     b.get('stationNumber'),
        'borne':  b.get('standNumber'),
        'status': b.get('status', 'UNKNOWN')[:1],  # A=available R=rented S=reserved
        'rating': round(b['rating']['value'], 1) if b.get('rating', {}).get('value') else None,
        'rev':    b.get('lastRevisionDateTime', '')[:10] or None,
        'trip':   b.get('lastTripDateTime', '')[:16] or None,
    }
    if b['type'] == 'ELECTRICAL' and b.get('battery'):
        out['batt'] = b['battery'].get('percentage')
    if b['type'] == 'MECHANICAL' and b.get('bikeBatteryMv'):
        out['mv'] = b.get('bikeBatteryMv', 0)
    return out

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Démarrage collect_bikes", flush=True)
    t0 = time.time()

    # Token
    token = get_token()
    print(f"  Token OK ({time.time()-t0:.1f}s)", flush=True)

    # Stations
    station_numbers = fetch_stations()
    print(f"  {len(station_numbers)} stations", flush=True)

    # Fetch bikes en batch
    current_bikes = {}  # id → normalized bike
    done = 0
    for i in range(0, len(station_numbers), BATCH_SIZE):
        batch = station_numbers[i:i+BATCH_SIZE]
        # Parallèle via threading
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE) as ex:
            results = list(ex.map(lambda n: fetch_station_bikes(n, token), batch))
        for bikes in results:
            for b in bikes:
                nb = normalize(b)
                current_bikes[nb['id']] = nb
        done += len(batch)
        print(f"  {done}/{len(station_numbers)} stations — {len(current_bikes)} vélos", flush=True)

    now_ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

    # ── Écrire latest.json ──
    LATEST_F.write_text(json.dumps({
        't': now_ts,
        'bikes': list(current_bikes.values())
    }, ensure_ascii=False, separators=(',', ':')))

    # ── Mettre à jour today.json ──
    today_str = date.today().isoformat()

    if TODAY_F.exists():
        today_data = json.loads(TODAY_F.read_text())
        # Vérifier que c'est bien aujourd'hui
        if today_data.get('date') != today_str:
            today_data = None
    else:
        today_data = None

    if today_data is None:
        # Premier snapshot du jour → base
        today_data = {
            'date': today_str,
            'base': {'t': now_ts, 'bikes': list(current_bikes.values())},
            'events': []
        }
        TODAY_F.write_text(json.dumps(today_data, ensure_ascii=False, separators=(',', ':')))
        print(f"  Nouveau today.json (base: {len(current_bikes)} vélos)", flush=True)
    else:
        # Reconstruire état précédent depuis base + events
        prev_bikes = {b['id']: b for b in today_data['base']['bikes']}
        for ev in today_data['events']:
            bid = ev['id']
            if bid not in prev_bikes:
                prev_bikes[bid] = {'id': bid}
            prev_bikes[bid].update({k: v for k, v in ev.items() if k not in ('id', 't')})

        # Calculer les deltas
        events = []
        TRACKED = ('st', 'borne', 'status', 'batt', 'mv', 'rating')
        for bid, cur in current_bikes.items():
            prev = prev_bikes.get(bid, {})
            diff = {'id': bid, 't': now_ts}
            for k in TRACKED:
                cv, pv = cur.get(k), prev.get(k)
                if cv != pv:
                    diff[k] = cv
            if len(diff) > 2:  # id + t + au moins un champ
                events.append(diff)

        # Vélos disparus
        for bid in prev_bikes:
            if bid not in current_bikes:
                events.append({'id': bid, 't': now_ts, 'status': 'X'})  # X = disparu

        if events:
            today_data['events'].extend(events)
            TODAY_F.write_text(json.dumps(today_data, ensure_ascii=False, separators=(',', ':')))
            print(f"  {len(events)} événements ajoutés (total: {len(today_data['events'])})", flush=True)
        else:
            print(f"  Aucun changement", flush=True)

    print(f"  Terminé en {time.time()-t0:.1f}s", flush=True)

if __name__ == '__main__':
    main()
