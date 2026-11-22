#!/usr/bin/env python3
"""Build news.json for the This Week news card (Oregon and Washington).

Five sources, each fail-soft: if one breaks, the others still publish and the
run records the error in news.json["sources"]. Nothing here rewrites what an
agency said. Headlines are copied as published; the only titles we compose are
for table rows that have no headline (OHA algae listings, WDFW stocking days),
and those state only what the row says.

  OR  ODFW news releases      myodfw.com/news-releases        (filtered)
  OR  ODFW Recreation Report  myodfw.com/recreation-report    (weekly)
  OR  OHA toxic algae table   oregon.gov ... Blue-GreenAlgaeAdvisories.aspx
                              (table is drawn by JavaScript; needs Playwright)
  WA  WDFW news releases      wdfw.wa.gov/newsroom/rss        (filtered)
  WA  WDFW trout plants       wdfw.wa.gov/fishing/reports/stocking/trout-plants

Usage:
  python news.py --out news.json [--prev news.json]
  python news.py --fixtures tests/fixtures --now 2026-11-19 --out /tmp/n.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

KEEP_DAYS = 30          # what the file holds; the app shows the last 14
MAX_PER_STATE = 20

ODFW_NEWS = "https://myodfw.com/news-releases"
ODFW_REPORT = "https://myodfw.com/recreation-report"
ODFW_FISHING_REPORT = "https://myodfw.com/recreation-report/fishing-report"
OHA_PAGE = ("https://www.oregon.gov/oha/ph/healthyenvironments/recreation/"
            "harmfulalgaeblooms/pages/blue-greenalgaeadvisories.aspx")
WDFW_RSS = "https://wdfw.wa.gov/newsroom/rss"
WDFW_PLANTS = "https://wdfw.wa.gov/fishing/reports/stocking/trout-plants"

UA = "PNWTroutStocking-news/1.0 (+https://oregon-trout-stocking.pages.dev/support)"

# --------------------------------------------------------------------------
# Filter: keep releases that change what an angler can do or should know now.
# Tested Nov 19 2026 against a hand-sorted year of ODFW releases (150, 52
# fishing): 52 of 52 kept, 1 extra (Chinese mitten crab sighting). WDFW's 25
# latest: 7 of 7 kept, 0 extra. The rules were tuned on those same lists.
# --------------------------------------------------------------------------
INCLUDE = re.compile(
    r"\b(fish|fishing|fishery|fisheries|angl\w*|trout|salmon|steelhead|chinook|"
    r"coho|sockeye|sturgeon|smelt|eulachon|clams?|clamming|crab\w*|mussels?|"
    r"shellfish|halibut|rockfish|bottomfish|groundfish|kokanee|bass|carp|"
    r"hatchery|two-rods?|two rods?|two-pole|bag limits?|gear restrictions?|"
    r"quagga|algae)\b", re.I)
EXCLUDE = re.compile(
    r"\b(meets?|met|meeting|meetings|town hall|committee|board|task forces?|"
    r"council|advisory groups?|comments?|petition|applicants?|applications?|"
    r"apply|candidates?|nominees?|nominations?|volunteer|grants?|funding|"
    r"budget|research\w*|passage|screening|entanglements?|commercial|"
    r"informational|review|alternatives|statement|bulletin)\b", re.I)
_AGENCY = re.compile(r"fish\s*(&|and)\s*wildlife", re.I)


def is_angler_news(title: str) -> bool:
    t = _AGENCY.sub(" ", title)
    return bool(INCLUDE.search(t)) and not EXCLUDE.search(t)


# --------------------------------------------------------------------------
def clean(s: str) -> str:
    s = s.replace("​", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def item(state, kind, source, title, date: dt.date, url) -> dict:
    title = clean(title)
    key = f"{state}|{kind}|{title}|{date.isoformat()}"
    return OrderedDict(
        id=hashlib.sha1(key.encode()).hexdigest()[:12],
        state=state, kind=kind, source=source,
        title=title, date=date.isoformat(), url=url,
    )


def parse_long_date(s: str) -> dt.date | None:
    s = clean(s).replace(".", "")
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d.date()
        except ValueError:
            continue
    return None


# ---------------------------- Oregon --------------------------------------
def odfw_news(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for art in soup.select("article.press-release-search-result"):
        a = art.find("a", href=True)
        h = art.find("h3")
        d = art.select_one(".field--name-field-published-date")
        if not (a and h and d):
            continue
        date = parse_long_date(d.get_text())
        title = clean(h.get_text())
        if not date or not is_angler_news(title):
            continue
        href = re.sub(r"/index%2[eE]php/", "/", a["href"])
        out.append(item("OR", "news", "ODFW", title, date, urljoin(ODFW_NEWS, href)))
    if not soup.select("article.press-release-search-result"):
        raise ValueError("no press releases found on page (layout changed?)")
    return out


def odfw_report(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    for intro in soup.select(".field--name-field-intro"):
        date = parse_long_date(intro.get_text())
        if date:
            return [item("OR", "report", "ODFW", "Weekly Recreation Report",
                         date, ODFW_FISHING_REPORT)]
    raise ValueError("report date not found (layout changed?)")


_STATUS = [
    (re.compile(r"advisory has been lifted|advisory lifted", re.I), "Toxic algae advisory lifted"),
    (re.compile(r"precaution has been lifted|precaution lifted", re.I), "Toxic algae precaution lifted"),
    (re.compile(r"advisory in effect", re.I), "Toxic algae advisory"),
    (re.compile(r"precaution in effect", re.I), "Toxic algae precaution"),
]


def oha_algae(html: str) -> list[dict]:
    """Rows of OHA's current-advisories table. Permanent advisories (no date)
    are skipped: they are standing signs, not news. The same water listed once
    per county collapses to one item."""
    soup = BeautifulSoup(html, "lxml")
    rows = [r for r in soup.select("table tr") if len(r.find_all("td")) >= 5]
    if not rows:
        raise ValueError("advisory table not found (page not rendered?)")
    out, seen = [], set()
    for r in rows:
        tds = r.find_all("td")
        name = clean(tds[0].get_text())
        status = clean(tds[2].get_text())
        date = parse_long_date(tds[3].get_text())
        if not name or not date:
            continue
        label = next((lab for rx, lab in _STATUS if rx.search(status)), None)
        if not label:
            continue
        key = (name.lower(), label, date)
        if key in seen:
            continue
        seen.add(key)
        link = tds[0].find("a", href=True)
        url = urljoin(OHA_PAGE, link["href"]) if link and "govdelivery" in link["href"] else OHA_PAGE
        out.append(item("OR", "algae", "Oregon Health Authority", f"{label}: {name}", date, url))
    return out


# ---------------------------- Washington ----------------------------------
def wdfw_news(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    out = []
    items = root.findall("./channel/item")
    if not items:
        raise ValueError("RSS has no items")
    for it in items:
        title = clean(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        date = parse_long_date(it.findtext("pubDate") or "")
        if title and link and date and is_angler_news(title):
            out.append(item("WA", "news", "WDFW", title, date, link))
    return out


_ABBR = {"LK": "Lake", "LKS": "Lakes", "PD": "Pond", "PDS": "Ponds", "CR": "Creek",
         "RES": "Reservoir", "R": "River", "SL": "Slough", "N": "North", "S": "South",
         "E": "East", "W": "West", "MT": "Mount", "FT": "Fort", "ST": "St."}


def wa_water_name(raw: str) -> str:
    """'BALLINGER LK (SNOH)' -> 'Ballinger Lake'; 'SILVER LK (SNOH) T28' -> 'Silver Lake';
    'CRANBERRY CR 05.0390' -> 'Cranberry Creek'."""
    s = re.sub(r"\([^)]*\)", " ", raw)               # county code
    s = re.sub(r"\b(T\d+|\d+(\.\d+)?)\b", " ", s)     # township / stream numbers
    def word(w: str) -> str:
        up = w.upper().strip(",")
        if up in _ABBR:
            return _ABBR[up]
        if "-" in w:  # 'LK-N' -> 'Lake North'; 'SPRAGUE-X' -> 'Sprague-X'
            parts = w.split("-")
            if all(p.upper() in _ABBR for p in parts if p):
                return " ".join(_ABBR[p.upper()] for p in parts if p)
            return "-".join(word(p) for p in parts)
        return w.capitalize() if w.isupper() else w
    return " ".join(word(w) for w in s.split())


def _join_names(names: list[str]) -> str:
    if len(names) <= 3:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{', '.join(names[:2])} and {len(names) - 2} more waters"


def wdfw_plants(html: str) -> list[dict]:
    """One item per stocking day: total catchable trout and where they went."""
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table tbody tr")
    if not rows:
        raise ValueError("plant table not found (layout changed?)")
    days: dict[dt.date, dict] = {}
    for r in rows:
        lake = r.select_one("td.views-field-lake-stocked a")
        date_td = r.select_one("td.views-field-stock-date")
        num_td = r.select_one("td.views-field-num-fish")
        if not (lake and date_td and num_td):
            continue
        date = parse_long_date(date_td.get_text())
        try:
            n = int(clean(num_td.get_text()).replace(",", ""))
        except ValueError:
            continue
        if not date:
            continue
        d = days.setdefault(date, {"fish": 0, "names": []})
        d["fish"] += n
        name = wa_water_name(clean(lake.get_text()))
        if name not in d["names"]:
            d["names"].append(name)
    out = []
    for date, d in sorted(days.items(), reverse=True):
        title = f"{d['fish']:,} trout stocked at {_join_names(d['names'])}"
        out.append(item("WA", "stocking", "WDFW stocking report", title, date, WDFW_PLANTS))
    return out


# ---------------------------- fetching ------------------------------------
def fetch_text(url: str) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
    r.raise_for_status()
    return r.text


def fetch_rendered(url: str, wait_selector: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=UA)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(wait_selector, timeout=45000)
        html = page.content()
        b.close()
    return html


SOURCES = [
    # name, parser, live fetch, fixture file
    ("odfw_news", odfw_news, lambda: fetch_text(ODFW_NEWS), "odfw_news.html"),
    ("odfw_report", odfw_report, lambda: fetch_text(ODFW_REPORT), "odfw_report.html"),
    ("oha_algae", oha_algae, lambda: fetch_rendered(OHA_PAGE, "table tbody tr td:nth-child(4)"), "oha.html"),
    ("wdfw_news", wdfw_news, lambda: fetch_text(WDFW_RSS), "wdfw_rss.xml"),
    ("wdfw_plants", wdfw_plants, lambda: fetch_text(WDFW_PLANTS), "wdfw_plants.html"),
]


def build(now: dt.date, fixtures: Path | None, prev: dict | None) -> dict:
    status, items = OrderedDict(), []
    prev_items = (prev or {}).get("items", [])
    for name, parse, live, fixture in SOURCES:
        try:
            raw = (fixtures / fixture).read_text() if fixtures else live()
            got = parse(raw)
            status[name] = f"ok ({len(got)})"
            items += got
        except Exception as e:  # keep what we had from this source last time
            status[name] = f"error: {type(e).__name__}: {str(e)[:160]}"
            items += [i for i in prev_items if i.get("_src") == name]
            continue
        for i in got:
            i["_src"] = name
    cutoff = now - dt.timedelta(days=KEEP_DAYS)
    keep = [i for i in items if cutoff <= dt.date.fromisoformat(i["date"]) <= now + dt.timedelta(days=1)]
    uniq = OrderedDict((i["id"], i) for i in keep)
    out_items = []
    for st in ("OR", "WA"):
        mine = sorted((i for i in uniq.values() if i["state"] == st),
                      key=lambda i: (i["date"], i["kind"] != "news"), reverse=True)
        out_items += mine[:MAX_PER_STATE]
    return OrderedDict(
        generated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        show_days=14,
        sources=status,
        items=out_items,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev")
    ap.add_argument("--fixtures")
    ap.add_argument("--now", help="YYYY-MM-DD (tests)")
    a = ap.parse_args(argv)
    now = dt.date.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc).date()
    prev = None
    if a.prev and Path(a.prev).exists():
        try:
            prev = json.loads(Path(a.prev).read_text())
        except Exception:
            prev = None
    data = build(now, Path(a.fixtures) if a.fixtures else None, prev)
    same = prev is not None and {k: v for k, v in prev.items() if k != "generated"} == \
        {k: v for k, v in json.loads(json.dumps(data)).items() if k != "generated"}
    if same and Path(a.out).exists():
        # Nothing new: leave the file untouched so the run commits nothing and
        # Cloudflare Pages does not rebuild for a timestamp.
        print("no change since last run")
    else:
        Path(a.out).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    for k, v in data["sources"].items():
        print(f"{k:12s} {v}")
    print(f"items: OR {sum(i['state']=='OR' for i in data['items'])}, "
          f"WA {sum(i['state']=='WA' for i in data['items'])}")
    failed = [k for k, v in data["sources"].items() if v.startswith("error")]
    if len(failed) == len(SOURCES):
        print("::error::every news source failed", file=sys.stderr)
        return 1
    for k in failed:
        print(f"::warning::news source {k} failed: {data['sources'][k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
