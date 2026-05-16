#!/usr/bin/env python3
"""
bootstrap.py — Import historique Vélo'v depuis data.grandlyon.com
Écriture au fil de l'eau pour éviter d'exploser la RAM.
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

parser = argparse.ArgumentParser()
parser.add_argument("--user",     default="")
parser.add_argument("--password", default="")
parser.add_argument("--out",      default="data/history")
args = parser.parse_args()

auth = (args.user, args.password) if args.user else None
os.makedirs(args.out, exist_ok=True)

def slot(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        m = (dt.minute // 10) * 10
        s = dt.replace(minute=m, second=0, microsecond=0)
        return s.strftime("%Y-%m-%d"), s.isoformat(timespec="seconds")
    except Exception:
        return None, None

def parse_row(row):
    ms  = row.get("main_stands", {})
    tot = row.get("total_stands", {})
    tav = tot.get("availabilities", {}) if isinstance(tot, dict) else {}
    bikes  = tav.get("bikes", 0) or 0
    elec   = tav.get("electricalBikes", 0) or 0
    meca   = tav.get("mechanicalBikes", 0) or 0
    cap    = tot.get("capacity", 0) or 0 if isinstance(tot, dict) else 0
    stands = (cap - bikes) if cap else tav.get("stands", 0) or 0
    return {
        "n": row.get("number"), "b": bikes, "s": stands,
        "c": cap, "e": elec, "m": meca,
        "st": row.get("status", "OPEN"),
    }

# Buffer en mémoire — on flush toutes les FLUSH_EVERY pages
FLUSH_EVERY = 20  # ~100 000 lignes entre chaque flush
buffer = defaultdict(lambda: defaultdict(dict))  # [date][slot][number] = row
total  = 0
start  = 1
flushed_files = set()

def flush_buffer():
    global buffer
    for date_str, slots in buffer.items():
        path = os.path.join(args.out, f"{date_str}.json")
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
                continue
            new_snaps.append({
                "t": slot_str,
                "s": [parse_row(r) for r in stations_dict.values()]
            })
        if new_snaps:
            merged = sorted(
                existing + new_snaps,
                key=lambda s: s.get("timestamp") or s.get("t") or ""
            )
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, separators=(",",":"))
            flushed_files.add(date_str)
    buffer = defaultdict(lambda: defaultdict(dict))
    print(f"  → flush : {len(flushed_files)} jours écrits", flush=True)

page = 0
print("Téléchargement…", flush=True)
while True:
    url = BASE_URL.format(start=start)
    print(f"  start={start}…", flush=True)
    try:
        r = requests.get(url, auth=auth, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Erreur start={start}: {e}", file=sys.stderr)
        break

    rows = data.get("values", [])
    if not rows:
        break

    for row in rows:
        date_str, slot_str = slot(row.get("horodate", ""))
        if not date_str:
            continue
        buffer[date_str][slot_str][row.get("number")] = row

    total += len(rows)
    page  += 1
    print(f"  start={start} → {len(rows)} lignes | total={total}", flush=True)

    if page % FLUSH_EVERY == 0:
        flush_buffer()

    if not data.get("next") or len(rows) < 5000:
        break
    start += 5000
    time.sleep(0.2)

flush_buffer()
print(f"\n✓ {total} lignes → {len(flushed_files)} jours dans {args.out}/")
