#!/usr/bin/env python3
"""
bootstrap.py — Import historique Vélo'v avec reprise via état persistant.
Lit data/bootstrap_state.json pour reprendre, écrit l'état à la fin.
Se coupe proprement avant la limite GitHub Actions (timeout paramétrable).
"""
import os, sys, json, argparse, time, signal
from datetime import datetime, timezone, date
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

PAGE_SIZE   = 5000
WORKERS     = 10
CUTOFF_DATE = '2023-01-01'  # ne garder que depuis 2023
STATE_FILE  = 'data/bootstrap_state.json'

parser = argparse.ArgumentParser()
parser.add_argument("--user",       default="")
parser.add_argument("--password",   default="")
parser.add_argument("--out",        default="data/history")
parser.add_argument("--max-minutes", type=int, default=100,
                    help="Arrêt propre après N minutes (défaut 100)")
args = parser.parse_args()

auth = (args.user, args.password) if args.user else None
os.makedirs(args.out, exist_ok=True)

DEADLINE = time.time() + args.max_minutes * 60
print(f"Deadline dans {args.max_minutes} min", flush=True)

# ── Helpers ────────────────────────────────────────────────────────────────────
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
    url = (f"https://data.grandlyon.com/fr/datapusher/ws/timeseries"
           f"/jcd_jcdecaux.historiquevelov/all.json"
           f"?compact=false&maxfeatures={PAGE_SIZE}&start={start}")
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
            time.sleep(2)

def flush_buffer(buf, flushed_files):
    written = 0
    for date_str, slots in buf.items():
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
    if written:
        print(f"  → flush : {written} nouveaux jours ({len(flushed_files)} total)", flush=True)
    return defaultdict(lambda: defaultdict(dict))

def save_state(next_start, finished=False):
    state = {
        "next_start": next_start,
        "finished":   finished,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
    print(f"  → état sauvegardé : next_start={next_start:,} finished={finished}", flush=True)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
            if s.get("finished"):
                print("Historique déjà complet (done).", flush=True)
                sys.exit(0)
            start = s.get("next_start", 1)
            print(f"Reprise depuis l'état : next_start={start:,}", flush=True)
            return start
        except Exception:
            pass
    print("Pas d'état précédent — démarrage depuis le début.", flush=True)
    return 1

# ── Main ───────────────────────────────────────────────────────────────────────
start_offset = load_state()

buffer = defaultdict(lambda: defaultdict(dict))
flushed_files = set()
total_rows = 0
finished = False
t0 = time.time()
last_flush = t0
next_start = start_offset
last_committed_start = start_offset

FLUSH_INTERVAL = 300  # flush RAM toutes les 5 min

print(f"Démarrage à start={start_offset:,} | {WORKERS} workers | "
      f"pages de {PAGE_SIZE} lignes", flush=True)

with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    pending = {}
    for _ in range(WORKERS):
        fut = ex.submit(fetch_page, next_start)
        pending[fut] = next_start
        next_start += PAGE_SIZE

    while pending:
        # Vérifier si on approche de la deadline
        if time.time() >= DEADLINE:
            print(f"\n⏰ Deadline atteinte — arrêt propre.", flush=True)
            for f in list(pending):
                f.cancel()
            pending.clear()
            break

        done_fut = next(as_completed(pending))
        page_start = pending.pop(done_fut)
        start, rows, has_next = done_fut.result()

        if rows:
            dates_in_page = set()
            kept = 0
            for row in rows:
                date_str, slot_str = slot(row.get("horodate", ""))
                if not date_str or date_str < CUTOFF_DATE:
                    continue
                dates_in_page.add(date_str)
                buffer[date_str][slot_str][row.get("number")] = row
                kept += 1
            total_rows += len(rows)
            elapsed = time.time() - t0
            rate = total_rows / elapsed if elapsed > 0 else 0
            dr = f"{min(dates_in_page)}…{max(dates_in_page)}" if dates_in_page else "?"
            print(f"  start={start:>12,} | {dr} | kept={kept}/{len(rows)} | "
                  f"total={total_rows:>8,} | {rate:,.0f} l/s", flush=True)
            last_committed_start = next_start


        now = time.time()
        if now - last_flush >= FLUSH_INTERVAL:
            buffer = flush_buffer(buffer, flushed_files)
            last_flush = now

        if not has_next:
            finished = True
            for f in list(pending):
                f.cancel()
            pending.clear()
            break

        if rows:  # soumettre la page suivante seulement si pas d'erreur
            fut = ex.submit(fetch_page, next_start)
            pending[fut] = next_start
            next_start += PAGE_SIZE

# Flush final + sauvegarde état
buffer = flush_buffer(buffer, flushed_files)
save_state(next_start if not finished else next_start, finished=finished)

elapsed = time.time() - t0
print(f"\n✓ {total_rows:,} lignes en {elapsed:.0f}s "
      f"({total_rows/max(1,elapsed):.0f} l/s) → {len(flushed_files)} jours", flush=True)

if finished:
    print("✅ TERMINÉ — tout l'historique importé.", flush=True)
else:
    print("⏸ PARTIEL — relancez pour continuer.", flush=True)
