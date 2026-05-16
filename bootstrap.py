#!/usr/bin/env python3
"""
bootstrap.py — Import de l'historique Vélo'v depuis data.grandlyon.com
vers le format data/history/YYYY-MM-DD.json de l'app.

Usage :
  python bootstrap.py
  python bootstrap.py --user EMAIL --password MDP   # si auth requise
  python bootstrap.py --days 90                     # limiter la période

L'API est publique (pas d'auth nécessaire a priori).
Pagination automatique sur tous les enregistrements disponibles.
"""

import os, sys, json, argparse, time
from datetime import datetime, timezone
from collections import defaultdict
import requests

BASE_URL = (
    "https://data.grandlyon.com/fr/datapusher/ws/timeseries"
    "/jcd_jcdecaux.historiquevelov/all.json"
    "?compact=false&maxfeatures=5000&start={start}"
)

# ── Parsing ────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--user",     default="", help="login data.grandlyon.com")
parser.add_argument("--password", default="", help="mot de passe data.grandlyon.com")

parser.add_argument("--out",      default="data/history", help="répertoire de sortie")
args = parser.parse_args()

auth = (args.user, args.password) if args.user else None
# On importe tout — pas de filtre temporel
# --days n'est plus utilisé (l'API retourne déjà les données disponibles)
cutoff = None

os.makedirs(args.out, exist_ok=True)

# ── Fetch all pages ────────────────────────────────────────────────────────────
# Structure : { "YYYY-MM-DD": { "HH:MM": { station_number: row } } }
# On regroupe par (date, heure_arrondie_10min) pour reconstruire les snapshots
# car l'API retourne un enregistrement par station par horodate

all_rows = []
start = 1
total_fetched = 0

print("Téléchargement de l'historique…")
while True:
    url = BASE_URL.format(start=start)
    try:
        r = requests.get(url, auth=auth, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Erreur page start={start}: {e}", file=sys.stderr)
        break

    rows = data.get("values", [])
    if not rows:
        break



    all_rows.extend(rows)
    total_fetched += len(rows)
    nb_results = data.get("nb_results", 0)
    print(f"  start={start} → {len(rows)} lignes | total={total_fetched}/{nb_results}")

    if not data.get("next") or len(data.get("values", [])) < 5000:
        break

    start += 5000
    time.sleep(0.3)  # politesse

print(f"\n{total_fetched} enregistrements récupérés.")

# ── Regroupement par (date, snapshot ~10min) ───────────────────────────────────
# Clé : (date_str, slot_10min) → {number: row}
# slot_10min = heure arrondie au multiple de 10 min le plus proche

def slot(ts_str):
    """Retourne (date_str, slot_str) depuis un horodate ISO."""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Arrondi aux 10 minutes
        m = (dt.minute // 10) * 10
        slotted = dt.replace(minute=m, second=0, microsecond=0)
        return slotted.strftime("%Y-%m-%d"), slotted.isoformat(timespec="seconds")
    except Exception:
        return None, None

snapshots = defaultdict(lambda: defaultdict(dict))  # [date][slot][number] = row

for row in all_rows:
    date_str, slot_str = slot(row.get("horodate", ""))
    if not date_str:
        continue
    number = row.get("number")
    if number is None:
        continue
    snapshots[date_str][slot_str][number] = row

# ── Conversion vers le format de l'app ────────────────────────────────────────
def parse_row(row):
    """Convertit une ligne API vers le format station de l'app."""
    ms = row.get("main_stands", {})
    av = ms.get("availabilities", {}) if isinstance(ms, dict) else {}
    total = row.get("total_stands", {})
    tav = total.get("availabilities", {}) if isinstance(total, dict) else {}

    bikes       = tav.get("bikes", 0) or 0
    elec        = tav.get("electricalBikes", 0) or 0
    meca        = tav.get("mechanicalBikes", 0) or 0
    capacity    = (total.get("capacity", 0) or 0) if isinstance(total, dict) else 0
    stands_free = (capacity - bikes) if capacity else tav.get("stands", 0) or 0

    return {
        "number":                row.get("number"),
        "name":                  "",  # pas dans l'historique, sera complété par collect.py
        "available_bikes":       bikes,
        "available_bike_stands": stands_free,
        "bike_stands":           capacity,
        "electrical_bikes":      elec,
        "mechanical_bikes":      meca,
        "status":                row.get("status", "OPEN"),
    }

# ── Écriture des fichiers ──────────────────────────────────────────────────────
dates_written = 0
snapshots_written = 0

for date_str, slots in sorted(snapshots.items()):
    path = os.path.join(args.out, f"{date_str}.json")

    # Charger l'existant si le fichier existe déjà
    existing = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    existing_ts = {s.get("timestamp") or s.get("t") for s in existing}

    new_snaps = []
    for slot_str, stations_dict in sorted(slots.items()):
        if slot_str in existing_ts:
            continue  # déjà présent
        stations = [parse_row(r) for r in stations_dict.values()]
        new_snaps.append({
            "timestamp": slot_str,
            "stations":  stations,
        })
        snapshots_written += 1

    if not new_snaps:
        continue

    merged = sorted(existing + new_snaps, key=lambda s: s.get("timestamp") or s.get("t") or "")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(path) // 1024
    print(f"  {path} → {len(merged)} snapshots ({size_kb} Ko)")
    dates_written += 1

print(f"\n✓ {snapshots_written} nouveaux snapshots sur {dates_written} jours écrits dans {args.out}/")
print("Relancez collect.py pour compléter les noms de stations (champ 'name' vide dans l'historique).")
