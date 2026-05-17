#!/usr/bin/env python3
"""
collect.py — Collecte événementielle Vélo'v Lyon.
Récupère uniquement les événements des 2 dernières minutes (overlap de sécurité).
Met à jour latest.json avec l'état complet et écrit les événements dans history.
"""
import os, json, sys, csv, io, ast, requests
from datetime import datetime, timezone, timedelta

EVENTS_URL = (
    "https://data.grandlyon.com/fr/datapusher/ws/timeseries"
    "/jcd_jcdecaux.historiquevelov/all.csv"
    "?maxfeatures=-1&horodate__gte={gte}&horodate__lt={lt}"
)
LIVE_URL = (
    "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov"
    "/all.csv?maxfeatures=-1&start=1"
)
METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=45.75&longitude=4.83"
    "&current=precipitation,rain,weathercode"
    "&timezone=Europe%2FParis"
)
VAE_LAUNCH = '2025-01-29'

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

def parse_elec_meca(tav, today):
    if today >= VAE_LAUNCH:
        elec = safe_int(tav.get('electricalInternalBatteryBikes'))
        meca = safe_int(tav.get('mechanicalBikes')) + safe_int(tav.get('electricalRemovableBatteryBikes'))
    else:
        elec = 0
        meca = safe_int(tav.get('bikes'))
    return elec, meca

def parse_live_row(row, today):
    tot   = parse_stands(row.get('total_stands', '') or row.get('main_stands', ''))
    tav   = tot.get('availabilities', {}) if isinstance(tot, dict) else {}
    bikes = safe_int(tav.get('bikes'))
    cap   = safe_int(tot.get('capacity') if isinstance(tot, dict) else 0)
    stands = (cap - bikes) if cap else safe_int(tav.get('stands'))
    elec, meca = parse_elec_meca(tav, today)
    try:
        lat = float(row.get('lat', '0').replace(',', '.'))
        lng = float(row.get('lng', '0').replace(',', '.'))
    except Exception:
        lat = lng = None
    return {
        "number": safe_int(row.get('number')), "name": row.get('name', ''),
        "available_bikes": bikes, "available_bike_stands": safe_int(row.get('available_bike_stands')),
        "bike_stands": safe_int(row.get('bike_stands')) or cap,
        "electrical_bikes": elec, "mechanical_bikes": meca,
        "status": row.get('status', 'OPEN'), "lat": lat, "lng": lng,
    }

def parse_event_row(row, today):
    """Parse une ligne de l'API timeseries (événement)."""
    tot   = parse_stands(row.get('total_stands', '') or row.get('main_stands', ''))
    tav   = tot.get('availabilities', {}) if isinstance(tot, dict) else {}
    bikes = safe_int(tav.get('bikes'))
    cap   = safe_int(tot.get('capacity') if isinstance(tot, dict) else 0)
    stands = (cap - bikes) if cap else safe_int(tav.get('stands'))
    elec, meca = parse_elec_meca(tav, today)
    return {
        'n': safe_int(row.get('number')), 'b': bikes, 's': stands,
        'c': cap, 'e': elec, 'm': meca, 'st': row.get('status', 'OPEN'),
    }

def fetch_meteo():
    try:
        r = requests.get(METEO_URL, timeout=10)
        r.raise_for_status()
        cur = r.json().get('current', {})
        precip = cur.get('precipitation', 0) or 0
        return {"precipitation": round(precip, 2), "rain": precip > 0.1,
                "weathercode": cur.get('weathercode', 0) or 0}
    except Exception as e:
        print(f"AVERTISSEMENT météo : {e}", file=sys.stderr)
        return None

def main():
    now   = datetime.now(timezone.utc)
    ts    = now.isoformat(timespec='seconds')
    today = now.strftime('%Y-%m-%d')

    # Fenêtre : 2 dernières minutes (overlap pour ne rien manquer)
    gte = (now - timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%S')
    lt  = now.strftime('%Y-%m-%dT%H:%M:%S')

    # ── 1. Événements de la dernière minute ───────────────────────────────────
    events_url = EVENTS_URL.format(gte=gte, lt=lt)
    print(f"Fetch événements : {gte} → {lt}")
    try:
        r = requests.get(events_url, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text), delimiter=';')
        events = list(reader)
        print(f"{len(events)} événements récupérés")
    except Exception as e:
        print(f"ERREUR événements : {e}", file=sys.stderr)
        events = []

    # ── 2. Snapshot live complet (pour latest.json) ───────────────────────────
    print(f"Fetch live complet...")
    try:
        r = requests.get(LIVE_URL, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text), delimiter=';')
        live_rows = [parse_live_row(row, today) for row in reader]
        live_rows = [s for s in live_rows if s['number']]
    except Exception as e:
        print(f"ERREUR live : {e}", file=sys.stderr)
        live_rows = []

    meteo = fetch_meteo()

    # ── 3. Écriture latest.json ───────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    snapshot = {"timestamp": ts, "stations": live_rows}
    if meteo:
        snapshot["meteo"] = meteo
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # ── 4. Écriture des événements dans history ───────────────────────────────
    if events:
        hist_dir  = "data/history"
        hist_path = f"{hist_dir}/{today}.json"
        os.makedirs(hist_dir, exist_ok=True)

        history = []
        if os.path.exists(hist_path):
            try:
                with open(hist_path, encoding="utf-8") as f:
                    history = json.load(f)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []

        # Grouper les événements par horodate (arrondi à la minute)
        from collections import defaultdict
        by_ts = defaultdict(dict)
        for row in events:
            horodate = row.get('horodate', '')
            try:
                dt = datetime.fromisoformat(horodate)
                # Arrondi à la minute
                slot_ts = dt.replace(second=0, microsecond=0).isoformat(timespec='seconds')
                num = row.get('number')
                if num:
                    by_ts[slot_ts][num] = parse_event_row(row, today)
            except Exception:
                continue

        existing_ts = {s.get('timestamp') or s.get('t') for s in history}
        new_snaps = [
            {"t": slot_ts, "s": list(sdict.values())}
            for slot_ts, sdict in sorted(by_ts.items())
            if slot_ts not in existing_ts
        ]

        if new_snaps:
            history.extend(new_snaps)
            history.sort(key=lambda s: s.get('timestamp') or s.get('t') or '')
            with open(hist_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
            size_kb = os.path.getsize(hist_path) // 1024
            print(f"[{ts}] OK — {len(new_snaps)} nouveaux snapshots → {hist_path} ({size_kb} Ko)")
        else:
            print(f"[{ts}] Pas de nouveaux événements")
    else:
        print(f"[{ts}] Aucun événement dans la fenêtre")

if __name__ == "__main__":
    main()
