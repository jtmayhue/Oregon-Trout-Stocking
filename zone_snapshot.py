#!/usr/bin/env python3
"""
ODFW zone fishing report snapshotter.

These reports are the ONLY per-waterbody confirmation that a stocking actually
happened. They are overwritten weekly and ODFW keeps no archive, so every week
this does not run is a week permanently lost.

Two jobs, in this order of importance:
  1. Archive the raw HTML verbatim, forever. This is the irreplaceable part.
  2. Extract confirmed stockings. Re-runnable later against better parsing,
     because job 1 kept the source.

Never let job 2 failing stop job 1 from succeeding.

  pip install requests beautifulsoup4 lxml
  python zone_snapshot.py --root ./archive
  # weekly:  0 6 * * 1  cd /path && python zone_snapshot.py --root ./archive
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://myodfw.com"
INDEX = f"{BASE}/recreation-report/fishing-report"
UA = {"User-Agent": "oregon-stocking-archive/0.1 (contact: you@example.com)"}

# Seed only. Zones are DISCOVERED at runtime; ODFW lists nine and publishes no
# slug index, so hardcoding is how you silently lose a zone.
# Verified live 2026-09-03. Marine dropped: saltwater, nothing stocked.
# These 7 return 200. "columbia-river-zone" 404s
# despite being named on the index page -- discovery is still what decides.
SEED_ZONES = [
    "northwest-zone", "southwest-zone", "willamette-zone", "central-zone",
    "northeast-zone", "southeast-zone", "snake-zone",
]

# ---------------------------------------------------------------- extraction

SIZE_CLASSES = r"(?:legals?|trophy|brood|fingerlings?|rainbows?|trout|bass|steelhead|kokanee)"
MONTHS = (r"(?:January|February|March|April|May|June|July|August|September|"
          r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)")

# Past tense only. Future-tense lines are PLANS and must never be stored as
# confirmations -- that distinction is the entire value of this dataset.
CONFIRMED_RE = re.compile(
    r"\b(was|were)\s+(?:last\s+)?stocked\b|\breceived\s+(?:a\s+)?stockings?\b|"
    r"\bwas\s+planted\b|\bwere\s+planted\b", re.I)
FUTURE_RE = re.compile(
    r"\b(will\s+be\s+stocked|is\s+scheduled|are\s+scheduled|to\s+be\s+stocked|"
    r"upcoming|expected\s+to\s+be\s+stocked|slated)\b", re.I)

# Allow up to two adjectives between the number and the species:
# "2,000 larger rainbows", "1,500 legal sized rainbows", "100 trophy largemouth bass".
COUNT_RE = re.compile(
    rf"\b(?:about\s+|over\s+|approximately\s+|roughly\s+)?([\d,]{{2,}})\s+"
    rf"(?:(?!and\b)\w+\s+){{0,2}}?({SIZE_CLASSES})", re.I)
DATE_RE = re.compile(rf"\b((?:early|mid|late)\s+)?({MONTHS})\b(?:\s+(\d{{4}}))?", re.I)


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


# ODFW writes "Devils lake" as often as "Devils Lake". Match the water word
# case-insensitively or you silently drop rows.
WATER_WORD = r"(?:[Ll]akes?|[Pp]onds?|[Rr]eservoirs?|[Rr]iver|[Cc]reek|[Ss]lough|[Bb]ay)"
ANAPHORA_RE = re.compile(r"^\s*(?:The|This|That|It)\b", re.I)
# A candidate starting with one of these is a back-reference, not a name.
# Caught live: "The lake was..." mid-sentence after a heading, and "Both lakes
# were stocked" -- both were being stored as waterbody names.
STOPWORD_HEAD_RE = re.compile(
    r"^(?:The|This|That|These|Those|Both|Each|All|Some|Any|Other|Another|"
    r"Two|Three|Several|Many|It|Its|Their|Our|Several)\b", re.I)
PAIR_RE = re.compile(r"\b(North|South|East|West|Upper|Lower|Big|Little)\s+and\s+"
                     r"(North|South|East|West|Upper|Lower|Big|Little)\s+", re.I)


def guess_waterbody(sentence: str):
    """Return (name_or_None, review_reason_or_None). Never guess between two
    candidates -- an ambiguous row is flagged, not resolved."""
    if PAIR_RE.search(sentence):
        # "North and South Twin Lakes" is TWO waters. Picking one is a data bug.
        return None, "coordinated_pair: sentence names two waters"

    if ANAPHORA_RE.match(sentence):
        # "The lake was last stocked..." -- the name lives in a heading above,
        # which this sentence-level pass cannot see.
        return None, "anaphoric_reference: needs section heading context"

    for pat in (rf"\s*((?:[A-Z][\w'’.-]*\s+){{0,4}}?{WATER_WORD})\b",
                rf"\b((?:[A-Z][\w'’.-]*\s+){{0,3}}{WATER_WORD})\b"
                rf"(?=[^.]*\b(?:was|were|received)\b)"):
        m = re.match(pat, sentence) if pat.startswith(r"\s*") else re.search(pat, sentence)
        if m:
            cand = m.group(1).strip()
            if STOPWORD_HEAD_RE.match(cand):
                # "The lake" / "Both lakes" -- a reference, not a name.
                if re.match(r"^(?:Both|Two|Three|Several|These|Those|All)\b", cand, re.I):
                    return None, "coordinated_pair: refers to multiple waters"
                return None, "anaphoric_reference: needs section heading context"
            return cand, None
    return None, "no_waterbody_found"


# Section headings are ALL-CAPS followed by a colon, e.g. "HAYSTACK RESERVOIR:"
# or "TAYLOR LAKE (Wasco County):". They resolve the anaphora in "The lake
# was last stocked..." -- which is how most of these reports are actually written.
HEADING_RE = re.compile(r"\b([A-Z][A-Z0-9\s'’&().\-]{3,60}?):")
UPDATED_RE = re.compile(r"Last\s+updated\s+(\d{1,2}/\d{1,2}/\d{2,4})", re.I)


def _real_counts(sentence: str) -> list[dict]:
    """Extract fish counts, rejecting years masquerading as quantities.

    Caught live: "Juniper Lake was stocked in 2024 with fingerling rainbow
    trout" recorded 2,024 fish. "stocked in early July 2025 with small rainbow
    trout fry" recorded 2,025 fish. Both are fabricated numbers -- the exact
    class of error that makes an angler stop trusting the app.
    """
    out = []
    for m in COUNT_RE.finditer(sentence):
        raw, cls = m.group(1), m.group(2)
        n = int(raw.replace(",", ""))
        before = sentence[:m.start(1)]
        looks_like_year = (
            1900 <= n <= 2099
            and "," not in raw                       # real counts get commas
            and bool(re.search(rf"(?:{MONTHS}|\bin|\bsince)\s*$", before, re.I))
        )
        if looks_like_year:
            continue
        out.append({"count": n, "class": cls.lower()})
    return out


def extract(text: str, zone: str, captured: str) -> list[dict]:
    out = []
    heading = None
    section_updated = None
    for s in split_sentences(text):
        for m in HEADING_RE.finditer(s):
            cand = m.group(1).strip()
            if not re.search(r"LAST UPDATED|CHECK|REGULATION", cand, re.I):
                heading = cand
        um = UPDATED_RE.search(s)
        if um:
            section_updated = um.group(1)

        if not CONFIRMED_RE.search(s):
            continue
        if FUTURE_RE.search(s):
            continue                      # a plan wearing a receipt's clothes
        counts = _real_counts(s)
        water, reason = guess_waterbody(s)
        if reason in ("anaphoric_reference: needs section heading context",
                      "no_waterbody_found") and heading:
            water, reason = heading.title(), None   # resolved by section heading
        dates = [re.sub(r"\s+", " ", " ".join(x for x in g if x)).strip()
                 for g in DATE_RE.findall(s)]

        out.append({
            "zone": zone,
            "captured_at": captured,
            "waterbody_text": water,
            "counts": counts,
            "date_text": "; ".join(dates) if dates else None,
            "section_heading": heading or "",
            "section_last_updated": section_updated or "",  # sections go stale
                                           # INDEPENDENTLY -- one said 7/27 while
                                           # the page said 9/3. Never present a
                                           # confirmation without this date.
            "source_sentence": s,          # ALWAYS keep. A fact without its sentence
                                           # is unauditable and therefore worthless.
            "has_counts": bool(counts),    # completeness, NOT a review trigger
            "needs_review": reason is not None,   # identity problems only
            "review_reason": reason or "",
        })
    return out

# ------------------------------------------------------------------ fetching

def discover_zones(session) -> list[str]:
    try:
        r = session.get(INDEX, headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        found = set()
        for a in soup.find_all("a", href=True):
            m = re.search(r"/recreation-report/fishing-report/([a-z0-9-]+-zone)", a["href"])
            if m:
                found.add(m.group(1))
        if found:
            new = sorted(found - set(SEED_ZONES))
            if new:
                print(f"  discovered zones not in seed list: {new}")
            return sorted(found | set(SEED_ZONES))
    except Exception as e:
        print(f"  zone discovery failed ({e}); falling back to seed list")
    return SEED_ZONES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="./archive")
    ap.add_argument("--no-extract", action="store_true")
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d")
    root = Path(a.root)
    raw_dir = root / "raw" / stamp
    raw_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    zones = discover_zones(session)
    print(f"{len(zones)} zones")

    hashes_path = root / "hashes.json"
    hashes = json.loads(hashes_path.read_text()) if hashes_path.exists() else {}

    manifest, facts, failures = [], [], []
    for z in zones:
        url = f"{INDEX}/{z}"
        try:
            r = session.get(url, headers=UA, timeout=45)
            if r.status_code == 404:
                print(f"  {z}: 404 (zone may not exist)")
                continue
            r.raise_for_status()
        except Exception as e:
            print(f"  {z}: FETCH FAILED {e}")
            failures.append({"zone": z, "error": str(e)})
            continue

        digest = hashlib.sha256(r.content).hexdigest()
        # JOB 1 -- always write the raw file, even if unchanged. Disk is cheap;
        # a missing week is not recoverable.
        (raw_dir / f"{z}.html").write_bytes(r.content)
        changed = hashes.get(z) != digest
        hashes[z] = digest

        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        (raw_dir / f"{z}.txt").write_text(text)

        manifest.append({"zone": z, "url": url, "sha256": digest,
                         "changed": changed, "bytes": len(r.content)})
        print(f"  {z}: {len(r.content):>7} bytes  {'CHANGED' if changed else 'unchanged'}")

        if not a.no_extract:
            try:
                facts.extend(extract(text, z, now.isoformat()))
            except Exception as e:
                # JOB 2 failing must never lose JOB 1
                print(f"  {z}: extraction failed ({e}); raw HTML is still saved")
                failures.append({"zone": z, "error": f"extract: {e}"})

    (raw_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    hashes_path.write_text(json.dumps(hashes, indent=2))

    # ODFW repeats blocks within a page; dedupe identical facts.
    if facts:
        seen_f, unique = set(), []
        for f in facts:
            k = (f["zone"], f["waterbody_text"], f["source_sentence"])
            if k in seen_f:
                continue
            seen_f.add(k)
            unique.append(f)
        if len(unique) != len(facts):
            print(f"  deduped {len(facts) - len(unique)} repeated sentences")
        facts = unique

    if facts:
        out = root / "confirmed_stockings.csv"
        exists = out.exists()

        # Dedupe against what is ALREADY in the file, so re-running after a
        # parser fix updates cleanly instead of piling up duplicates.
        prior = set()
        if exists:
            with open(out, newline="") as f:
                for row in csv.DictReader(f):
                    prior.add((row.get("zone", ""), row.get("waterbody_text", ""),
                               row.get("source_sentence", "")))
            before = len(facts)
            facts = [f for f in facts
                     if (f["zone"], f["waterbody_text"] or "",
                         f["source_sentence"]) not in prior]
            if before != len(facts):
                print(f"  {before - len(facts)} already recorded; appending "
                      f"{len(facts)} new")
            if not facts:
                print("  nothing new this run")
        with open(out, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["zone", "captured_at", "waterbody_text",
                                              "date_text", "counts", "has_counts",
                                              "needs_review",
                                              "review_reason", "section_heading",
                                              "section_last_updated",
                                              "source_sentence"])
            if not exists:
                w.writeheader()
            for fact in facts:
                w.writerow({**fact, "counts": json.dumps(fact["counts"])})
    if facts:
        review = sum(1 for f in facts if f["needs_review"])
        counts = sum(1 for f in facts if f["has_counts"])
        print(f"\n{len(facts)} confirmed stockings -> {out}")
        print(f"  {len(facts) - review} usable, {review} need review")
        print(f"  {counts} include fish counts, {len(facts) - counts} confirmed without a number")

    if failures:
        (root / f"failures_{stamp}.json").write_text(json.dumps(failures, indent=2))
        print(f"{len(failures)} failures logged")

    print(f"\nRaw HTML archived at {raw_dir} -- this is the part that cannot be redone.")
    return 1 if len(manifest) == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
