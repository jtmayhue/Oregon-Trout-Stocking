# How to run this

## The weekly one (do this first — it's the data with a clock on it)

**Easiest way:** in Finder, open the `Trout Stocking App` folder and **double-click
`run_weekly.command`.**

If macOS says it can't be opened because it's from an unidentified developer,
right-click it → **Open** → **Open** again. You only do that once.

If double-clicking does nothing, open **Terminal** and run this once:

```bash
chmod +x ~/"Trout Stocking App/run_weekly.command"
```

Then double-click it again.

**What happens:** the first run takes about a minute — it builds a small self-contained
Python environment inside the folder (`.venv`) so nothing touches the rest of your Mac.
Every run after that takes a few seconds.

**What you get:**
- `archive/raw/2026-09-03/` — the raw HTML of all 8 zone reports. **This is the point.**
  ODFW overwrites these weekly and keeps no archive, so this folder is the only copy
  that will exist.
- `archive/confirmed_stockings.csv` — extracted confirmations, appended each run.
- `archive/hashes.json` — so you can see which zones actually changed week to week.

**Do it every Monday.** To make it automatic, open Terminal and run `crontab -e`,
press `i`, paste this line, then press Escape and type `:wq` and Return:

```
0 9 * * 1 cd ~/"Trout Stocking App" && ./.venv/bin/python zone_snapshot.py --root ./archive
```

That runs it at 9am every Monday. Your Mac has to be awake. Honestly, double-clicking
it yourself on Mondays is fine to start — don't let the cron setup become the reason
you don't start.

## The full-year schedule pull (do this once a year, plus whenever you want fresh data)

In Terminal:

```bash
cd ~/"Trout Stocking App"
./.venv/bin/python odfw_ingest.py --start 2026-01-01 --end 2026-12-31 --out ./data
```

Expect ~20 pages, 959 rows, 231 waters. It prints each page as it goes.

**Always read `data/rejects.json` afterward.** That's where rows it refused to
guess at end up. If that file is empty on a real run, be suspicious — ODFW's data
is never that clean.

## If something breaks

The scripts fail loudly on purpose rather than returning partial data quietly.

- **"403 on the bare page"** — your IP or headers are blocked. Rare from home.
- **"403 on page N"** — if page 0 worked, you're being rate limited; raise the
  `time.sleep` value in `fetch_schedule`. If page 0 failed, ODFW changed the form
  field names — open the schedule page in a browser, filter by date, and look at
  the URL to see the new ones.
- **"No table on page N"** — they changed the page markup; the CSS selector needs
  updating.
- **A zone 404s** — normal. `columbia-river-zone` is listed on ODFW's index but
  doesn't exist. The script logs it and continues.
