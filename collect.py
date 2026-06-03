#!/usr/bin/env python3
"""
collect_bikes.py — Collecte tous les vélos JCDecaux et reconstruit les données stations.
Génère :
  data/latest.json          — stations (compatible avec l'existant)
  data/bikes/latest.json    — état live de tous les vélos
  data/bikes/today.json     — événements du jour (base + deltas)
"""
import os, sys, json, time, concurrent.futures
from datetime import datetime, timezone, date, timedelta
import time as _time
from pathlib import Path
import urllib.request, urllib.error

OUT_DIR   = Path("data/bikes")
TODAY_F   = OUT_DIR / "today.json"
BIKES_F   = OUT_DIR / "latest.json"
LATEST_F  = Path("data/latest.json")
META_F    = Path("data/stations_meta.json")

STATIONS_URL = "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?maxfeatures=-1&start=1"
BIKES_URL    = lambda n: f"https://api.cyclocity.fr/contracts/lyon/bikes?stationNumber={n}"
ACCEPT       = "application/vnd.bikes.v4+json"
TIMEOUT      = 15

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Token Playwright ─────────────────────────────────────────────────────────
def get_token():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERREUR: playwright non installé"); sys.exit(1)
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
        page.goto('https://velov.grandlyon.com', wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(5000)
        browser.close()
    if not state['token']:
        print("ERREUR: token non trouvé"); sys.exit(1)
    return state['token']

# ── Métadonnées stations ──────────────────────────────────────────────────────
def load_station_meta():
    """Charge les métadonnées stations (nom, lat, lng, capacité)."""
    if META_F.exists():
        return json.loads(META_F.read_text())
    # Fallback: fetch depuis Grand Lyon
    try:
        req = urllib.request.Request(STATIONS_URL)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            meta = {}
            for s in data.get('values', []):
                meta[s['number']] = {
                    'number': s['number'],
                    'name': s['name'],
                    'lat': s['lat'],
                    'lng': s['lng'],
                    'bike_stands': s['bike_stands']
                }
            META_F.write_text(json.dumps(meta, ensure_ascii=False, separators=(',', ':')))
            print(f"  Métadonnées: {len(meta)} stations", flush=True)
            return meta
    except Exception as e:
        print(f"  AVERT: métadonnées non disponibles ({e})", flush=True)
        return {}

# ── Fetch bikes d'une station ────────────────────────────────────────────────
def fetch_station_bikes(args):
    station_number, token = args
    req = urllib.request.Request(
        BIKES_URL(station_number),
        headers={'Authorization': token, 'Accept': ACCEPT, 'Content-Type': ACCEPT}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return station_number, json.loads(r.read())
    except Exception:
        return station_number, []

# ── Normaliser un vélo ────────────────────────────────────────────────────────
def normalize(b):
    out = {
        'id':     b['number'],
        'type':   'E' if b['type'] == 'ELECTRICAL' else 'M',
        'st':     b.get('stationNumber'),
        'borne':  b.get('standNumber'),
        'status': b.get('status', 'UNKNOWN')[:1],
        'rating': round(b['rating']['value'], 1) if b.get('rating', {}).get('value') else None,
        'rcount': b['rating'].get('count', 0) if b.get('rating') else 0,
        'rev':    b.get('lastRevisionDateTime', '')[:10] or None,
        'ctrl':   b.get('lastControlDateTime', '')[:10] or None,
        'trip':   b.get('lastTripDateTime', '')[:16] or None,
    }
    if b['type'] == 'ELECTRICAL' and b.get('battery'):
        out['batt'] = b['battery'].get('percentage')
    if b.get('bikeBatteryMv'):
        out['mv'] = b['bikeBatteryMv']
    return out

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] collect_bikes", flush=True)
    t0 = time.time()

    token = get_token()
    print(f"  Token OK ({time.time()-t0:.1f}s)", flush=True)

    meta = load_station_meta()
    station_numbers = [int(k) if str(k).isdigit() else k for k in meta.keys()] if meta else []

    # Fallback si meta vide
    if not station_numbers and BIKES_F.exists():
        try:
            prev = json.loads(BIKES_F.read_text())
            station_numbers = list(set(b['st'] for b in prev.get('bikes', []) if b.get('st')))
            print(f"  Fallback: {len(station_numbers)} stations depuis latest.json", flush=True)
        except: pass

    if not station_numbers:
        print("ERREUR: aucune station disponible"); sys.exit(1)

    # Fetch en parallèle
    print(f"  Fetch {len(station_numbers)} stations…", flush=True)
    bikes_by_station = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as ex:
        for stn, bikes in ex.map(fetch_station_bikes, [(n, token) for n in station_numbers]):
            bikes_by_station[stn] = bikes

    # Normaliser tous les vélos
    import zoneinfo; tz=zoneinfo.ZoneInfo('Europe/Paris')
    now_ts = datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S')  # heure Paris
    current_bikes = {}
    for bikes in bikes_by_station.values():
        for b in bikes:
            nb = normalize(b)
            current_bikes[nb['id']] = nb

    total_bikes = len(current_bikes)
    print(f"  {total_bikes} vélos ({time.time()-t0:.1f}s)", flush=True)

    # ── Écrire data/bikes/latest.json ──
    BIKES_F.write_text(json.dumps({
        't': now_ts,
        'bikes': list(current_bikes.values())
    }, ensure_ascii=False, separators=(',', ':')))

    # ── Reconstruire data/latest.json (format stations compatible) ──
    stations_live = []
    for stn_num, bikes in bikes_by_station.items():
        m = meta.get(stn_num, {}) or meta.get(str(stn_num), {}) or meta.get(int(stn_num) if str(stn_num).isdigit() else stn_num, {})
        if not m: continue
        available = [b for b in bikes if b.get('status') == 'AVAILABLE']
        elec = [b for b in available if b['type'] == 'ELECTRICAL']
        meca = [b for b in available if b['type'] == 'MECHANICAL']
        total_avail = len(available)
        capacity = m.get('bike_stands', 0)
        stations_live.append({
            'number': int(stn_num) if str(stn_num).isdigit() else stn_num,
            'name': m.get('name', ''),
            'available_bikes': total_avail,
            'available_bike_stands': max(0, capacity - total_avail),
            'bike_stands': capacity,
            'electrical_bikes': len(elec),
            'mechanical_bikes': len(meca),
            'status': 'OPEN',
            'lat': m.get('lat'),
            'lng': m.get('lng'),
        })

    LATEST_F.write_text(json.dumps({
        'timestamp': now_ts,
        'stations': stations_live
    }, ensure_ascii=False, separators=(',', ':')))
    print(f"  {len(stations_live)} stations reconstituées", flush=True)

    # ── Mettre à jour data/today.json (snapshots stations intraday) ──
    TODAY_STATIONS_F = Path("data/today.json")
    today_str = date.today().isoformat()

    # Snapshot compact : {number: [available_bikes, electrical_bikes, mechanical_bikes, available_bike_stands]}
    snap = {str(s['number']): [s['available_bikes'], s['electrical_bikes'], s['mechanical_bikes'], s['available_bike_stands']]
            for s in stations_live}

    ts_data = None
    if TODAY_STATIONS_F.exists():
        try:
            td = json.loads(TODAY_STATIONS_F.read_text())
            if td.get('date') == today_str:
                ts_data = td
        except: pass

    if ts_data is None:
        ts_data = {
            'date': today_str,
            'snapshots': [{
                't': now_ts,
                's': snap
            }]
        }
    else:
        ts_data['snapshots'].append({'t': now_ts, 's': snap})

    TODAY_STATIONS_F.write_text(json.dumps(ts_data, ensure_ascii=False, separators=(',', ':')))
    print(f"  today.json stations: {len(ts_data['snapshots'])} snapshots", flush=True)

    # ── Mettre à jour data/bikes/today.json ──
    today_str = date.today().isoformat()
    today_data = None
    if TODAY_F.exists():
        try:
            td = json.loads(TODAY_F.read_text())
            if td.get('date') == today_str:
                today_data = td
        except: pass

    if today_data is None:
        today_data = {
            'date': today_str,
            'base': {'t': now_ts, 'bikes': list(current_bikes.values())},
            'events': []
        }
        TODAY_F.write_text(json.dumps(today_data, ensure_ascii=False, separators=(',', ':')))
        print(f"  Nouveau today.json (base: {total_bikes} vélos)", flush=True)
    else:
        prev_bikes = {b['id']: b for b in today_data['base']['bikes']}
        for ev in today_data['events']:
            bid = ev['id']
            if bid not in prev_bikes: prev_bikes[bid] = {'id': bid}
            prev_bikes[bid].update({k: v for k, v in ev.items() if k not in ('id', 't')})

        TRACKED = ('st', 'borne', 'status', 'batt', 'mv', 'rating', 'rcount')
        events = []
        for bid, cur in current_bikes.items():
            prev = prev_bikes.get(bid, {})
            diff = {'id': bid, 't': now_ts}
            for k in TRACKED:
                if cur.get(k) != prev.get(k):
                    diff[k] = cur.get(k)
            if len(diff) > 2:
                events.append(diff)
        for bid in prev_bikes:
            if bid not in current_bikes:
                events.append({'id': bid, 't': now_ts, 'status': 'X'})

        if events:
            today_data['events'].extend(events)
            TODAY_F.write_text(json.dumps(today_data, ensure_ascii=False, separators=(',', ':')))
            print(f"  {len(events)} événements (total: {len(today_data['events'])})", flush=True)
        else:
            print(f"  Aucun changement", flush=True)

    print(f"  Done {time.time()-t0:.1f}s", flush=True)

if __name__ == '__main__':
    main()
