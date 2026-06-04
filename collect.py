#!/usr/bin/env python3
"""
collect.py — Collecte temps réel Vélo'v Lyon.
- Fetch état complet → latest.json
- Ajoute delta dans today.json
- Reset today.json à 2h00 (heure Paris)
"""
import os, json, sys, csv, io, ast, requests
from datetime import datetime, timezone, timedelta

LIVE_URL = (
    "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov"
    "/all.csv?maxfeatures=-1&start=1"
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
RESET_HOUR  = 0  # reset à minuit heure locale Paris

def parse_stands(raw):
    if not raw or raw.strip() in ('', '""', "''"):
        return {}
    try:
        return ast.literal_eval(raw.strip().strip('"'))
    except Exception:
        pass
    try:
        return json.loads(raw.strip().strip('"').replace("'", '"'))
    except Exception:
        return {}

def safe_int(v):
    try: return int(v or 0)
    except Exception: return 0

def parse_station(row, today):
    tot   = parse_stands(row.get('total_stands', '') or row.get('main_stands', ''))
    tav   = tot.get('availabilities', {}) if isinstance(tot, dict) else {}
    bikes = safe_int(tav.get('bikes'))
    cap   = safe_int(tot.get('capacity') if isinstance(tot, dict) else 0)
    stands = (cap - bikes) if cap else safe_int(tav.get('stands'))
    if today >= VAE_LAUNCH:
        elec = safe_int(tav.get('electricalInternalBatteryBikes'))
        meca = safe_int(tav.get('mechanicalBikes')) + safe_int(tav.get('electricalRemovableBatteryBikes'))
    else:
        elec = 0
        meca = bikes
    try:
        lat = float(row.get('lat', '0').replace(',', '.'))
        lng = float(row.get('lng', '0').replace(',', '.'))
    except Exception:
        lat = lng = None
    return {
        "number": safe_int(row.get('number')), "name": row.get('name', ''),
        "available_bikes": bikes, "available_bike_stands": stands,
        "bike_stands": cap, "electrical_bikes": elec, "mechanical_bikes": meca,
        "status": row.get('status', 'OPEN'), "lat": lat, "lng": lng,
    }

def fetch_meteo():
    try:
        r = requests.get(METEO_URL, timeout=10)
        r.raise_for_status()
        cur = r.json().get('current', {})
        precip = cur.get('precipitation', 0) or 0
        return {"precipitation": round(precip, 2), "rain": precip > 0.1,
                "weathercode": cur.get('weathercode', 0) or 0,
                "temp": round(cur.get('temperature_2m', 0) or 0, 1)}
    except Exception:
        return None

def main():
    now    = datetime.now(timezone.utc)
    paris  = now + timedelta(hours=2)  # approximation UTC+2 (heure d'été)
    ts     = now.isoformat(timespec='seconds')
    today  = now.strftime('%Y-%m-%d')

    # ── Fetch live ────────────────────────────────────────────────────────────
    try:
        r = requests.get(LIVE_URL, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text.lstrip('\ufeff')), delimiter=';')
        stations = [parse_station(row, today) for row in reader]
        stations = [s for s in stations if s['number']]
    except Exception as e:
        print(f"ERREUR live : {e}", file=sys.stderr)
        sys.exit(1)

    meteo = fetch_meteo()

    # ── latest.json ───────────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    snapshot = {"timestamp": ts, "stations": stations}
    if meteo:
        snapshot["meteo"] = meteo
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # ── today.json ────────────────────────────────────────────────────────────
    FIELDS = ['b', 's', 'c', 'e', 'm', 'st']

    def st_compact(s):
        return {'n': s['number'], 'b': s['available_bikes'],
                's': s['available_bike_stands'], 'c': s['bike_stands'],
                'e': s['electrical_bikes'], 'm': s['mechanical_bikes'],
                'st': s['status']}

    curr_by_n = {s['number']: st_compact(s) for s in stations}

    # Reset si on est après RESET_HOUR et que le fichier date d'avant
    should_reset = paris.hour == RESET_HOUR and paris.minute < 2

    today_data = None
    if os.path.exists(TODAY_FILE) and not should_reset:
        try:
            with open(TODAY_FILE, encoding='utf-8') as f:
                today_data = json.load(f)
            # Vérifier que c'est bien du jour
            base_date = (today_data.get('base', {}).get('t', '') or '')[:10]
            if base_date != today:
                today_data = None  # date différente → reset
        except Exception:
            today_data = None

    if today_data is None:
        # Nouveau fichier today : snapshot complet en base
        today_data = {
            "base": {"t": ts, "s": list(curr_by_n.values())},
            "deltas": []
        }
        if meteo:
            today_data["base"]["meteo"] = meteo
    else:
        # Calculer le delta depuis le dernier état connu
        # Reconstruire l'état précédent depuis base + deltas
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
                for f in FIELDS:
                    if curr.get(f) != prev.get(f):
                        diff[f] = curr.get(f)
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
