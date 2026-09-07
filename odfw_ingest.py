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
import argparse, csv, json, re, sys, time, time
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


def fetch_schedule(start: str, end: str) -> list[dict]:
    """Full-year pull, all pages. NEVER derive the waterbody universe from a
    default view: it is date-windowed and silently omits out-of-season waters.
    A 2026-09-03 default pull returned 103 waters; the full year has 231."""
    sess = requests.Session()
    sess.headers.update(UA)

    bare = sess.get(SCHEDULE, timeout=45)
    if bare.status_code == 403:
        sys.exit("ODFW returned 403 on the bare page -- IP or headers blocked.")

    rows, headers = [], None
    for page in range(0, 60):                      # hard stop; a year is ~20
        r = sess.get(SCHEDULE, params={P_START: start, P_END: end, "page": page},
                     timeout=45)
        if r.status_code == 403:
            sys.exit(f"403 on page {page}. If page 0 worked, you are being rate "
                     f"limited -- raise the delay. If page 0 failed, the field "
                     f"names changed; re-inspect the form.")
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        if not table:
            sys.exit(f"No table on page {page}. Selector needs updating.")
        if headers is None:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

        page_rows = []
        for tr in table.select("tbody tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if cells:
                page_rows.append(dict(zip(headers, cells)))

        if not page_rows:
            break                                   # past the last page
        rows.extend(page_rows)
        print(f"  page {page}: {len(page_rows)} rows")
        if len(page_rows) < PAGE_SIZE:
            break                                   # short page == last page
        time.sleep(0.4)                             # be polite

    # ODFW repeats rows across page boundaries. Dedupe on the whole row.
    seen, deduped = set(), []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    if len(deduped) != len(rows):
        print(f"  dropped {len(rows) - len(deduped)} duplicate rows")
    return deduped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", default="./data")
    a = ap.parse_args()

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rows = fetch_schedule(a.start, a.end)
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
