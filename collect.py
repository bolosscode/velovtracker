#!/usr/bin/env python3
"""
collect.py — Collecte des données Vélo'v Lyon via le WFS Grand Lyon (open data, sans clé).
Endpoint : https://data.grandlyon.com/geoserver/wfs

Variable d'environnement (optionnelle) :
  STATION_IDS  — IDs de stations séparés par virgules (variable GitHub Actions)
                 ex. "10002,10003,10007,10008,10015,10020,10025,10030"
                 Si absente ou vide, toutes les stations Lyon sont collectées.
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone
from urllib.parse import urlencode

# ── Config ────────────────────────────────────────────────────────────────────
STATION_IDS = [
    int(s.strip())
    for s in os.environ.get("STATION_IDS", "").split(",")
    if s.strip().isdigit()
]

WFS_BASE   = "https://data.grandlyon.com/geoserver/wfs"
WFS_PARAMS = {
    "SERVICE":      "WFS",
    "REQUEST":      "GetFeature",
    "VERSION":      "1.1.0",
    "TYPENAME":     "jcd_jcdecaux.jcdvelov",
    "outputformat": "geojson",
}

def build_url():
    return f"{WFS_BASE}?{urlencode(WFS_PARAMS)}"

def main():
    url = build_url()
    print(f"Fetch : {url}")

    try:
        resp = requests.get(url, timeout=30)
        print(f"HTTP {resp.status_code} | Content-Type: {resp.headers.get('Content-Type','?')}")
        print(f"Réponse (500 premiers chars) : {resp.text[:500]!r}")
        resp.raise_for_status()
        geojson = resp.json()
    except Exception as e:
        print(f"ERREUR fetch WFS : {e}", file=sys.stderr)
        sys.exit(1)

    features = geojson.get("features", [])
    if not features:
        print("AVERTISSEMENT : aucune feature retournée.", file=sys.stderr)
        sys.exit(1)

    # Filtre en Python si des IDs sont configurés
    if STATION_IDS:
        features = [f for f in features if f.get("properties", {}).get("number") in STATION_IDS]
        print(f"Filtre appliqué : {len(features)} station(s) sur {len(geojson.get('features', []))} totales")

    now      = datetime.now(timezone.utc)
    stations = []

    for f in features:
        p      = f.get("properties", {})
        coords = f.get("geometry", {}).get("coordinates", [None, None])
        # Capacité totale
        total  = p.get("bike_stands") or (
            (p.get("available_bikes") or 0) + (p.get("available_bike_stands") or 0)
        )
        # Statut : availabilitycode 4 = gris = fermé
        status = "CLOSED" if p.get("availabilitycode") == 4 else "OPEN"

        stations.append({
            "number":                p.get("number"),
            "name":                  p.get("name", ""),
            "available_bikes":       p.get("available_bikes", 0),
            "available_bike_stands": p.get("available_bike_stands", 0),
            "bike_stands":           total,
            "status":                status,
            "position": {
                "lat": coords[1] if len(coords) > 1 else None,
                "lng": coords[0] if len(coords) > 0 else None,
            },
        })

    snapshot = {
        "timestamp": now.isoformat(timespec="seconds"),
        "stations":  stations,
    }

    # ── data/latest.json ──────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))

    # ── data/history/YYYY-MM-DD.json ──────────────────────────────────────────
    today     = now.strftime("%Y-%m-%d")
    hist_dir  = "data/history"
    hist_path = f"{hist_dir}/{today}.json"
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

    print(f"[{now.isoformat(timespec='seconds')}] OK — {len(stations)} station(s) → {hist_path} ({len(history)} snapshots)")

if __name__ == "__main__":
    main()
