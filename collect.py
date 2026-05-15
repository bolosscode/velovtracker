#!/usr/bin/env python3
"""
collect.py — Vélo'v Lyon, stockage allégé (champs utiles uniquement).
~60 octets/station/snapshot au lieu de ~2 Ko → ×30 de compression.
"""
import os, json, sys, requests
from datetime import datetime, timezone

URL = "https://download.data.grandlyon.com/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?maxfeatures=-1&start=1"

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

def main():
    print(f"Fetch : {URL}")
    try:
        resp = requests.get(URL, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("values", [])
    except Exception as e:
        print(f"ERREUR : {e}", file=sys.stderr); sys.exit(1)

    if not rows:
        print("Aucune donnée.", file=sys.stderr); sys.exit(1)

    now = datetime.now(timezone.utc)
    ts  = now.isoformat(timespec="seconds")

    # Snapshot allégé — uniquement les champs lus par l'app
    stations = []
    for r in rows:
        elec, meca = parse_avail(r.get("main_stands") or r.get("overflow_stands"))
        stations.append({
            "n":  r.get("number"),           # number
            "b":  r.get("available_bikes") or 0,
            "s":  r.get("available_bike_stands") or 0,
            "c":  r.get("bike_stands") or 0, # capacity
            "e":  elec,
            "m":  meca,
            "st": r.get("status","OPEN"),
            "la": r.get("lat"),
            "ln": r.get("lng"),
        })

    # latest.json — snapshot complet avec noms (pour la carte)
    full = []
    for r in rows:
        elec, meca = parse_avail(r.get("main_stands") or r.get("overflow_stands"))
        full.append({
            "number": r.get("number"),
            "name":   r.get("name",""),
            "available_bikes":       r.get("available_bikes") or 0,
            "available_bike_stands": r.get("available_bike_stands") or 0,
            "bike_stands":           r.get("bike_stands") or 0,
            "electrical_bikes": elec,
            "mechanical_bikes": meca,
            "status": r.get("status","OPEN"),
            "lat": r.get("lat"),
            "lng": r.get("lng"),
        })

    os.makedirs("data", exist_ok=True)
    with open("data/latest.json","w",encoding="utf-8") as f:
        json.dump({"timestamp":ts,"stations":full}, f, ensure_ascii=False, separators=(",",":"))

    # history — format compact
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

    history.append({"t": ts, "s": stations})

    with open(hist_path,"w",encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",",":"))

    size_kb = os.path.getsize(hist_path) // 1024
    print(f"[{ts}] OK — {len(stations)} stations → {hist_path} ({len(history)} snapshots, {size_kb} Ko)")

if __name__ == "__main__":
    main()
    # Snapshot allégé — uniquement les champs lus par l'app
    stations = []
    for r in rows:
        elec, meca = parse_avail(r.get("main_stands") or r.get("overflow_stands"))
        stations.append({
            "n":  r.get("number"),           # number
            "b":  r.get("available_bikes") or 0,
            "s":  r.get("available_bike_stands") or 0,
            "c":  r.get("bike_stands") or 0, # capacity
            "e":  elec,
            "m":  meca,
            "st": r.get("status","OPEN"),
            "la": r.get("lat"),
            "ln": r.get("lng"),
        })

    # latest.json — snapshot complet avec noms (pour la carte)
    full = []
    for r in rows:
        elec, meca = parse_avail(r.get("main_stands") or r.get("overflow_stands"))
        full.append({
            "number": r.get("number"),
            "name":   r.get("name",""),
            "available_bikes":       r.get("available_bikes") or 0,
            "available_bike_stands": r.get("available_bike_stands") or 0,
            "bike_stands":           r.get("bike_stands") or 0,
            "electrical_bikes": elec,
            "mechanical_bikes": meca,
            "status": r.get("status","OPEN"),
            "lat": r.get("lat"),
            "lng": r.get("lng"),
        })

    os.makedirs("data", exist_ok=True)
    with open("data/latest.json","w",encoding="utf-8") as f:
        json.dump({"timestamp":ts,"stations":full}, f, ensure_ascii=False, separators=(",",":"))

    # history — format compact
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

    history.append({"t": ts, "s": stations})

    with open(hist_path,"w",encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",",":"))

    size_kb = os.path.getsize(hist_path) // 1024
    print(f"[{ts}] OK — {len(stations)} stations → {hist_path} ({len(history)} snapshots, {size_kb} Ko)")

if __name__ == "__main__":
    main()
