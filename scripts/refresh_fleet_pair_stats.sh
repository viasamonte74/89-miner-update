source /venv/sn89/bin/activate && cd /workspace/InfiniteQuant-Subnet && PYTHONPATH=. python3 <<'PY'
import json, urllib.request, time, os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, Counter
import bittensor as bt
from sn89_signals import hf as hf_mod, hf_grade

ours={bt.Wallet(name='rabi', hotkey=n).hotkey.ss58_address:n for n in ['rabi-h1','rabi-h2','rabi-h3','rabi-h4']}
ua={'User-Agent':'sn89-research/1'}
cp=json.load(urllib.request.urlopen(urllib.request.Request('https://partner.infinitequant.app/sn89/checkpoint.json', headers=ua), timeout=120))

def pt(s):
    p=s.get('plaintext')
    return json.loads(p) if isinstance(p,str) else (p or {})

# --- LF analysis ---
lf=[]
for s in cp['signals']:
    hk=s.get('hotkey')
    if hk not in ours: continue
    st=s.get('status')
    if st not in ('won','lost','washed'): continue
    p=pt(s)
    t0=float(s.get('t0_unix') or 0)
    hour=int((t0%86400)//3600)
    lf.append(dict(
        miner=ours[hk], pair=(p.get('trade_pair') or '?').upper(),
        dir=(p.get('direction') or '?').upper(), status=st, t0=t0, hour=hour,
        copy=bool(s.get('is_copy'))))

print(f'LF graded rows: {len(lf)}')

def summarize(rows, keyfn, min_n=2):
    bucket=defaultdict(Counter)
    for r in rows:
        bucket[keyfn(r)][r['status']]+=1
    out=[]
    for k,c in bucket.items():
        w,l,wa=c['won'],c['lost'],c['washed']
        dec=w+l
        hr=w/dec if dec else None
        out.append((k, w+l+wa, w, l, wa, hr, dec))
    out.sort(key=lambda x: (-(x[5] if x[5] is not None and x[6]>=min_n else -1), -x[2], -x[1]))
    return out

print('\n=== LF by pair (min 2 decisive) ===')
print(f"{'pair':10} {'n':>3} {'W':>3} {'L':>3} {'Wa':>3} {'HR':>5}")
for k,n,w,l,wa,hr,dec in summarize(lf, lambda r:r['pair'], 2):
    if dec<2 and n<3: continue
    print(f'{k:10} {n:3} {w:3} {l:3} {wa:3} {hr and f"{hr:.2f}" or "-":>5}')

print('\n=== LF by pair+dir (min 2 decisive) ===')
for k,n,w,l,wa,hr,dec in summarize(lf, lambda r:(r['pair'],r['dir']), 2):
    if dec<2: continue
    print(f'{k[0]:10} {k[1]:5} n={n} W{w} L{l} Wa{wa} HR={hr and round(hr,2)}')

print('\n=== LF by UTC hour ===')
for k,n,w,l,wa,hr,dec in sorted(summarize(lf, lambda r:r['hour'], 1), key=lambda x:x[0]):
    print(f'  hour={k:02d} n={n:2} W{w} L{l} Wa{wa} HR={hr and round(hr,2)}')

print('\n=== LF by dir ===')
for k,n,w,l,wa,hr,dec in summarize(lf, lambda r:r['dir'], 1):
    print(f'  {k}: n={n} W{w} L{l} Wa{wa} HR={hr and round(hr,2)}')

# --- HF from receipts + grade ---
tick_dir='/workspace/sn89_data/hf_grade_ticks'
os.makedirs(tick_dir, exist_ok=True)
base=hf_mod.HF_PUBLIC_BASE
hf=[]
for rp in Path('/root/.sn89').glob('hf_receipts_*.jsonl'):
    for line in rp.read_text().splitlines():
        e=json.loads(line); sub=e.get('submit') or {}; rcpt=e.get('receipt') or {}; payload=sub.get('payload') or {}
        hk=sub.get('hk') or rcpt.get('hk')
        if hk not in ours: continue
        grid=rcpt.get('grid_t0_ms')
        pair=(payload.get('trade_pair') or '').upper()
        direction=(payload.get('direction') or '').upper()
        if not grid or not pair: continue
        band=(hf_mod.hf_bands_as_of(grid/1000) or {}).get(pair)
        if not band: continue
        tp,sl,hz=float(band[0]),float(band[1]),int(payload.get('horizon_s') or band[2])
        ticks,miss=hf_grade._ticks_for(base, tick_dir, pair, int(grid), int(grid)+hz*1000)
        entry=hf_mod.price_at(ticks, int(grid))
        if entry is None: continue
        gr=hf_mod.grade(pair, direction, float(entry), tp, sl, int(grid), hz, ticks)
        st=gr['status'] if isinstance(gr,dict) else str(gr)
        if st not in ('won','lost','wash'): continue
        hour=int(((grid/1000)%86400)//3600)
        hf.append(dict(miner=ours[hk], pair=pair, dir=direction, status='washed' if st=='wash' else st,
                       t0=grid/1000, hour=hour))

print(f'\nHF graded rows: {len(hf)}')
print('\n=== HF by pair (min 2 decisive) ===')
print(f"{'pair':10} {'n':>3} {'W':>3} {'L':>3} {'Wa':>3} {'HR':>5}")
for k,n,w,l,wa,hr,dec in summarize(hf, lambda r:r['pair'], 2):
    if dec<2 and n<3: continue
    print(f'{k:10} {n:3} {w:3} {l:3} {wa:3} {hr and f"{hr:.2f}" or "-":>5}')

print('\n=== HF by pair+dir (min 2 decisive) ===')
for k,n,w,l,wa,hr,dec in summarize(hf, lambda r:(r['pair'],r['dir']), 2):
    if dec<2: continue
    print(f'{k[0]:10} {k[1]:5} n={n} W{w} L{l} Wa{wa} HR={hr and round(hr,2)}')

print('\n=== HF by UTC hour ===')
for k,n,w,l,wa,hr,dec in sorted(summarize(hf, lambda r:r['hour'], 1), key=lambda x:x[0]):
    if n<2: continue
    print(f'  hour={k:02d} n={n:2} W{w} L{l} Wa{wa} HR={hr and round(hr,2)}')

print('\n=== HF by dir ===')
for k,n,w,l,wa,hr,dec in summarize(hf, lambda r:r['dir'], 1):
    print(f'  {k}: n={n} W{w} L{l} Wa{wa} HR={hr and round(hr,2)}')

# Save JSON for code update
report=dict(lf_pair=[{'k':a[0],'n':a[1],'w':a[2],'l':a[3],'wa':a[4],'hr':a[5]} for a in summarize(lf, lambda r:r['pair'],1)],
            lf_hour=[{'k':a[0],'n':a[1],'w':a[2],'l':a[3],'wa':a[4],'hr':a[5]} for a in summarize(lf, lambda r:r['hour'],1)],
            hf_pair=[{'k':a[0],'n':a[1],'w':a[2],'l':a[3],'wa':a[4],'hr':a[5]} for a in summarize(hf, lambda r:r['pair'],1)],
            hf_hour=[{'k':a[0],'n':a[1],'w':a[2],'l':a[3],'wa':a[4],'hr':a[5]} for a in summarize(hf, lambda r:r['hour'],1)],
            hf_pair_dir=[{'k':f'{a[0][0]}:{a[0][1]}','n':a[1],'w':a[2],'l':a[3],'wa':a[4],'hr':a[5]} for a in summarize(hf, lambda r:(r['pair'],r['dir']),1)],
            lf_pair_dir=[{'k':f'{a[0][0]}:{a[0][1]}','n':a[1],'w':a[2],'l':a[3],'wa':a[4],'hr':a[5]} for a in summarize(lf, lambda r:(r['pair'],r['dir']),1)],
            generated_at=time.time())
Path('/workspace/sn89_data/fleet_pair_stats.json').write_text(json.dumps(report, indent=2))
print('\nwrote /workspace/sn89_data/fleet_pair_stats.json')
PY
