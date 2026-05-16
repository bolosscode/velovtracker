#!/usr/bin/env python3
"""
bootstrap.py — Import historique Vélo'v, fetch parallèle par chunks.
"""
import os, sys, json, argparse, time
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BASE_URL = (
    "https://data.grandlyon.com/fr/datapusher/ws/timeseries"
    "/jcd_jcdecaux.historiquevelov/all.json"
    "?compact=false&maxfeatures=5000&start={start}"
)
WORKERS   = 10   # requêtes parallèles
PAGE_SIZE = 5000

parser = argparse.ArgumentParser()
parser.add_argument("--user",         default="")
parser.add_argument("--password",     default="")
parser.add_argument("--out",          default="data/history")
parser.add_argument("--resume-after", default="", dest="resume_after")
args = parser.parse_args()

auth = (args.user, args.password) if args.user else None
os.makedirs(args.out, exist_ok=True)
resume_date = args.resume_after
print(f"Reprise après : {resume_date or 'début'}", flush=True)

# ── helpers ────────────────────────────────────────────────────────────────────
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
    return {"n": row.get("number"), "b": bikes, "s": stands,
            "c": cap, "e": elec, "m": meca, "st": row.get("status", "OPEN")}

def fetch_page(start):
    """Fetche une page, retourne (start, rows) ou (start, None) si erreur."""
    url = BASE_URL.format(start=start)
    for attempt in range(3):
        try:
            r = requests.get(url, auth=auth, timeout=30)
            r.raise_for_status()
            data = r.json()
            return start, data.get("values", []), bool(data.get("next"))
        except Exception as e:
            if attempt == 2:
                print(f"  ERREUR start={start}: {e}", flush=True)
                return start, [], False
            time.sleep(2)

# ── Phase 1 : découverte du nombre total de pages ─────────────────────────────
print("Découverte du volume total…", flush=True)
_, first_rows, _ = fetch_page(1)
if not first_rows:
    print("API vide ou inaccessible.", file=sys.stderr)
    sys.exit(1)

# Estimer le total via une requête de la dernière page connue
# On fait une binary search approx : on teste des offsets croissants
total_pages = 1
probe = PAGE_SIZE
while True:
    _, rows, has_next = fetch_page(probe + 1)
    if not rows:
        break
    total_pages = probe // PAGE_SIZE + 1
    if not has_next:
        break
    probe *= 2
    if probe > 50_000_000:
        break

# Liste de tous les starts à fetcher
all_starts = list(range(1, probe + PAGE_SIZE, PAGE_SIZE))
print(f"{len(all_starts)} pages estimées (~{len(all_starts)*PAGE_SIZE:,} lignes)", flush=True)

# ── Phase 2 : filtrer les pages déjà traitées (resume) ───────────────────────
# En mode reprise, on skip les pages dont toutes les dates sont <= resume_date
# Pour ça on scan séquentiellement les premières pages jusqu'à trouver la bonne

skip_until = 0  # index de page à partir duquel on fetch vraiment
if resume_date:
    print(f"Recherche du point de reprise (après {resume_date})…", flush=True)
    # Scan binaire : trouver la première page avec date > resume_date
    lo, hi = 0, len(all_starts) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        _, rows, _ = fetch_page(all_starts[mid])
        if not rows:
            hi = mid
            continue
        dates = [slot(r.get("horodate",""))[0] for r in rows if r.get("horodate")]
        dates = [d for d in dates if d]
        if dates and max(dates) <= resume_date:
            lo = mid + 1
        else:
            hi = mid
        time.sleep(0.2)
    skip_until = lo
    print(f"Reprise à la page index {skip_until} (start={all_starts[skip_until]})", flush=True)

starts_to_fetch = all_starts[skip_until:]
print(f"{len(starts_to_fetch)} pages à fetcher avec {WORKERS} workers", flush=True)

# ── Phase 3 : fetch parallèle + flush incrémental ────────────────────────────
FLUSH_EVERY = 50  # flush tous les 50 lots de WORKERS pages
buffer = defaultdict(lambda: defaultdict(dict))
total_rows = 0
flushed_files = set()
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
        new_snaps = [
            {"t": slot_str, "s": [parse_row(r) for r in stations_dict.values()]}
            for slot_str, stations_dict in sorted(slots.items())
            if slot_str not in existing_ts
        ]
        if new_snaps:
            merged = sorted(existing + new_snaps,
                            key=lambda s: s.get("timestamp") or s.get("t") or "")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, separators=(",",":"))
            flushed_files.add(date_str)
            written += 1
    buffer = defaultdict(lambda: defaultdict(dict))
    print(f"  → flush : {written} nouveaux jours ({len(flushed_files)} total)", flush=True)

batch_num = 0
i = 0
while i < len(starts_to_fetch):
    batch = starts_to_fetch[i:i+WORKERS]
    i += WORKERS
    batch_num += 1

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_page, s): s for s in batch}
        for fut in as_completed(futures):
            start, rows, has_next = fut.result()
            results[start] = (rows, has_next)

    batch_rows = 0
    all_empty = True
    for start in sorted(results):
        rows, has_next = results[start]
        if not rows:
            continue
        all_empty = False
        for row in rows:
            date_str, slot_str = slot(row.get("horodate", ""))
            if not date_str:
                continue
            if resume_date and date_str <= resume_date:
                continue
            buffer[date_str][slot_str][row.get("number")] = row
        batch_rows += len(rows)
        total_rows += len(rows)
        if not has_next:
            finished = True

    print(f"  batch {batch_num} ({batch[0]}…{batch[-1]}) → {batch_rows} lignes | total={total_rows:,}", flush=True)

    if all_empty:
        finished = True
        break

    if batch_num % FLUSH_EVERY == 0:
        flush_buffer()

flush_buffer()
print(f"\n✓ {total_rows:,} lignes → {len(flushed_files)} jours dans {args.out}/", flush=True)

if finished:
    print("TERMINÉ.", flush=True)
    with open("done.txt", "w") as f:
        f.write("done")
else:
    print("PARTIEL — relance nécessaire.", flush=True)
