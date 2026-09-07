# Finishing the trout app

## The one decision everything else hangs on

The whole product is *"tell me when my water is stocked."* That is a **notification**,
and notifications are what make this architecturally different from Marea.

Marea is local-first with no backend because every fact in it is something the user
typed. Here the facts come from ODFW, which means something has to fetch them and
something has to poke the phone. There are two honest ways to do that.

### Option A — server plus real push
A backend runs the scrapers, stores waters and events, keeps every user's followed
waters and device token, and sends APNs when a match appears.

Real-time. Also: a server to run, a database to back up, APNs certificates to rotate,
user device tokens to store (which means a privacy policy with teeth), and $5–20/month
forever. You have never shipped this, and it is the part that would still be your
problem in two years.

### Option B — static JSON plus local notifications  ← recommended
**GitHub Actions** runs the scrapers weekly (free, no Mac required, no server).
It commits `waters.json` and `stockings.json` to the repo. **Cloudflare Pages** serves
them as static files — you already run mareacycle.com there. The app fetches that JSON
on a background refresh, diffs it against the waters you follow locally, and raises a
**local** notification.

No backend. No database. No accounts. No device tokens. No user data leaves the phone.
Same SwiftData spine as Marea, same philosophy, and the hosting is free.

**Why this isn't a compromise:** ODFW publishes at *week* granularity — "the week of
April 20" — and the confirmation reports lag by days or weeks anyway. A notification
that lands within a day of the data changing is indistinguishable from instant.
`BGAppRefreshTask` being opportunistic only matters if your data is real-time. Yours
isn't, and pretending otherwise would buy a server you don't need.

Go with B. Revisit only if you ever add something genuinely time-critical.

---

## What's left, in order

### Phase 1 — Finish the dataset (the real remaining work)
1. Re-run the full-year pull. **231 waters, not the 103 in `included_waters.csv`.**
2. Re-run the exclusion pass against 231. Expect the shipping set to roughly double
   and to pull in the destination fisheries — Detroit, Wallowa, Trillium, Olallie,
   Paulina, Cultus.
3. Attach coordinates. Pull ODFW's own MyMaps as KML (IDs are in `data_sources.md`);
   it carries lat/lon **and** amenities — restrooms, bank access, boat ramps, parking.
   Fall back to GNIS for anything it misses.
4. Join campgrounds via RIDB. Factual attributes only, no reviews.
5. Emit two files: `waters.json` (~231 records, changes rarely) and
   `stockings.json` (schedule plus confirmations, changes weekly).

### Phase 2 — Move the pipeline off your Mac
Put this repo on GitHub and run `zone_snapshot.py` and `odfw_ingest.py` in a weekly
Action. Commit the raw HTML into the repo as part of the run.

**This also fixes what just happened.** The Sept 3 capture lived only in a folder on
one machine, so deleting the folder destroyed it. In git, the archive has history,
lives on GitHub, and keeps running whether or not your Mac is awake.

### Phase 3 — The app (Swift, reuse Marea's spine)
- Water list, searchable, with the alias table behind the search.
- Follow / unfollow, stored locally in SwiftData.
- Water detail: last confirmed stocking **with the section's own date**, upcoming
  scheduled weeks, campground, access amenities.
- `BGAppRefreshTask` fetches the JSON; diff against followed waters; local notification.
- No accounts. No feed. No reviews.

### Phase 4 — Ship
Free or cheap, Oregon only. "Oregon trout stocking" in the App Store does the
distribution work that a content campaign would otherwise have to.

---

## Non-negotiables carried over

1. **Never show a confirmation without its section date.** 29 of 37 sections were
   stale by weeks. A stocking "confirmed" by a page last touched in June is a
   different claim than one confirmed yesterday.
2. **Never present a plan as a confirmation.** The schedule is intent; the zone
   reports are the receipt. Keep them visually distinct in the UI.
3. **Never guess a water's identity.** Quarantine with a reason instead.
4. **Every fact traces to a government source, with a date.** That is the product.

## Rough effort

Phase 1 is a solid weekend of data work. Phase 2 is an afternoon. Phase 3 is the
actual app and the only part that is genuinely new to you — the background-refresh
and local-notification pieces are each a few dozen lines.
