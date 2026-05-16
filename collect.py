#!/usr/bin/env python3
"""
collect.py — Collecte Vélo'v Lyon + météo actuelle (Open-Meteo, sans clé).
"""
import os, json, sys, requests
from datetime import datetime, timezone

VELOV_URL = "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?maxfeatures=-1&start=1"
METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=45.75&longitude=4.83"
    "&current=precipitation,rain,weathercode"
    "&timezone=Europe%2FParis"
)

def parse_avail(raw):
    if not raw:
        return 0, 0
    try:
        s = (raw if isinstance(raw, str) else json.dumps(raw)) \
            .replace("'", '"').replace("True","true").replace("False","false")
        d = json.loads(s)
        av = d.get("availabilities", {})
        return av.get("electricalBikes", 0), av.get("mechanicalBikes", 0)
    except Exception:
        return 0, 0

def fetch_meteo():
    """Retourne un dict météo simplifié ou None."""
    try:
        r = requests.get(METEO_URL, timeout=10)
        r.raise_for_status()
        cur = r.json().get("current", {})
        precip = cur.get("precipitation", 0) or 0
        code   = cur.get("weathercode", 0) or 0
        return {
            "precipitation": round(precip, 2),   # mm/h
            "rain":          precip > 0.1,        # booléen seuil 0.1 mm/h
            "weathercode":   code,                # WMO code
        }
    except Exception as e:
        print(f"AVERTISSEMENT météo : {e}", file=sys.stderr)
        return None

def main():
    print(f"Fetch Vélo'v : {VELOV_URL}")
    try:
        resp = requests.get(VELOV_URL, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("values", [])
    except Exception as e:
        print(f"ERREUR Vélo'v : {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("Aucune donnée.", file=sys.stderr)
        sys.exit(1)

    meteo = fetch_meteo()
    if meteo:
        print(f"Météo : {meteo['precipitation']} mm/h | rain={meteo['rain']} | code={meteo['weathercode']}")
    else:
        print("Météo indisponible — snapshot sans données météo")

    now = datetime.now(timezone.utc)
    ts  = now.isoformat(timespec="seconds")

    stations = []
    for r in rows:
        elec, meca = parse_avail(r.get("main_stands") or r.get("overflow_stands"))
        stations.append({
            "number":                r.get("number"),
            "name":                  r.get("name", ""),
            "available_bikes":       r.get("available_bikes") or 0,
            "available_bike_stands": r.get("available_bike_stands") or 0,
            "bike_stands":           r.get("bike_stands") or 0,
            "electrical_bikes":      elec,
            "mechanical_bikes":      meca,
            "status":                r.get("status", "OPEN"),
            "lat":                   r.get("lat"),
            "lng":                   r.get("lng"),
        })

    snapshot = {"timestamp": ts, "stations": stations}
    if meteo:
        snapshot["meteo"] = meteo

    # latest.json
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # history/YYYY-MM-DD.json — format compact
    today     = now.strftime("%Y-%m-%d")
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

    # Format compact pour l'history
    compact = {"t": ts, "s": [
        {"n": s["number"], "b": s["available_bikes"],
         "s": s["available_bike_stands"], "c": s["bike_stands"],
         "e": s["electrical_bikes"], "m": s["mechanical_bikes"],
         "st": s["status"], "la": s["lat"], "ln": s["lng"]}
        for s in stations
    ]}
    if meteo:
        compact["meteo"] = meteo

    history.append(compact)
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(hist_path) // 1024
    print(f"[{ts}] OK — {len(stations)} stations → {hist_path} ({len(history)} snapshots, {size_kb} Ko)")

if __name__ == "__main__":
    main()
