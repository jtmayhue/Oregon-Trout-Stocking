# Full-year 2026 pull — what it changed

Pulled live from ODFW through the browser on this Mac. **20 pages, 959 rows,
231 distinct waterbodies.**

The September snapshot had 103. **It was missing 55% of the year.**

## The date filter works — I had the parameter names wrong

Not `start_date`. The form is Drupal and the real fields are:

```
https://myodfw.com/fishing/species/trout/stocking-schedule
  ?field_planned_stocking_date_value=2026-01-01
  &field_planned_stocking_date_end_value=2026-12-31
  &page=N
```

Two more corrections: use the **interactive page, not the print page** (the print
page 403s with any parameter, even from a real browser), and the results are
**paginated at 50 rows** — 20 pages for a full year.

## Timothy Lake

`TIMOTHY LK | Willamette / Clackamas | 4 stockings | 7,700 fish | May 11 – Jun 1`

Stocked. My September pull missed it because its season ended in June.

## Name collisions are worse than estimated, and now provable

One year of data, single strings covering multiple real waters:

- **`LOST LK`** — stocked by Newport, Tillamook **and** The Dalles offices. Three lakes, one string.
- **`FISH LK`** — Central Point, La Grande **and** Hines. Three lakes.
- **`CLEAR LK`** — Springfield and The Dalles. Two lakes.
- **`DEVIL'S LK` (Newport) vs `DEVILS LK` (Bend)** — **two different lakes that differ
  only by an apostrophe.** Normalize punctuation and you silently merge them.
- **`TWIN LK` (La Grande)** is a different water from **`TWIN LK, N/S` (Bend)**.

## `B` and `L` are Big and Little, not Lower

I earlier guessed `BURMA PD, L` meant Lower, low confidence. The full year settles it:
ODFW writes Lower as **`LWR`** (`EMPIRE LK, LWR`) and Upper as `UPR`. And it uses
bare `B` for Big — **`LAVA LK, B`**, **`CULTUS LK, B`**. So a bare `L` is **Little**.

## New source defects this pull exposed

- **Blank zone AND office**: `BOLAN LK`, `SMITH LK` have neither. `BENSON LK`,
  `NORTH LK`, `PILCHER CREEK RES`, `WOLF CREEK RES`, `SCOUT LK` have no office.
  Zone-based routing drops these entirely.
- **A fourth rename format**: `SCOUT LK (Fmrly Scout Camp Lk)` — now `former`,
  `FMR`, `-Fmr`, and `Fmrly`.
- **Trailing period**: `POISON CREEK RES.`
- **Hash numbering plus parenthetical**: `MORROW CO OHV PARK PD #2 (Red Rock)`,
  `#3 (O'Brien)`, `#4 (Wilson)` — three separate ponds.

## Major waters the September slice missed entirely

Detroit Reservoir is the biggest stocked water in the state at **53,400 fish across
6 events** and it was absent. So were Wallowa Lake (34,685), Trillium, Olallie,
Paulina, East, Hosmer, Three Creeks, Anthony, Magone, Jubilee, Lemolo, Laurance
and Cultus. Nearly all of them have campgrounds on the water.

Every one of those is a destination fishery. A product built on the September
snapshot would have looked broken to exactly the anglers you most want.
