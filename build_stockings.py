#!/usr/bin/env python3
"""
Build stockings.json for the app from a full-year ODFW schedule pull.

    python odfw_ingest.py --start 2026-01-01 --end 2026-12-31 --out ./data
    python build_stockings.py --events ./data/stocking_events.json \
        --waters ./waters.json --out ./stockings.json

waters.json changes rarely and is curated by hand. stockings.json is regenerated
every run. Keeping them separate is what lets the app cache the first one.

Anything whose ODFW name is not in waters.json is written to unmatched.json,
never silently dropped -- a water the app cannot key is a water a user cannot
follow, and you want to see that list grow.
"""
import argparse, json, re
from collections import defaultdict
from pathlib import Path

MONTHS = {m: i for i, m in enumerate(
    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}


def parse_week(s: str):
    """'Week of Mar. 16, 2026 - Mar. 20, 2026' -> '2026-03-16' (the Monday)."""
    s = re.sub(r'^Week of\s*', '', s.strip()).split(' - ')[0]
    m = re.match(r'([A-Za-z]{3})\w*\.?\s+(\d{1,2}),\s*(\d{4})', s)
    if not m:
        return None
    mon, day, yr = m.groups()
    if mon not in MONTHS:
        return None
    return f"{yr}-{MONTHS[mon]:02d}-{int(day):02d}"


def num(v):
    v = (v or '').replace(',', '').strip()
    return int(v) if v.isdigit() else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--events', default='./data/stocking_events.json')
    ap.add_argument('--waters', default='./waters.json')
    ap.add_argument('--out', default='./stockings.json')
    a = ap.parse_args()

    waters = json.load(open(a.waters))['waters']
    lookup = {}
    for w in waters:
        for n in w['odfw_names']:
            lookup[n.strip().upper()] = w['id']

    events = json.load(open(a.events))


    def odfw_name(e):
        """The ODFW waterbody name, NOT odfw_ingest's own slug.

        odfw_ingest writes each event as {'waterbody_id': <slug>, **table_row},
        and 'waterbody_id' is inserted FIRST -- so a naive
        `next(k for k in e if 'water' in k)` returns the slug and every event
        silently fails to match waters.json. Prefer the exact column name.
        """
        for k in ('waterbody', 'Waterbody', 'WATERBODY'):
            if k in e:
                return e[k]
        for k, v in e.items():
            kl = k.lower()
            if 'water' in kl and not kl.endswith('_id') and kl != 'waterbody_id':
                return v
        return None
    by_water, unmatched = defaultdict(list), defaultdict(int)
    skipped_no_name = skipped_no_week = 0

    for e in events:
        raw = odfw_name(e)
        if not raw:
            skipped_no_name += 1
            continue
        wid = lookup.get(raw.strip().upper())
        if not wid:
            unmatched[raw] += 1
            continue
        wk = next((parse_week(v) for k, v in e.items() if 'week' in k.lower()), None)
        if not wk:
            skipped_no_week += 1
            continue
        g = lambda key: num(next((v for k, v in e.items() if k.lower().startswith(key)), 0))
        by_water[wid].append({
            'week': wk,
            'legals': g('legal'), 'trophy': g('trophy'),
            'brood': g('brood'), 'fingerling': g('fingerling'),
            'total': g('total'),
        })

    # Dedupe and sort; ODFW repeats rows across pages.
    out = {}
    for wid, evs in by_water.items():
        seen, uniq = set(), []
        for ev in sorted(evs, key=lambda x: x['week']):
            k = (ev['week'], ev['total'])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(ev)
        out[wid] = uniq

    payload = {
        'generated': __import__('datetime').date.today().isoformat(),
        'kind': 'planned',      # THE SCHEDULE IS INTENT, NOT A RECEIPT.
        'note': 'ODFW publishes the WEEK a water is scheduled, not the day, '
                'and fish are sometimes diverted. Confirmations come from the '
                'zone reports, not this file.',
        'water_count': len(out),
        'event_count': sum(len(v) for v in out.values()),
        'stockings': out,
    }
    Path(a.out).write_text(json.dumps(payload, indent=1))
    Path('unmatched.json').write_text(json.dumps(
        dict(sorted(unmatched.items(), key=lambda x: -x[1])), indent=1))

    print(f"{payload['event_count']} events across {payload['water_count']} waters -> {a.out}")
    if skipped_no_name or skipped_no_week:
        print(f"  skipped: {skipped_no_name} without a waterbody name, "
              f"{skipped_no_week} without a parseable week")
    matched_pct = 100 * len(out) / max(len(out) + len(unmatched), 1)
    if matched_pct < 50:
        print(f"  WARNING: only {matched_pct:.0f}% of waters matched waters.json. "
              f"That usually means the event key or the name format changed.")
    if unmatched:
        print(f"{len(unmatched)} ODFW names not in waters.json -> unmatched.json")
        for n, c in list(sorted(unmatched.items(), key=lambda x: -x[1]))[:5]:
            print(f"   {c:>3}x  {n}")


if __name__ == '__main__':
    main()
