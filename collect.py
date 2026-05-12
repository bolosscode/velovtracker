#!/usr/bin/env python3
"""
collect.py — Collecte des données Vélo'v Lyon.
Source : download.data.grandlyon.com (JSON, open data, sans clé).
Champs récupérés : vélos mécaniques ET électriques séparément.

Variable d'environnement (optionnelle) :
  STATION_IDS — IDs séparés par virgules, ex. "3006,6044,6016"
                Vide = toutes les stations Lyon.
"""

import os, json, sys, ast
import requests
from datetime import datetime, timezone

STATION_IDS = [
    int(s.strip())
    for s in os.environ.get("STATION_IDS", "").split(",")
    if s.strip().isdigit()
]

# Endpoint JSON Grand Lyon (pas de CORS côté serveur, pas de clé requise)
URL = "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?maxfeatures=-1&start=1"

def parse_avail(raw):
    """Extrait electricalBikes / mechanicalBikes depuis le champ JSON embarqué."""
    if not raw:
        return None, None
    try:
        # Le champ est un dict Python sérialisé en string (guillemets simples)
        d = ast.literal_eval(raw) if isinstance(raw, str) else raw
        av = d.get("availabilities", {})
        return av.get("electricalBikes"), av.get("mechanicalBikes")
    except Exception:
        return None, None

def main():
    print(f"Fetch : {URL}")
    try:
        resp = requests.get(URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERREUR fetch : {e}", file=sys.stderr)
        sys.exit(1)

    # L'API renvoie {"values": [...], "nb_results": N}
    rows = data.get("values") or data.get("features") or []
    if not rows:
        print("AVERTISSEMENT : aucune donnée retournée.", file=sys.stderr)
        sys.exit(1)

    if STATION_IDS:
        rows = [r for r in rows if r.get("number") in STATION_IDS]
        print(f"Filtre : {len(rows)} station(s) retenues")

    now      = datetime.now(timezone.utc)
    stations = []

    for r in rows:
        # Champ JSON embarqué (main_stands ou overflow contient les détails électrique)
        elec, meca = parse_avail(r.get("main_stands") or r.get("overflow_stands"))

        total_bikes = r.get("available_bikes", 0) or 0
        # Fallback si les champs électrique/mécanique sont absents
        if elec is None and meca is None:
            elec = 0
            meca = total_bikes

        stations.append({
            "number":                  r.get("number"),
            "name":                    r.get("name", ""),
            "available_bikes":         total_bikes,
            "available_bike_stands":   r.get("available_bike_stands", 0) or 0,
            "bike_stands":             r.get("bike_stands", 0) or 0,
            "electrical_bikes":        elec,
            "mechanical_bikes":        meca,
            "status":                  r.get("status", "OPEN"),
            "position": {
                "lat": r.get("lat"),
                "lng": r.get("lng"),
            },
        })

    snapshot = {
        "timestamp": now.isoformat(timespec="seconds"),
        "stations":  stations,
    }

    # data/latest.json
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # data/history/YYYY-MM-DD.json
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

    history.append(snapshot)
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

    print(f"[{now.isoformat(timespec='seconds')}] OK — {len(stations)} station(s) → {hist_path} ({len(history)} snapshots)")

if __name__ == "__main__":
    main()
