# Oregon Trout Stocking

Data pipeline for an Oregon trout stocking app. Every fact traces to a
government source, with a date.

## What's here

| File | What it is |
|---|---|
| `waters.json` | 176 stocked waters — coordinates, amenities, species, acreage. Curated; changes rarely. |
| `quarantine.json` | 51 waters held back, each with a reason. Not deleted — most need one verification pass. |
| `stockings.json` | Generated weekly. **Planned** stockings from ODFW's schedule. |
| `archive/raw/<date>/` | Raw HTML of the zone reports. **The irreplaceable part.** |
| `archive/confirmed_stockings.csv` | Extracted **confirmed** stockings, with the source sentence. |

## The distinction that matters

- **The schedule is intent.** ODFW publishes the *week* a water is scheduled, not
  the day, and fish get diverted. `stockings.json` is stamped `"kind": "planned"`.
- **The zone reports are the receipt.** Past tense, with counts. These are the only
  per-waterbody confirmation ODFW publishes.

Never show a confirmation without `section_last_updated`. Sections go stale
independently of the page — one read 7/27 while the page header said 9/3.

## Rules

1. A water ships only when its identity is certain. Otherwise it goes to quarantine
   with a reason.
2. Never substring-match names. Prineville Fishing Pond is not Prineville Reservoir;
   Blue River is not Blue Lake; Devil's Lake and Devils Lake are two different lakes.
3. Never drop a row silently. Rejects go to a file you read every run.

## Running it

Weekly capture happens automatically via `.github/workflows/weekly.yml`.
To run by hand: Actions tab → Weekly ODFW capture → Run workflow.

Locally:

```bash
python -m venv .venv && ./.venv/bin/pip install requests beautifulsoup4 lxml
./.venv/bin/python zone_snapshot.py --root ./archive
./.venv/bin/python odfw_ingest.py --start 2026-01-01 --end 2026-12-31 --out ./data
./.venv/bin/python build_stockings.py
```

## Sources

ODFW weekly trout stocking schedule · ODFW zone fishing reports ·
ODFW trout stocking maps (coordinates and amenities) ·
ODFW Fish Propagation Annual Reports 2003–2025 (validation)
