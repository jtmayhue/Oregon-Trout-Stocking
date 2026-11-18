#!/usr/bin/env python3
"""
Confirmed stockings from ODFW's Recreation Report tables.

Since mid-October 2026 the zone fishing reports carry a "Trout Stocking" table
(Week, Hatchery, Zone, Location, Legals, 1 LB, Trophy) in place of the old
per-water prose. Those rows are what was actually stocked, and they already
differ from the planned schedule (Hagg Lake got 9,000 the week of Nov 2 against
a plan of 4,100 on Oct 19; Cottage Grove, Dexter and Dorena got fish the
schedule never listed).

This reads EVERY archived page in archive/raw on each run, so it is idempotent
and backfills on its own. Output is a CSV the app does not read yet: the point
is to build the record now and learn by spring how complete it is.

    python report_tables.py --archive ./archive --waters ./waters.json \
        --out ./archive/stocking_reports.csv

It never fails the workflow: a parse problem prints a warning and exits 0,
because the raw HTML is already safe and can be re-read later.
"""
import argparse, csv, json, re, sys
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

ABBR = {"res": "reservoir", "lk": "lake", "pd": "pond", "pds": "ponds",
        "cr": "creek", "r": "river", "st": "saint"}


def tokens(name: str) -> list[str]:
    t = re.sub(r"[’']", "", name.lower())
    t = re.sub(r"[^a-z0-9#]+", " ", t).split()
    out = []
    for w in t:
        w = ABBR.get(w, w)
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") else w)   # Lakes ~ Lake
    return out


def week_iso(s: str):
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat() if m else None


def num(s: str) -> int:
    s = (s or "").replace(",", "").strip()
    return int(s) if s.isdigit() else 0


def parse_page(html: str) -> list[dict]:
    rows = []
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        head = [c.get_text(" ", strip=True).upper() for c in trs[0].find_all(["td", "th"])]
        if not head or "WEEK" not in head[0] or "LOCATION" not in head:
            continue
        week = hatchery = None                      # blank cells repeat the row above
        for tr in trs[1:]:
            cells = [c.get_text(" ", strip=True).replace("\xa0", " ").strip()
                     for c in tr.find_all(["td", "th"])]
            row = dict(zip(head, cells))
            if row.get("WEEK"):
                week = week_iso(row["WEEK"]) or week
            if row.get("HATCHERY"):
                hatchery = row["HATCHERY"]
            loc = row.get("LOCATION", "").strip()
            if not loc or not week:
                continue
            rows.append({"week": week, "hatchery": hatchery or "",
                         "zone": row.get("ZONE", ""), "location": loc,
                         "legals": num(row.get("LEGALS")),
                         "one_lb": num(row.get("1 LB")),
                         "trophy": num(row.get("TROPHY"))})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="./archive")
    ap.add_argument("--waters", default="./waters.json")
    ap.add_argument("--out", default="./archive/stocking_reports.csv")
    a = ap.parse_args()

    waters = json.loads(Path(a.waters).read_text())["waters"]
    index = []                                       # (token set, water id)
    for w in waters:
        for n in [w["name"], *w["odfw_names"]]:
            index.append((set(tokens(n)), w["id"]))

    def match(location: str):
        lt = set(tokens(location))
        exact = {wid for ts, wid in index if ts == lt}
        if len(exact) == 1:
            return exact.pop()
        within = {wid for ts, wid in index if lt and lt <= ts}
        if len(within) == 1:
            return within.pop()
        # ODFW sometimes abbreviates a word to one letter ("S Louis Ponds" for
        # St. Louis Ponds). Retry without single letters, if still unambiguous.
        core = {t for t in lt if len(t) > 1}
        if core and core != lt:
            loose = {wid for ts, wid in index if core <= ts}
            if len(loose) == 1:
                return loose.pop()
        return ""

    found = {}
    pages = sorted(Path(a.archive, "raw").glob("*/*.html"))
    for page in pages:
        snap = page.parent.name
        try:
            rows = parse_page(page.read_text(errors="replace"))
        except Exception as e:
            print(f"::warning::Could not parse {page}: {e}")
            continue
        for r in rows:
            key = (r["week"], " ".join(tokens(r["location"])),
                   r["legals"], r["one_lb"], r["trophy"])
            if key in found:
                found[key]["last_seen"] = max(found[key]["last_seen"], snap)
                found[key]["first_seen"] = min(found[key]["first_seen"], snap)
            else:
                found[key] = {**r, "water_id": match(r["location"]),
                              "first_seen": snap, "last_seen": snap}

    rows = sorted(found.values(), key=lambda r: (r["week"], r["location"]))
    out = Path(a.out)
    with open(out, "w", newline="") as f:
        cols = ["week", "location", "water_id", "legals", "one_lb", "trophy",
                "hatchery", "zone", "first_seen", "last_seen"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in cols})

    matched = sum(1 for r in rows if r["water_id"])
    unmatched = sorted({r["location"] for r in rows if not r["water_id"]})
    print(f"{len(pages)} archived pages -> {len(rows)} reported stockings "
          f"({matched} matched to a water) -> {out}")
    if unmatched:
        print(f"  not matched to a water in the app: {', '.join(unmatched)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                 # never fail the workflow over this
        print(f"::warning::report_tables.py failed: {e}")
        sys.exit(0)
