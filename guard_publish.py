#!/usr/bin/env python3
"""
Refuse to publish a stockings file that lost a chunk of a year it shares with
the copy already published.

    python guard_publish.py --new stockings-v2.json --old /tmp/prev/stockings-v2.json

Only years present in BOTH files are compared, so the New Year roll-forward
(last year drops off, next year appears) never trips it. A year that had at
least 50 rows and now has under 85% of them means ODFW changed something, and a
thinner file must not replace a good one. On failure the old file is copied
back over the new one and the exit code is 1, so the workflow run goes red and
GitHub emails you. The app keeps showing the previous data meanwhile.
"""
import argparse, json, shutil, sys
from collections import Counter
from pathlib import Path

MIN_ROWS = 50
KEEP = 0.85


def per_year(path: Path) -> Counter:
    doc = json.loads(path.read_text())
    return Counter(e["week"][:4] for evs in doc["stockings"].values() for e in evs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", required=True)
    a = ap.parse_args()
    new, old = Path(a.new), Path(a.old)

    if not old.exists():
        print(f"No previous {new.name} to compare against; publishing.")
        return
    if not new.exists():
        sys.exit(f"{new} was not built.")

    n, o = per_year(new), per_year(old)
    problems = []
    for year in sorted(set(n) & set(o)):
        flag = ""
        if o[year] >= MIN_ROWS and n[year] < KEEP * o[year]:
            flag = "  <-- dropped too far"
            problems.append(year)
        print(f"  {year}: {o[year]} rows before, {n[year]} now{flag}")
    for year in sorted(set(o) - set(n)):
        if o[year] >= MIN_ROWS and year > min(n or o):   # a later year vanished
            print(f"  {year}: {o[year]} rows before, MISSING now")
            problems.append(year)

    if problems:
        shutil.copyfile(old, new)
        sys.exit(f"Refusing to publish {new.name}: {', '.join(problems)} lost too "
                 f"many rows. The previous file was kept.")
    print(f"{new.name} OK to publish.")


if __name__ == "__main__":
    main()
