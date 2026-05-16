#!/usr/bin/env python3
"""
bootstrap.py — Import historique Vélo'v depuis data.grandlyon.com
Supporte la reprise (--resume-after DATE) et écrit done.txt quand terminé.
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
parser.add_argument("--user",         default="")
parser.add_argument("--password",     default="")
parser.add_argument("--out",          default="data/history")
parser.add_argument("--resume-after", default="", dest="resume_after",
                    help="reprendre après cette date YYYY-MM-DD (déjà dans l'historique)")
args = parser.parse_args()

auth = (args.user, args.password) if args.user else None
os.makedirs(args.out, exist_ok=True)

# Si reprise : trouver le start offset à partir du dernier fichier écrit
# L'API est paginée chronologiquement — on skip les pages déjà traitées
# en cherchant la première page qui contient des dates > resume_after
resume_date = args.resume_after  # ex: "2023-06-15"
if resume_date:
    print(f"Reprise après {resume_date}", flush=True)

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
    tot = row.get("total_stands", {})
    tav = tot.get("availabilities", {}) if isinstance(tot, dict) else {}
    bikes  = tav.get("bikes", 0) or 0
    elec   = tav.get("electricalBikes", 0) or 0
    meca   = tav.get("mechanicalBikes", 0) or 0
    cap    = (tot.get("capacity", 0) or 0) if isinstance(tot, dict) else 0
    stands = (cap - bikes) if cap else tav.get("stands", 0) or 0
    return {
        "n": row.get("number"), "b": bikes, "s": stands,
        "c": cap, "e": elec, "m": meca,
        "st": row.get("status", "OPEN"),
    }

FLUSH_EVERY = 20
buffer = defaultdict(lambda: defaultdict(dict))
total  = 0
start  = 1
flushed_files = set()
skipping = bool(resume_date)
finished = False

def flush_buffer():
    global buffer
    written = 0
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
            written += 1
    buffer = defaultdict(lambda: defaultdict(dict))
    if written:
        print(f"  → flush : {written} nouveaux jours ({len(flushed_files)} total)", flush=True)

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
        print(f"Erreur start={start}: {e}", file=sys.stderr, flush=True)
        break

    rows = data.get("values", [])
    if not rows:
        finished = True
        break

    # Mode reprise : sauter les pages jusqu'à trouver des données après resume_date
    if skipping and resume_date:
        page_dates = set()
        for row in rows:
            d, _ = slot(row.get("horodate", ""))
            if d:
                page_dates.add(d)
        if page_dates and max(page_dates) <= resume_date:
            print(f"  skip (max={max(page_dates)} <= {resume_date})", flush=True)
            start += 5000
            time.sleep(0.1)
            continue
        else:
            skipping = False
            print(f"  reprise active à start={start}", flush=True)

    for row in rows:
        date_str, slot_str = slot(row.get("horodate", ""))
        if not date_str:
            continue
        if resume_date and date_str <= resume_date:
            continue  # ignorer les données déjà importées
        buffer[date_str][slot_str][row.get("number")] = row

    total += len(rows)
    page  += 1
    print(f"  start={start} → {len(rows)} lignes | total={total}", flush=True)

    if page % FLUSH_EVERY == 0:
        flush_buffer()

    if not data.get("next") or len(rows) < 5000:
        finished = True
        break

    start += 5000
    time.sleep(0.2)

flush_buffer()
print(f"\n✓ {total} lignes → {len(flushed_files)} jours dans {args.out}/", flush=True)

if finished:
    print("TERMINÉ — tout l'historique importé.", flush=True)
    with open("done.txt", "w") as f:
        f.write("done")
else:
    print("PARTIEL — relance nécessaire.", flush=True)
