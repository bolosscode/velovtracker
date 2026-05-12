#!/usr/bin/env python3
"""
collect.py — Collecte des données Vélo'v Lyon depuis l'API JCDecaux
et stockage en JSON pour GitHub Pages.

Variables d'environnement requises :
  JCDECAUX_API_KEY  — clé API JCDecaux (secret GitHub)
  STATION_IDS       — IDs de stations, séparés par virgules (variable GitHub)
                      ex. "10002,10003,10007,10008,10015,10020,10025,10030"
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY     = os.environ.get("JCDECAUX_API_KEY", "").strip()
STATION_IDS = [
    int(s.strip())
    for s in os.environ.get("STATION_IDS", "").split(",")
    if s.strip().isdigit()
]
CONTRACT    = "Lyon"
API_URL     = f"https://api.jcdecaux.com/vls/v1/stations?contract={CONTRACT}&apiKey={API_KEY}"

def main():
    if not API_KEY:
        print("ERREUR : JCDECAUX_API_KEY non défini.", file=sys.stderr)
        sys.exit(1)

    # ── Fetch ──────────────────────────────────────────────────────────────────
    try:
        resp = requests.get(API_URL, timeout=15)
        resp.raise_for_status()
        all_stations = resp.json()
    except Exception as e:
        print(f"ERREUR fetch API : {e}", file=sys.stderr)
        sys.exit(1)

    # ── Filtrer les stations voulues ───────────────────────────────────────────
    if STATION_IDS:
        stations = [s for s in all_stations if s["number"] in STATION_IDS]
        if not stations:
            print(f"AVERTISSEMENT : aucune station trouvée pour {STATION_IDS}")
    else:
        stations = all_stations  # tout Lyon si aucun filtre

    # ── Construire le snapshot ────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    snapshot = {
        "timestamp": now.isoformat(timespec="seconds"),
        "stations": [
            {
                "number":                  s["number"],
                "name":                    s["name"],
                "available_bikes":         s["available_bikes"],
                "available_bike_stands":   s["available_bike_stands"],
                "bike_stands":             s["bike_stands"],
                "status":                  s["status"],
                "position": {
                    "lat": s["position"]["lat"],
                    "lng": s["position"]["lng"],
                },
            }
            for s in stations
        ],
    }

    # ── Écrire data/latest.json ───────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # ── Appender à data/history/YYYY-MM-DD.json ───────────────────────────────
    today      = now.strftime("%Y-%m-%d")
    hist_dir   = "data/history"
    hist_path  = f"{hist_dir}/{today}.json"
    os.makedirs(hist_dir, exist_ok=True)

    history: list = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except (json.JSONDecodeError, IOError):
            history = []

    history.append(snapshot)

    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

    n = len(snapshot["stations"])
    print(f"[{now.isoformat(timespec='seconds')}] OK — {n} station(s) → {hist_path} ({len(history)} snapshots)")

if __name__ == "__main__":
    main()
