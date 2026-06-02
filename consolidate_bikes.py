#!/usr/bin/env python3
"""
consolidate_bikes.py — Consolide today.json en trajets déduits pour YYYY-MM-DD.
Lancé à 2h15 UTC via GitHub Actions.
"""
import os, sys, json, argparse
from datetime import date, timedelta
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--date', default='', help='Date à consolider (défaut: hier)')
parser.add_argument('--out', default='data/bikes/history')
args = parser.parse_args()

OUT_DIR  = Path(args.out)
TODAY_F  = Path('data/bikes/today.json')
OUT_DIR.mkdir(parents=True, exist_ok=True)

target = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
out_path = OUT_DIR / f"{target}.json"

print(f"Consolidation vélos {target}", flush=True)

if not TODAY_F.exists():
    print("ERREUR: today.json introuvable")
    sys.exit(1)

data = json.loads(TODAY_F.read_text())

if data.get('date') != target.isoformat():
    print(f"AVERTISSEMENT: today.json est pour {data.get('date')}, pas {target}")

# ── Reconstruire l'état complet à chaque timestamp ──────────────────────────
base_bikes = {b['id']: dict(b) for b in data['base']['bikes']}
events = data.get('events', [])

# Grouper les events par timestamp
from collections import defaultdict
events_by_ts = defaultdict(list)
for ev in events:
    events_by_ts[ev['t']].append(ev)

# Reconstruire snapshots horodatés
state = dict(base_bikes)
snapshots = [(data['base']['t'], dict(state))]

for ts in sorted(events_by_ts.keys()):
    for ev in events_by_ts[ts]:
        bid = ev['id']
        if bid not in state:
            state[bid] = {'id': bid}
        state[bid].update({k: v for k, v in ev.items() if k != 't'})
    snapshots.append((ts, {bid: dict(b) for bid, b in state.items()}))

# ── Déduire les trajets ─────────────────────────────────────────────────────
trips = []
# Pour chaque vélo, trouver les séquences : st→None (départ) puis None→st (arrivée)
bike_dep = {}  # bid → {'t': ..., 'st': ..., 'batt': ...}

for i, (ts, snap) in enumerate(snapshots):
    for bid, bike in snap.items():
        status = bike.get('status', 'A')
        prev_status = snapshots[i-1][1].get(bid, {}).get('status', 'A') if i > 0 else 'A'

        # Départ : vélo passe de A/S à R ou disparaît
        if prev_status == 'A' and status in ('R', 'X'):
            prev_bike = snapshots[i-1][1].get(bid, {}) if i > 0 else {}
            bike_dep[bid] = {
                't': ts,
                'st': prev_bike.get('st'),
                'batt_dep': prev_bike.get('batt'),
                'mv_dep': prev_bike.get('mv'),
                'rating': prev_bike.get('rating'),
                'type': bike.get('type', prev_bike.get('type', 'M')),
            }

        # Arrivée : vélo passe de R/X à A dans une nouvelle station
        elif prev_status in ('R', 'X') and status == 'A':
            if bid in bike_dep:
                dep = bike_dep.pop(bid)
                arr_st = bike.get('st')
                dep_st = dep['st']
                if dep_st and arr_st and dep_st != arr_st:
                    trips.append({
                        'id':      bid,
                        'type':    dep['type'],
                        'dep_st':  dep_st,
                        'arr_st':  arr_st,
                        'dep_t':   dep['t'],
                        'arr_t':   ts,
                        'batt_dep': dep.get('batt_dep'),
                        'batt_arr': bike.get('batt'),
                        'mv_dep':  dep.get('mv_dep'),
                        'rating':  dep.get('rating'),
                    })

print(f"  {len(trips)} trajets déduits", flush=True)

# ── Stats par vélo ──────────────────────────────────────────────────────────
bike_stats = defaultdict(lambda: {'trips': 0, 'type': 'M', 'rating': None})
for trip in trips:
    bid = trip['id']
    bike_stats[bid]['trips'] += 1
    bike_stats[bid]['type']  = trip['type']
    if trip.get('rating'):
        bike_stats[bid]['rating'] = trip['rating']

# ── Écrire ──────────────────────────────────────────────────────────────────
result = {
    'date':   target.isoformat(),
    'trips':  trips,
    'bikes':  [{'id': bid, **stats} for bid, stats in bike_stats.items()],
    'n_trips': len(trips),
    'n_bikes': len(bike_stats),
}

out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
size = out_path.stat().st_size // 1024
print(f"✓ {out_path} — {size}KB | {len(trips)} trajets | {len(bike_stats)} vélos actifs", flush=True)
