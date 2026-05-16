#!/usr/bin/env python3
"""
bootstrap.py — Import historique Vélo'v, pipeline continu 20 workers.
"""
import os, sys, json, argparse, time
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

PAGE_SIZE = 10000
WORKERS   = 30

BASE_URL = (
    "https://data.grandlyon.com/fr/datapusher/ws/timeseries"
    "/jcd_jcdecaux.historiquevelov/all.json"
    "?compact=false&maxfeatures={size}&start={start}"
)

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
    url = BASE_URL.format(size=PAGE_SIZE, start=start)
    for attempt in range(3):
        try:
            r = requests.get(url, auth=auth, timeout=30)
            r.raise_for_status()
            data = r.json()
            rows = data.get("values", [])
            has_next = bool(data.get("next")) and len(rows) == PAGE_SIZE
            return start, rows, has_next
        except Exception as e:
            if attempt == 2:
                print(f"  ERREUR start={start}: {e}", flush=True)
                return start, [], False
            time.sleep(1)

def flush_buffer(buffer, flushed_files):
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
            {"t": slot_str, "s": [parse_row(r) for r in sd.values()]}
            for slot_str, sd in sorted(slots.items())
            if slot_str not in existing_ts
        ]
        if new_snaps:
            merged = sorted(existing + new_snaps,
                            key=lambda s: s.get("timestamp") or s.get("t") or "")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, separators=(",",":"))
            flushed_files.add(date_str)
            written += 1
    print(f"  → flush : {written} nouveaux jours ({len(flushed_files)} total)", flush=True)
    return defaultdict(lambda: defaultdict(dict))

# ── Estimer le point de départ ─────────────────────────────────────────────────
start_offset = 1
if resume_date:
    try:
        d0 = datetime(2023, 4, 6)
        d1 = datetime.strptime(resume_date, "%Y-%m-%d")
        days = (d1 - d0).days
        estimated_rows = days * 65000
        start_offset = max(1, (estimated_rows // PAGE_SIZE - 20) * PAGE_SIZE + 1)
        print(f"Démarrage estimé à start={start_offset:,}", flush=True)
    except Exception:
        pass

# ── Pipeline continu ────────────────────────────────────────────────────────────
buffer = defaultdict(lambda: defaultdict(dict))
flushed_files = set()
total_rows = 0
finished = False

t0 = time.time()
last_flush_time = t0
FLUSH_INTERVAL = 600  # flush RAM toutes les 10 min

with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    # Soumettre les WORKERS premières pages
    pending = {}
    next_start = start_offset
    
    # Remplir la queue initiale
    for _ in range(WORKERS):
        fut = ex.submit(fetch_page, next_start)
        pending[fut] = next_start
        next_start += PAGE_SIZE

    while pending:
        # Traiter le premier futur qui se termine
        done_fut = next(as_completed(pending))
        submitted_start = pending.pop(done_fut)
        start, rows, has_next = done_fut.result()

        if rows:
            dates_in_page = set()
            kept = 0
            for row in rows:
                date_str, slot_str = slot(row.get("horodate", ""))
                if not date_str:
                    continue
                dates_in_page.add(date_str)
                if resume_date and date_str <= resume_date:
                    continue
                buffer[date_str][slot_str][row.get("number")] = row
                kept += 1
            total_rows += len(rows)

            elapsed = time.time() - t0
            rate = total_rows / elapsed if elapsed > 0 else 0
            date_range = f"{min(dates_in_page)}…{max(dates_in_page)}" if dates_in_page else "?"
            print(f"  start={start:>12,} | dates={date_range} | kept={kept}/{len(rows)} | "
                  f"total={total_rows:>10,} | {rate:,.0f} l/s", flush=True)

        # Flush RAM périodiquement pour éviter OOM
        now = time.time()
        if now - last_flush_time >= FLUSH_INTERVAL:
            buffer = flush_buffer(buffer, flushed_files)
            last_flush_time = now

        if not has_next or not rows:
            finished = True
            # Annuler les futures en cours si on a fini
            for f in list(pending):
                f.cancel()
                pending.pop(f)
            break

        # Soumettre la prochaine page
        if has_next:
            fut = ex.submit(fetch_page, next_start)
            pending[fut] = next_start
            next_start += PAGE_SIZE



# Flush final
buffer = flush_buffer(buffer, flushed_files)

elapsed = time.time() - t0
print(f"\n✓ {total_rows:,} lignes en {elapsed:.0f}s "
      f"({total_rows/elapsed:.0f} lignes/s) → {len(flushed_files)} jours", flush=True)

if finished:
    print("TERMINÉ.", flush=True)
    with open("done.txt", "w") as f:
        f.write("done")
else:
    print("PARTIEL — relance nécessaire.", flush=True)
