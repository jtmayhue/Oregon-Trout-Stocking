#!/usr/bin/env python3
"""
ODFW stocking ingest — run this from your own machine or a small server.
It could not be run from the session that wrote it: that container's egress
proxy blocks myodfw.com, web.archive.org, kaggle.com and usgs.gov.
So treat this as UNVERIFIED against the live site. Expect to adjust the
selectors on first run.

    pip install requests beautifulsoup4 lxml pdfplumber
    python odfw_ingest.py --out ./data
"""
import argparse, csv, json, re, sys, time
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://myodfw.com"
# VERIFIED against the live site 2026-09-03 through a real browser.
# Use the INTERACTIVE page. The -print page returns 403 for ANY query parameter,
# even from a genuine browser -- it is not bot detection, the URL is just blocked.
SCHEDULE = f"{BASE}/fishing/species/trout/stocking-schedule"
# Real Drupal field names. "start_date"/"end_date" are NOT the parameters.
P_START = "field_planned_stocking_date_value"
P_END   = "field_planned_stocking_date_end_value"
PAGE_SIZE = 50   # results are paginated; a full year is ~20 pages
# ODFW returns 403 to parameterized requests that do not look like a browser.
# Verified: bare URL 200, same URL + ?start_date=... 403 from their server (not a proxy).
UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://myodfw.com/fishing/species/trout/stocking-schedule",
}

ABBREV = {
    "LK": "Lake", "PD": "Pond", "PDS": "Ponds", "RES": "Reservoir",
    "CR": "Creek", "R": "River", "FK": "Fork", "RVR": "River",
    "PK": "Park", "WLDLIFE": "Wildlife", "UPR": "Upper", "CST": "Coast",
    "N": "North", "S": "South", "E": "East", "W": "West",
}
# Settled by the 2026 full-year pull: ODFW writes Lower as LWR (EMPIRE LK, LWR)
# and Upper as UPR, and uses bare B for Big (LAVA LK, B / CULTUS LK, B).
# So a bare L is Little, not Lower.
ABBREV.update({"LWR": "Lower", "B": "Big", "L": "Little"})
AMBIGUOUS: set[str] = set()

RENAME_RE = re.compile(r"\((?:formerly|former|fmrly|fmr)\.?\s*(.+?)\)", re.I)
RENAME_HYPHEN_RE = re.compile(r"[-\u2013]\s*(?:former|fmr)\.?\s*(.+)$", re.I)
PAREN_RE = re.compile(r"\(([^)]+)\)")
JUNK_RE = re.compile(r"\b(area operations|operations)\b", re.I)
# Parentheticals that disambiguate a place stay in the name. Everything else
# is an alternate name and becomes a searchable alias.
QUALIFIER_RE = re.compile(
    r"\b(coast range|hood r|willamette r|rogue|campus|above|below|north|south|"
    r"east|west|upper|lower|mhcc)\b", re.I)


def _titlecase(tok: str) -> str:
    """ODFW ships SHOUTING. Fix all-caps tokens, leave real mixed case alone."""
    # Allow apostrophes and periods: DEVIL'S and RES. must still title-case.
    # NOTE: never strip the apostrophe. DEVIL'S LK (Newport) and DEVILS LK (Bend)
    # are two different lakes; normalizing punctuation silently merges them.
    core = tok.replace("'", "").replace("\u2019", "").replace(".", "")
    if not (tok.isupper() and core.isalpha() and core):
        return tok
    t = tok.title()
    # str.title() capitalises after an apostrophe: DEVIL'S -> Devil'S. Undo that.
    t = re.sub(r"(['\u2019])([A-Z])", lambda m: m.group(1) + m.group(2).lower(), t)
    # Mckenzie -> McKenzie, Macleay -> MacLeay
    m = re.match(r"^(Ma?c)([a-z])(.*)$", t)
    if m and len(t) > 4:
        t = m.group(1) + m.group(2).upper() + m.group(3)
    return t


def normalize(raw: str):
    """Return (canonical_name, aliases, flags) for one ODFW waterbody string."""
    flags, aliases = [], []
    s = raw.strip()

    if JUNK_RE.search(s):
        return None, [], ["junk_row"]

    is_reach = bool(re.search(r"\b(above|below)\b", s, re.I))

    mh = RENAME_HYPHEN_RE.search(s)
    if mh:
        aliases.append(" ".join(_titlecase(t) for t in mh.group(1).split()))
        s = RENAME_HYPHEN_RE.sub("", s).strip()
        flags.append("renamed")

    m = RENAME_RE.search(s)
    if m:
        aliases.append(" ".join(_titlecase(t) for t in m.group(1).split()))
        s = RENAME_RE.sub("", s).strip()
        flags.append("renamed")

    for p in PAREN_RE.findall(s):
        if QUALIFIER_RE.search(p):
            fixed = " ".join(_titlecase(t) for t in p.split())
            if fixed != p:
                s = s.replace(f"({p})", f"({fixed})")
            continue                     # geographic qualifier: keep in name
        aliases.append(" ".join(_titlecase(t) for t in p.split()))
        s = s.replace(f"({p})", " ").strip()
        flags.append("alt_name")

    if "," in s:
        head, tail = [x.strip() for x in s.split(",", 1)]
        tail_tokens = [ABBREV.get(t.upper(), t) for t in tail.split()]
        s = f"{head} {' '.join(tail_tokens)}"
        flags.append("comma_modifier")

    tokens = []
    for tok in s.split():
        bare = tok.strip(".").upper()
        if bare in AMBIGUOUS:
            flags.append("ambiguous_modifier")
            tokens.append(tok)
        elif bare in ABBREV:
            tokens.append(ABBREV[bare])
        else:
            tokens.append(_titlecase(tok))

    name = re.sub(r"\s{2,}", " ", " ".join(tokens)).strip()

    if is_reach:
        flags.append("river_reach")
    if PAREN_RE.search(raw) and "alt_name" not in flags and "renamed" not in flags:
        flags.append("qualifier_kept")

    return name, aliases, flags


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


ZONE_PARAM = "field_zone_value"
# The zone dropdown on the live form (checked 2026-11-17): 1 Central,
# 2 Northeast, 3 Northwest, 4 Southeast, 5 Southwest, 6 Willamette.
ZONES = ["1", "2", "3", "4", "5", "6"]
EXPECTED_HEADERS = {"week of", "waterbody", "total"}
NAME_PARAM = "field_waterbody_name_value_1"   # case-insensitive "contains"
# Waters ODFW lists with no zone at all. A zone query can never return them,
# so they are also fetched by name. Any new ones seen in a run are added.
NO_ZONE_SEED = ["BOLAN LK", "SMITH LK", "HYATT RES, LITTLE"]
DELAY = 0.4


class MarkupChanged(Exception):
    pass


def _row_key(row: dict) -> tuple:
    return tuple(sorted(row.items()))


def _query(sess, params: dict) -> tuple[list[dict], int]:
    """Every page of one query. Returns (rows, slots).

    `slots` is the number of rows ODFW's pager handed out, counting repeats.
    That is ODFW's own count of how many rows the query matched, which is what
    the completeness check compares against.

    No table at all is NORMAL here: an empty week, an empty zone, or the page
    after the last one all come back without a table.
    """
    rows, slots = [], 0
    for page in range(0, 20):
        r = sess.get(SCHEDULE, params={**params, "page": page}, timeout=45)
        if r.status_code == 403:
            sys.exit(f"403 for {params} page {page}. If earlier requests worked "
                     f"you are being rate limited -- raise DELAY.")
        r.raise_for_status()
        table = BeautifulSoup(r.text, "lxml").find("table")
        time.sleep(DELAY)
        if not table:
            break
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not EXPECTED_HEADERS.issubset(headers):
            raise MarkupChanged(f"unexpected table headers {headers}")
        page_rows = []
        for tr in table.select("tbody tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if cells:
                page_rows.append(dict(zip(headers, cells)))
        if not page_rows:
            break
        slots += len(page_rows)
        rows.extend(page_rows)
        # Keep going until an empty page. Short pages are not a reliable end.
    return rows, slots


def _no_zone(row: dict) -> bool:
    z = next((v for k, v in row.items() if k.startswith("zone")), "")
    return not z.strip() or z.strip().startswith("/")


def _dates(row: dict) -> tuple[date | None, date | None]:
    """First and last day of the row's 'Week of' range."""
    v = next((v for k, v in row.items() if "week" in k), "")
    found = re.findall(r"([A-Za-z]{3})\w*\.?\s+(\d{1,2}),\s*(\d{4})", v or "")
    days = [date(int(y), _MONTHS[m], int(d)) for m, d, y in found if m in _MONTHS]
    return (days[0], days[-1]) if days else (None, None)


def _mondays(start: str, end: str):
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    d = lo - timedelta(days=lo.weekday())
    while d <= hi:
        yield d
        d += timedelta(days=7)


def fetch_window(sess, first: date, last: date) -> tuple[list[dict], int]:
    """Every row ODFW returns for one date window. Returns (rows, odfw_count).

    Two ODFW behaviours this works around, both verified 2026-11-17 in a real
    browser:

    1. When a query matches more than 50 rows, the pager repeats rows from
       page 1 on page 2 instead of showing the rest. The week of Apr 12 2027
       matches 64 rows and the all-Oregon query only ever shows 50; the six
       zone queries (each under 50, one page) show all 64. Rows with no zone
       (Bolan Lake, Little Hyatt Reservoir) only appear in the all-Oregon
       query, so both are fetched and merged.
    2. A row is returned only when the window covers its WHOLE span, so a
       stocking ODFW lists across two weeks never matches a one-week window.
       fetch_schedule runs a second pass of two-week windows for those.
    """
    win = {P_START: first.isoformat(), P_END: last.isoformat()}
    rows, odfw_count = _query(sess, win)
    found = {_row_key(r): r for r in rows}
    if odfw_count > PAGE_SIZE:
        for z in ZONES:
            zrows, _ = _query(sess, {**win, ZONE_PARAM: z})
            for r in zrows:
                found.setdefault(_row_key(r), r)
    return list(found.values()), odfw_count


def fetch_schedule(start: str, end: str, report_path: Path | None = None) -> list[dict]:
    """Every row from start to end.

    Replaces the old single paged pull over the whole range, which lost about
    1 row in 10 to ODFW's pager -- a different 1 in 10 on every run.

    Pass 1: one-week windows. Pass 2: no-zone waters by name. Pass 3: two-week
    windows, for stockings ODFW spreads across two weeks. Every window's row
    count from ODFW is compared with the rows held for that window, so any
    shortfall is reported rather than silently published.
    """
    sess = requests.Session()
    sess.headers.update(UA)
    bare = sess.get(SCHEDULE, timeout=45)
    if bare.status_code == 403:
        sys.exit("ODFW returned 403 on the bare page -- IP or headers blocked.")

    all_rows, spans = [], {}            # spans: row key -> (first, last day)
    windows = []                        # (first, last, odfw_count)

    def keep(r):
        k = _row_key(r)
        if k not in spans:              # a row can turn up in several windows
            spans[k] = _dates(r)
            all_rows.append(r)

    try:
        for monday in _mondays(start, end):
            last = monday + timedelta(days=6)
            rows, count = fetch_window(sess, monday, last)
            windows.append((monday, last, count))
            for r in rows:
                keep(r)

        names = set(NO_ZONE_SEED)
        names.update(next((v for k, v in r.items() if "water" in k), "")
                     for r in all_rows if _no_zone(r))
        names.discard("")
        for name in sorted(names):
            rows, _ = _query(sess, {P_START: start, P_END: end, NAME_PARAM: name})
            for r in rows:
                keep(r)

        for monday in _mondays(start, end):
            last = monday + timedelta(days=13)
            rows, count = fetch_window(sess, monday, last)
            windows.append((monday, last, count))
            for r in rows:
                keep(r)
    except MarkupChanged as e:
        sys.exit(f"ODFW changed the schedule table: {e}. Not publishing.")

    def held(first, last):
        return sum(1 for a, b in spans.values()
                   if a is not None and first <= a and b <= last)

    short, missing_total, odfw_total = [], 0, 0
    for first, last, count in windows:
        have = held(first, last)
        odfw_total += count
        if have < count:
            missing_total += count - have
            short.append({"window": f"{first}..{last}", "odfw_count": count,
                          "missing": count - have})
            print(f"  {first}..{last}: ODFW lists {count}, recovered {have}")
    multi_week = sum(1 for a, b in spans.values() if a and b and (b - a).days > 6)

    print(f"  {len(all_rows)} rows ({multi_week} span two weeks); "
          f"{len(short)} windows short, {missing_total} rows unrecovered")
    if report_path:
        report_path.write_text(json.dumps({
            "start": start, "end": end, "rows": len(all_rows),
            "multi_week_rows": multi_week, "windows": len(windows),
            "odfw_count_all_windows": odfw_total, "missing": missing_total,
            "short_windows": short}, indent=2))
    if not all_rows:
        sys.exit("No rows at all for the whole range. ODFW changed the page "
                 "or ignored the date filter. Not publishing.")
    # A row genuinely listed twice also counts as unrecovered, so allow a
    # little slack. Beyond 1% of all window counts, something is wrong.
    if odfw_total and missing_total / odfw_total > 0.01:
        sys.exit(f"{missing_total} rows across {len(short)} windows could not be "
                 f"recovered (over 1%). Not publishing; see the report.")

    _check_coverage(all_rows, start, end)
    return all_rows


_MONTHS = {m: i for i, m in enumerate(
    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}


def _check_coverage(rows, start, end):
    """Fail loudly when the returned range is narrower than the one requested.

    This is the Timothy Lake bug generalised. A partial pull looks exactly like
    a successful one -- same shape, no error, just fewer waters -- and the waters
    it drops are the out-of-season ones a user is most likely to be following.
    """
    dates = []
    for r in rows:
        v = next((v for k, v in r.items() if 'week' in k.lower()), '')
        m = re.search(r'([A-Za-z]{3})\w*\.?\s+(\d{1,2}),\s*(\d{4})', v or '')
        if m and m.group(1) in _MONTHS:
            dates.append(f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}")
    if not dates:
        print("  WARNING: no parseable week dates -- check the table format.")
        return
    lo, hi = min(dates), max(dates)
    print(f"  coverage: {lo} .. {hi}  (requested {start} .. {end})")

    def days(a, b):
        from datetime import date
        f = lambda s: date(*map(int, s.split('-')))
        return (f(b) - f(a)).days

    gap_start, gap_end = days(start, lo), days(hi, end)
    if gap_start > 45 or gap_end > 45:
        print("  " + "!" * 68)
        print(f"  PARTIAL YEAR. Missing ~{max(gap_start,0)}d at the start and "
              f"~{max(gap_end,0)}d at the end.")
        print("  Waters stocked only outside this window are MISSING, and the")
        print("  output will look normal. Do not publish this as a full year.")
        print("  Re-run; if it persists, ODFW is ignoring the date parameters.")
        print("  " + "!" * 68)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", default="./data")
    a = ap.parse_args()

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rows = fetch_schedule(a.start, a.end, report_path=out / "ingest_report.json")
    print(f"fetched {len(rows)} rows for {a.start}..{a.end}")

    waters, events, rejects = {}, [], []
    for row in rows:
        raw = next((v for k, v in row.items() if "water" in k), None)
        if not raw:
            continue
        name, aliases, flags = normalize(raw)
        if name is None:
            rejects.append({"raw": raw, "reason": "junk_row"})
            continue
        wid = slug(name)
        w = waters.setdefault(wid, {"id": wid, "name": name,
                                    "aliases": set(), "raw": set(), "flags": set()})
        w["aliases"].update(aliases); w["raw"].add(raw); w["flags"].update(flags)
        events.append({"waterbody_id": wid, **row})

    with open(out / "waterbodies.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "name", "aliases", "odfw_raw_strings", "flags"])
        for w in sorted(waters.values(), key=lambda x: x["id"]):
            wr.writerow([w["id"], w["name"], "|".join(sorted(w["aliases"])),
                         "|".join(sorted(w["raw"])), "|".join(sorted(w["flags"]))])

    with open(out / "stocking_events.json", "w") as f:
        json.dump(events, f, indent=2)
    with open(out / "rejects.json", "w") as f:
        json.dump(rejects, f, indent=2)

    print(f"{len(waters)} distinct waters, {len(events)} events, {len(rejects)} rejected")
    print("Review rejects.json every run. Silent drops are how this breaks.")


if __name__ == "__main__":
    main()
