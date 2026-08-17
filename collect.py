#!/usr/bin/env python3
"""
collect.py — Collecte temps réel Vélo'v Lyon.
Source : API JCDecaux v3 publique (remplace Grand Lyon CSV gelé depuis juin 2026).
Format de sortie identique — latest.json + today.json en base+deltas.
"""
import os, json, sys, requests
from datetime import datetime, timezone, timedelta

LIVE_URL = (
    "https://api.jcdecaux.com/vls/v3/stations"
    "?contract=lyon"
    "&apiKey=frifk0jbxfefqqniqez09tw4jvk37wyf823b5j1i"
)
METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=45.75&longitude=4.83"
    "&current=precipitation,rain,weathercode,temperature_2m"
    "&timezone=Europe%2FParis"
)
VAE_LAUNCH  = '2025-01-29'
TODAY_FILE  = 'data/today.json'
LATEST_FILE = 'data/latest.json'
RESET_HOUR  = 0  # reset à minuit heure Paris

def safe_int(v):
    try: return int(v or 0)
    except: return 0

def parse_station(row, today):
    """Parse une station depuis l'API JCDecaux v3.
    
    Format v3 :
      mainStands.availabilities.electricalBikes          = total élec (interne + amovible)
      mainStands.availabilities.electricalInternalBatteryBikes = élec interne seulement
      mainStands.availabilities.electricalRemovableBatteryBikes = élec amovible
      mainStands.availabilities.mechanicalBikes          = méca
    
    On garde e = electricalInternalBatteryBikes pour cohérence avec l'historique existant.
    m = mechanicalBikes + electricalRemovableBatteryBikes (même logique que l'ancien CSV).
    """
    stands = row.get('mainStands') or row.get('totalStands') or {}
    av     = stands.get('availabilities', {})
    cap    = safe_int(stands.get('capacity', 0))
    bikes  = safe_int(av.get('bikes', 0))
    free   = safe_int(av.get('stands', cap - bikes))

    if today >= VAE_LAUNCH:
        elec = safe_int(av.get('electricalInternalBatteryBikes', 0))
        meca = safe_int(av.get('mechanicalBikes', 0)) + safe_int(av.get('electricalRemovableBatteryBikes', 0))
    else:
        elec = 0
        meca = bikes

    pos = row.get('position', {})
    return {
        "number":                safe_int(row.get('number')),
        "name":                  row.get('name', ''),
        "available_bikes":       bikes,
        "available_bike_stands": free,
        "bike_stands":           cap,
        "electrical_bikes":      elec,
        "mechanical_bikes":      meca,
        "status":                row.get('status', 'OPEN'),
        "lat":                   pos.get('latitude'),
        "lng":                   pos.get('longitude'),
    }

def fetch_meteo():
    try:
        r = requests.get(METEO_URL, timeout=10)
        r.raise_for_status()
        cur    = r.json().get('current', {})
        precip = cur.get('precipitation', 0) or 0
        return {
            "precipitation": round(precip, 2),
            "rain":          precip > 0.1,
            "weathercode":   cur.get('weathercode', 0) or 0,
            "temp":          round(cur.get('temperature_2m', 0) or 0, 1),
        }
    except Exception:
        return None

def main():
    now   = datetime.now(timezone.utc)
    paris = now + timedelta(hours=2)   # approximation UTC+2 (heure d'été)
    ts    = now.isoformat(timespec='seconds')
    today = now.strftime('%Y-%m-%d')

    # ── Fetch live ────────────────────────────────────────────────────────
    try:
        r = requests.get(LIVE_URL, timeout=30)
        r.raise_for_status()
        stations = [parse_station(s, today) for s in r.json()]
        stations = [s for s in stations if s['number']]
    except Exception as e:
        print(f"ERREUR live : {e}", file=sys.stderr)
        sys.exit(1)

    meteo = fetch_meteo()

    # ── latest.json ───────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    snapshot = {"timestamp": ts, "stations": stations}
    if meteo:
        snapshot["meteo"] = meteo
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # ── today.json (base + deltas) ────────────────────────────────────────
    FIELDS = ['b', 's', 'c', 'e', 'm', 'st']

    def st_compact(s):
        return {
            'n': s['number'], 'b': s['available_bikes'],
            's': s['available_bike_stands'], 'c': s['bike_stands'],
            'e': s['electrical_bikes'], 'm': s['mechanical_bikes'],
            'st': s['status'],
        }

    curr_by_n = {s['number']: st_compact(s) for s in stations}

    should_reset = paris.hour == RESET_HOUR and paris.minute < 2

    today_data = None
    if os.path.exists(TODAY_FILE) and not should_reset:
        try:
            with open(TODAY_FILE, encoding='utf-8') as f:
                today_data = json.load(f)
            base_date = (today_data.get('base', {}).get('t', '') or '')[:10]
            if base_date != today:
                today_data = None   # date différente → reset
        except Exception:
            today_data = None

    if today_data is None:
        today_data = {
            "base": {"t": ts, "s": list(curr_by_n.values())},
            "deltas": [],
        }
        if meteo:
            today_data["base"]["meteo"] = meteo
    else:
        # Reconstruire l'état précédent
        prev_by_n = {st['n']: {**st} for st in today_data['base']['s']}
        for d in today_data['deltas']:
            for ch in d.get('d', []):
                n = ch['n']
                if n not in prev_by_n:
                    prev_by_n[n] = {'n': n}
                prev_by_n[n].update(ch)

        changed = []
        for n, curr in curr_by_n.items():
            prev = prev_by_n.get(n)
            if prev is None:
                changed.append(curr)
            else:
                diff = {'n': n}
                for field in FIELDS:
                    if curr.get(field) != prev.get(field):
                        diff[field] = curr.get(field)
                if len(diff) > 1:
                    changed.append(diff)
        for n in prev_by_n:
            if n not in curr_by_n:
                changed.append({'n': n, 'st': 'CLOSED'})

        delta = {"t": ts, "d": changed}
        if meteo:
            delta["meteo"] = meteo
        today_data["deltas"].append(delta)

    with open(TODAY_FILE, "w", encoding="utf-8") as f:
        json.dump(today_data, f, ensure_ascii=False, separators=(",", ":"))

    n_deltas = len(today_data.get('deltas', []))
    size_kb  = os.path.getsize(TODAY_FILE) // 1024
    print(f"[{ts}] OK — {len(stations)} stations | {n_deltas} deltas | today={size_kb}Ko")

if __name__ == "__main__":
    main()
