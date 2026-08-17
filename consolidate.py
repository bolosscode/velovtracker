#!/usr/bin/env python3
"""
consolidate.py — Fetche le jour J-1 depuis l'API Grand Lyon timeseries
et écrit data/history/YYYY-MM-DD.json au format delta compact.

Fix cohérence historique :
  e = electricalInternalBatteryBikes (pas electricalBikes total)
  m = mechanicalBikes + electricalRemovableBatteryBikes
"""
import os, sys, json, csv, io, ast, argparse
from datetime import datetime, timezone, date, timedelta
from collections import defaultdict
import requests

PAGE_SIZE = 500000

BASE_URL = (
    "https://data.grandlyon.com/fr/datapusher/ws/timeseries"
    "/jcd_jcdecaux.historiquevelov/all.csv"
    "?maxfeatures={size}&start={start}"
    "&horodate__gte={gte}&horodate__lt={lt}"
)

parser = argparse.ArgumentParser()
parser.add_argument("--user",     default="")
parser.add_argument("--password", default="")
parser.add_argument("--out",      default="data/history")
parser.add_argument("--date",     default="", help="Date à consolider (défaut: hier)")
args = parser.parse_args()

auth = (args.user, args.password) if args.user else None
os.makedirs(args.out, exist_ok=True)

target   = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
gte, lt  = target.isoformat(), (target + timedelta(days=1)).isoformat()
out_path = os.path.join(args.out, f"{target}.json")

print(f"Consolidation {gte} → {lt}", flush=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_stands(raw):
    if not raw or raw.strip() in ('', '""', "''"):
        return {}
    try:
        return ast.literal_eval(raw.strip().strip('"'))
    except Exception:
        pass
    try:
        return json.loads(raw.strip().strip('"').replace("'", '"'))
    except Exception:
        return {}

def safe_int(v):
    try: return int(v or 0)
    except: return 0

def fetch_meteo_day(day):
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude=45.75&longitude=4.83"
        f"&start_date={day}&end_date={day}"
        f"&hourly=precipitation,weathercode,temperature_2m"
        f"&timezone=Europe%2FParis"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        h = r.json()["hourly"]
        meteo = {}
        for i, t in enumerate(h["time"]):
            meteo[t[:13]] = {
                "precipitation": h["precipitation"][i] or 0,
                "rain":          (h["precipitation"][i] or 0) > 0.1,
                "weathercode":   int(h["weathercode"][i] or 0),
                "temp":          round(h["temperature_2m"][i] or 0, 1),
            }
        print(f"Météo : {len(meteo)} heures chargées", flush=True)
        return meteo
    except Exception as e:
        print(f"AVERTISSEMENT météo : {e}", flush=True)
        return {}

def slot(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        m = (dt.minute // 10) * 10
        s = dt.replace(minute=m, second=0, microsecond=0)
        return s.strftime('%Y-%m-%d'), s.isoformat(timespec='seconds')
    except Exception:
        return None, None

def normalize_row(row):
    try:
        tot = parse_stands(row.get('total_stands', '') or row.get('main_stands', ''))
        tav = tot.get('availabilities', {}) if isinstance(tot, dict) else {}
        bikes = safe_int(tav.get('bikes'))
        cap   = safe_int(tot.get('capacity') if isinstance(tot, dict) else 0)
        stands = (cap - bikes) if cap else safe_int(tav.get('stands'))

        # ── Cohérence avec l'historique existant ──────────────────────────
        # e = interne seulement (pas le total electricalBikes)
        # m = méca + amovible (même logique que collect.py)
        elec = safe_int(tav.get('electricalInternalBatteryBikes', 0))
        meca = safe_int(tav.get('mechanicalBikes', 0)) + \
               safe_int(tav.get('electricalRemovableBatteryBikes', 0))

        num = row.get('number')
        if not num:
            return None

        return {
            'n':  safe_int(num),
            'b':  bikes,
            's':  stands,
            'c':  cap,
            'e':  elec,
            'm':  meca,
            'st': row.get('status', 'OPEN'),
        }
    except Exception:
        return None

# ── Fetch météo ───────────────────────────────────────────────────────────────
meteo_by_hour = fetch_meteo_day(target.isoformat())

# ── Fetch timeseries ──────────────────────────────────────────────────────────
all_rows   = []
page_start = 1

while True:
    url = BASE_URL.format(size=PAGE_SIZE, start=page_start, gte=gte, lt=lt)
    try:
        r = requests.get(url, auth=auth, timeout=60)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text.lstrip('\ufeff')), delimiter=';')
        rows   = list(reader)
        all_rows.extend(rows)
        print(f"  page {page_start} | {len(rows)} lignes | total={len(all_rows):,}", flush=True)
        if len(rows) < PAGE_SIZE:
            break
        page_start += PAGE_SIZE
    except Exception as e:
        print(f"  ERREUR start={page_start}: {e}", file=sys.stderr)
        break

print(f"{len(all_rows)} lignes récupérées", flush=True)

# ── Grouper par slot 10 min ───────────────────────────────────────────────────
buffer = defaultdict(dict)
for row in all_rows:
    _, slot_str = slot(row.get('horodate', ''))
    if slot_str:
        buffer[slot_str][row.get('number')] = row

# ── Snapshots ─────────────────────────────────────────────────────────────────
snapshots = []
for ts in sorted(buffer.keys()):
    stations = [normalize_row(r) for r in buffer[ts].values()]
    stations = [s for s in stations if s]
    if stations:
        snap = {'t': ts, 's': stations}
        hour_key = ts[:13]
        if hour_key in meteo_by_hour:
            snap['meteo'] = meteo_by_hour[hour_key]
        snapshots.append(snap)

print(f"{len(snapshots)} snapshots", flush=True)

if not snapshots:
    print("Aucun snapshot — arrêt.", flush=True)
    sys.exit(0)

# ── Format delta ──────────────────────────────────────────────────────────────
FIELDS = ['b', 's', 'c', 'e', 'm', 'st']

base   = snapshots[0]
prev   = {st['n']: {**st} for st in base['s']}
deltas = []

for snap in snapshots[1:]:
    curr    = {st['n']: st for st in snap['s']}
    changed = []

    for n, st in curr.items():
        p = prev.get(n)
        if p is None:
            changed.append(st)
        else:
            diff = {'n': n}
            for f in FIELDS:
                if st.get(f) != p.get(f):
                    diff[f] = st.get(f)
            if len(diff) > 1:
                changed.append(diff)

    for n in prev:
        if n not in curr:
            changed.append({'n': n, 'st': 'CLOSED'})

    deltas.append({'t': snap['t'], 'd': changed})
    prev = curr

result = {'base': base, 'deltas': deltas}

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, separators=(',', ':'))

size_kb = os.path.getsize(out_path) // 1024
print(f"✓ {out_path} — {size_kb} KB | {len(snapshots)} snapshots | {len(deltas)} deltas", flush=True)
