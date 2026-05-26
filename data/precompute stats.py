#!/usr/bin/env python3
"""
precompute_stats.py — Lit tout l'historique et génère des fichiers
data/stats/dow{0-6}_h{0-23}.json avec les probas précalculées par station.
À lancer après consolidate.py (ou en tâche hebdomadaire).
"""
import os, sys, json, gzip
from datetime import date, timedelta
from collections import defaultdict

HISTORY_DIR = "data/history"
STATS_DIR   = "data/stats"
DAYS_BACK   = 182  # 26 semaines

os.makedirs(STATS_DIR, exist_ok=True)

# ── Charger tous les fichiers historiques ─────────────────────────────────────
def load_day(path):
    try:
        if path.endswith('.json.gz'):
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                return json.load(f)
        else:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"  skip {path}: {e}", flush=True)
        return None

def expand_deltas(data):
    base = data['base']
    state = {st['n']: dict(st) for st in base.get('s', [])}
    snaps = [{'t': base['t'], 's': list(state.values()), 'meteo': base.get('meteo')}]
    last_meteo = base.get('meteo')
    for delta in data.get('deltas', []):
        for d in delta.get('d', []):
            n = d['n']
            if n not in state:
                state[n] = {'n': n}
            state[n].update(d)
        meteo = delta.get('meteo', last_meteo)
        if delta.get('meteo'):
            last_meteo = delta['meteo']
        snaps.append({'t': delta['t'], 's': [dict(s) for s in state.values()], 'meteo': meteo})
    return snaps

# ── Accumulateurs par (dow, hour, station_number) ────────────────────────────
# Structure : acc[dow][h][num] = {e_sum, m_sum, s_sum, w_sum, e_hit, s_hit, n}
acc = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {
    'e_sum':0,'m_sum':0,'s_sum':0,'w_sum':0,'e_hit':0,'s_hit':0,'n':0,
    'e_name':'', 'cap':0
})))

today = date.today()
loaded = 0
for i in range(DAYS_BACK):
    d = today - timedelta(days=i+1)
    ds = d.isoformat()
    dow = d.weekday()  # 0=lundi … 6=dimanche (Python) → on garde Python dow
    
    path = None
    for ext in ['.json.gz', '.json']:
        p = os.path.join(HISTORY_DIR, ds + ext)
        if os.path.exists(p):
            path = p
            break
    if not path:
        continue
    
    raw = load_day(path)
    if not raw:
        continue
    
    snaps = expand_deltas(raw) if 'base' in raw else raw
    loaded += 1
    
    for snap in snaps:
        try:
            t = snap['t']
            h = int(t[11:13])  # heure locale dans le timestamp
            meteo = snap.get('meteo', {})
            rain = meteo.get('rain', False) if meteo else False
            w = 1.0  # poids uniforme (météo ignorée ici, pré-calculé sans pondération)
            
            for st in snap.get('s', []):
                num = st.get('n') or st.get('number')
                if not num: continue
                e = st.get('e', 0) or 0
                m = st.get('m', 0) or 0
                s = st.get('s', 0) or 0  # places libres
                cap = st.get('c', 0) or 0
                if not cap: continue
                
                a = acc[dow][h][num]
                a['e_sum'] += e / cap * 100 * w
                a['m_sum'] += m / cap * 100 * w
                a['s_sum'] += s / cap * 100 * w
                a['w_sum'] += w
                if e >= 1: a['e_hit'] += w
                if s >= 1: a['s_hit'] += w
                a['n'] += 1
                a['cap'] = cap
        except Exception:
            continue

print(f"Chargé {loaded} jours", flush=True)

# ── Écrire un fichier par (dow, hour) ─────────────────────────────────────────
total_files = 0
for dow in range(7):
    for h in range(24):
        stations = acc[dow][h]
        if not stations:
            continue
        
        out = {}
        for num, a in stations.items():
            if a['w_sum'] < 1:
                continue
            out[str(num)] = {
                'ep': round(a['e_sum'] / a['w_sum']),          # % élec moyen
                'sp': round(a['s_sum'] / a['w_sum']),          # % places moyen
                'eProb': round(a['e_hit'] / a['w_sum'] * 100), # proba ≥1 élec
                'sProb': round(a['s_hit'] / a['w_sum'] * 100), # proba ≥1 place
                'eN': round(a['e_sum'] / a['w_sum'] * a['cap'] / 100, 1), # nb moyen élec
                'sN': round(a['s_sum'] / a['w_sum'] * a['cap'] / 100, 1), # nb moyen places
                'n': a['n'],
            }
        
        if not out:
            continue
        
        fname = os.path.join(STATS_DIR, f"dow{dow}_h{h:02d}.json")
        with open(fname, 'w') as f:
            json.dump(out, f, separators=(',', ':'))
        total_files += 1

print(f"✓ {total_files} fichiers stats générés dans {STATS_DIR}/", flush=True)
