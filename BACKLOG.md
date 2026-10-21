# Backlog

What we intend to build next, in priority order, with enough on each item that
someone could pick it up cold. Shipped work is described in the README; this
file is only what's ahead.

Background on the programs themselves is in
[docs/affordable-housing-categories.md](docs/affordable-housing-categories.md).

## 1. Affordable units — where they're being permitted and built

**Question.** Where in the city are affordable units going, and does that track
where market-rate housing is permitted?

**The hunch needs splitting before it can be tested.** "Affordable" covers two
things with opposite geography:

- *ARO on-site units* are carved out of market-rate buildings under the
  Affordable Requirements Ordinance. They are wherever the market builds — by
  construction, not by finding. Plotting them against permits is a tautology.
- *Subsidized developments* (LIHTC, DOH-financed multifamily, senior and
  supportive housing) follow land cost, allocation-plan scoring and city policy.
  Historically they cluster on the South and West sides, where market-rate
  permitting is thinnest.

The expected result is therefore *the affordable units the market produces go
where the market goes; the ones the city funds go where it doesn't.* That is a
sharper and more useful finding than a single correlation, and the page should
be designed to show the two series separately.

**Data.**

| Source | What it gives | Limits |
|---|---|---|
| `s6ha-ppgi` Affordable Rental Housing Developments | 598 city-assisted rental developments, 29,550 units, all geocoded, typed (Multifamily 259 / 12,916 units; Senior 106 / 9,809; ARO 156 / 1,727; supportive and other ~77 / ~5,100) | A **stock** directory: no dates. Drops projects when their ~30-year compliance period expires. Rental only — no for-sale ARO, no in-lieu fees. Excludes CHA and naturally-occurring affordable housing. Check whether `units` is restricted units or all units in mixed-income buildings. |
| Our permit pipeline (`ydr8-5enu`) | Permit dates for developments built since 2010, by address match | Catches new construction only; much subsidized activity is rehab or preservation and will not match. |
| HUD LIHTC database (huduser.gov, downloadable) | Placed-in-service year, address, low-income unit count for every tax-credit property | Federal program only; misses DOH-only deals. |
| DOH ARO project lists (PDF, chicago.gov) | Per project: on-site units vs in-lieu fee, by address | PDF wrangling. No portal dataset for ARO exists. |
| `p293-wvbd` wards | Point-in-polygon to current wards, same as permits | — |

**Phases.**

1. ~~**ARO.**~~ **Shipped** as `/affordable`. Source is the ArcGIS feature
   service behind the city's ARO map page, which is *not* on the data portal
   and is far better than `s6ha-ppgi`: refreshed 2026-02, with ward, unit
   counts and AMI tiers. It carries no date, so buildings are dated by
   matching to the nearest new-construction permit within 75 m (93% of units).
   Importer: `scripts/import_aro.py`.
2. **In-lieu fees — a note, not a project.** Developers may pay instead of
   building ARO units. The fees go to the City's affordable housing funds,
   which pay for City-financed deals already counted under LIHTC or for rent
   subsidies, so counting them as units would double-count. Worth one line
   on the page once sourced ("this many required units were bought out"),
   with a primary citation for any fee figure; the February 2024 ARO Rules
   PDF's text layer did not yield a fee table.
3. ~~**LIHTC.**~~ **Shipped** in `/affordable` alongside ARO (program
   switch; new vs preserved; permit year or opening year). Importer:
   `scripts/import_lihtc.py`, run by hand; sources are saved under
   `sources/` (git-ignored). HUD's file was reconciled against the City's
   round awards and 2010–2023 year-end reports, IHDA's award lists, closing
   reports and TEFRA notices, and City Council ordinances (2023–26, via the
   Clerk's API). Other City/State developments (no tax credits) are a program
   (CITY_FUNDED: 69 counted, 1,247 restricted units, including 39 NSP
   rental buildings); refinancing-only, for-sale and out-of-scope ones are
   in CITY_SET_ASIDE. The pipeline table lists both programs (43
   developments, 3,530 units awarded but not permitted). Rules decided
   2026-10: only units at or under 80% AMI count, but 81–120% units are
   kept in `over80`; a deal that newly restricts existing homes counts as
   preserved without a renovation; "new" needs documented vacancy or a
   building that was not housing.

   **Open, to pick up in the refresh routine (item 4):**
   - possibly stalled: Riverside Village, Calhoun, Englewood Phase I;
   - award not confirmed: Galleria 89, Pioneros, Lawndale ReDefined;
   - Southbridge 3A: the City's 48 may leave out CHA units; Teachers
     Village: CHA unit count not stated;
   - NSP: 36 of 39 buildings use the City's yearly at-or-under-80% share,
     not a published split;
   - PEAR loans small enough not to need an ordinance, and the City's TIF
     purchase-rehab units (totals only, no addresses);
   - IHDA's pre-FY2018 closing reports (not online) and its FY2026 report.
4. **Supportive housing.** Done as a "Who it serves" filter (general /
   senior / supportive) on the Affordable Units page, classed per building
   in the importer's SENIOR and SUPPORTIVE sets. Still to consider (decided
   2026-10 to keep in mind, not include yet): senior-only buildings with no
   tax credits and no City money, mostly HUD Section 202, which are outside
   every program on the page. `MULTIFAMILY_PROPERTIES_ASSISTED` (330 Chicago
   properties, client groups elderly / disabled / family) is the source.
5. **CHA.** `Public_Housing_Developments` filtered to `PARTICIPANT_CODE='IL002'`:
   103 developments, 19,651 units, current to 2026-07. No year-added field.
   Show *net* of removals or not at all.
   CHA renovations without tax credits are left out of the page for now —
   e.g. ABLA Brooks Homes, 330 units under renovation 2025–27 with City TIF
   for site work only (decided 2026-10-19); this item is where they would go.

The directory (`s6ha-ppgi`) remains useful as a cross-check on coverage and
as a secondary stock view, normalised per 1,000 households.

**External request, 2026-09-18.** A reader asked for "an additional map/view
that highlights LIHTC/ARO/CHA units added, and maybe another one for shelters
and supportive housing." This is the same pipeline framed by program rather
than by question, and it resolves two decisions:

- *Flow, not stock.* "Units added" is a flow question. The undated directory
  is not a first version; it is a cross-check.
- *CHA is in* — and it is the hardest source. No dataset gives CHA units
  added by year; CHA's annual Moving to Work reports list deliveries per
  development and would be hand-entered. **A CHA "added" figure without
  "removed" misleads:** the Plan for Transformation demolished on the order
  of 25,000 units and replaced fewer. Show net, or say so prominently.

Design consequence: one tab, *program* as a dimension. Map with program
filters (LIHTC, ARO, CHA, supportive), the scatter grouped market-linked vs
subsidized, one table.

**Overlap rule — decided 2026-09.** One building can be LIHTC-financed,
CHA-owned and supportive housing at once — most Plan for Transformation sites
are — so summing programs double- or triple-counts. Every development
therefore stores **all** its programs, plus a derived primary by precedence
(CHA > supportive > LIHTC > ARO, deepest subsidy wins the label). The two
views then answer different questions:

- **Unfiltered — attribution.** Each unit counted once, under its primary
  program. Totals are honest and reconcile to the deduplicated universe.
- **Filtered to a program — membership.** Every unit participating in that
  program, whatever else it participates in. A supportive + LIHTC building
  appears under LIHTC when supportive is filtered out.

**Consequence to design around: filtered figures do not sum to the unfiltered
total.** LIHTC-only plus supportive-only exceeds the combined figure, because
the overlap is in both. That is correct, but it forbids any chart implying
additivity — no stacked bars of program totals, no pie. Selecting two programs
must take the deduplicated union, not the sum. Say so on the page.

**Page structure — decided 2026-09.** The shipped `/affordable` tab, which is
entirely ARO, gets absorbed. The destination is one affordable-housing section
with **stock** and **flow** sub-tabs covering every program, ARO among them.

**Stock and flow are not equally available, and the sub-tabs must not promise
symmetry they cannot deliver:**

| Program | Stock | Flow |
|---|---|---|
| ARO | yes | **yes**, via permit matching, 92% of units dated |
| LIHTC | 42,576 units | placed-in-service records **stop in 2020** |
| HUD project-based | 30,572 units | no date field |
| CHA public housing | 19,651 units | no year-added field |
| City-financed | partial | no |

The technique that worked for ARO — take an undated stock list and date it
against construction permits by address — is the obvious thing to try on the
rest, and should work about as well, since these are also buildings that
pulled permits.

**Shelters are a separate section, not a layer.** Different department
(DFSS), measured in beds not units, no year-added, and the most contested
siting question in the city since 2023. Ward-level bed counts only, no
addresses. **Domestic-violence shelters are never mapped** — locations are
confidential for safety. Scope this separately, last.

**Sequence:** LIHTC and ARO shipped → supportive (a filter on data already
loaded) → CHA (after the net-vs-gross decision) → shelters, if at all.

**Resolved.** The directory's `units` field is *inconsistent*: across 35
mixed-income buildings matched to HUD records, it equalled HUD's total unit
count in 9 cases, HUD's assisted count in 2, and neither in 24. It cannot
carry a headline number without per-row reconciliation, which is another
reason the ARO page uses the DOH service instead. Normalisation settled as
"ARO units as a share of all new units permitted in the ward", which is the
most quotable framing and is what `/affordable` shows.

**Overlap, measured.** Matching on coordinates within 60 m: 43% of LIHTC
properties appear in the city directory, 22% of HUD-assisted properties do,
and 18% of LIHTC properties are also HUD-assisted. 385 of 598 city entries
match neither federal set, of which 152 are ARO. So the deduplicated
universe is roughly 60,000–70,000 income-restricted rental units, not the
~120,000 a naive sum would give.

## 1b. ~~New construction filed as renovation~~ — FIXED

Shipped. Chicago sometimes types a brand-new building as
`PERMIT - RENOVATION/ALTERATION` (the Thompson's permit reads "FULL BUILDING
PERMIT FOR PROPOSED NEW 12 STORY RESIDENTIAL"), and the unit extractor only
understood "units" and "D.U.", not "apartments". Both are fixed in
`scripts/import_permits.py`; the citywide total moved from 89,420 to 92,142.

**Still open: conversions.** Measured, not yet shipped. A net-units extractor
over renovation permits finds four parseable patterns:

| Pattern | Permits | Net units |
|---|---|---|
| Adaptive reuse (non-residential building to housing) | 114 | +2,694 |
| Explicit addition ("to provide 320 new apartments") | 88 | +657 |
| Before/after ("convert 5 D.U. to 6 D.U.") | 1,163 | −302 |
| Deconversion to single family | 516 | −632 |
| **Net** | **1,881** | **+2,417** |

Gains +4,098, losses −1,681, over 2010–2026. Concentrated recently: 2019
+620, 2025 +630, which is the LaSalle Street office-to-residential wave. Every
gain of 25 units or more was reviewed by hand and is correct — a 24-storey
office converting floors 4–24, a nursing home to 160 units, a parking garage
to 72, the Duncan's YMCA to 320.

Order of tests matters: check for deconversion wording *first*, or
"deconversion of 3 dwelling units to original 2" reads as +3. Exclude a hotel
as the *result*, since hotel rooms are not dwelling units.

**Accuracy.** A random sample of 25 was reviewed by hand: 24 correct, one
wrong. The failure was "addition to existing 2 dwelling unit... to create 3
dwelling units", where 3 is the total after the work, not an increment, so it
scored +3 instead of +1. Across the whole corpus only 1 of 94 addition-pattern
matches is ambiguous that way, worth +2 units, but the phrasing will recur and
needs a guard: if a permit states a prior unit count and says "to create/
provide N" without "add" or "new", treat N as the total.

Largest single loss is 2300 N Lincoln Park West, the Belden-Stratford, going
from 278 to 209 units on a $43M gut renovation of a 1920s hotel-residence.
Verified against the permit trail. **Decision: no special-casing.** It is
counted like any other loss, not flagged or framed separately; the table is
sortable if a reader wants to find it.

**These are floors, not totals.** Only permits with explicit parseable
language are caught; a conversion that never states a count is invisible.
Demolition losses are not in here at all — that is item 2.

**Why it matters for ARO.** `/affordable` counts ARO units in conversions
(currently 307 of 1,995) but expresses them as a share of a denominator that
excludes conversions, because `permits.json` is new construction only. That
comparison is inconsistent today. Adding a conversions series fixes it.

## 2. Demolitions — the reason nothing says "net"

Conversions and deconversions shipped; demolitions did not, and they are the
single largest gap in any claim about net housing supply. Investigated
thoroughly 2026-09; everything below is measured, not estimated.

### What the permits say

17,279 wrecking permits since 2010:

| Class | Permits | Share |
|---|---|---|
| Explicitly residential | 7,562 | 44% |
| Accessory structure (garage, shed, porch, deck) | 4,690 | 27% |
| **Generic — "building" or "N story" only** | **3,656** | **21%** |
| Explicitly non-residential | 996 | 6% |
| Unclear | 375 | 2% |

Beware two traps found the hard way. Text like `WRECK & REMOVE FRAMED
RESIDENTIAL GARAGE` contains "residential" but is a garage, so accessory
structures must be excluded *before* testing for residential wording. And the
bare word "BUILDING" is not evidence of housing — an earlier pass counted
12,176 residential demolitions by accepting it, against 7,562 confirmed.

**Quick win available:** some generic permits are residential but written
badly — `WRECK AND REMVOE A 2 STORY BRICK RES` is a typo for "remove" plus an
abbreviation of "residence". Matching `\bRES\b` would recover a chunk of the
3,656.

### Unit counts are the real blocker

Only **114 permits state a unit count**. Everything recoverable totals 2,413
units, an order of magnitude below the truth: at even 1.5 units per building
the unknowns imply roughly 20,000 units lost, which would take the 94,571 on
the site down towards 75,000. Publishing 2,413 as a demolition total would
mislead far worse than omitting it, which is why the permitting page says
"units permitted + converted" and states plainly that it is not net.

**The fix has to come from outside the permit text.** Cook County Assessor
parcel data carries units per property; joining on address or PIN as of the
date before demolition would resolve most of it. That is the real project.

### Only about a fifth of demolitions are replaced on site

| | Permits | Share |
|---|---|---|
| Rebuilt, exact address match within 5 years | 1,485 | 12% |
| Rebuilt, found only by coordinates within 40 m | ~1,190 | 10% |
| **Rebuilt, corrected estimate** | **~2,676** | **22%** |
| **Not rebuilt** | **~9,400** | **78%** |

The second row was validated, not assumed: address matching alone said 88%
were never rebuilt, so 400 of those were re-checked spatially and 11% had a
new-construction permit within 40 m. Widening the window from 5 years to 8
barely moves it, so this is not a timing artifact. Note these rates are over
the looser 12,176 population; they need recomputing against the 7,562.

**The errors are one-directional, which is why this matters.** Where a site is
rebuilt we count the replacement and never subtract what stood there — and
855 of 1,488 confirmed rebuilds are single-family houses, so a two-flat
replaced by one house is recorded as +1 when the truth is −1. Where it is not
rebuilt the loss is invisible entirely. Almost every error overstates supply.

### Where they concentrate

Confirmed-residential demolitions by ward: w16 960, w20 572, w9 523, w17 425,
w21 389, w32 351, w28 340, w24 288, w6 274, w15 272. Mostly South and West
Side disinvestment, but wards 32 and 47 ranking high is a different story —
North Side teardowns, the two-flat-to-single-family conversion.

### If picked up again

1. Tighten the classifier (`\bRES\b`, accessory-first ordering) and recompute
   the rebuild rates against the 7,562 confirmed population.
2. Join Cook County Assessor parcel data for unit counts.
3. Only then consider a net figure, and say what it excludes.

## 3. Zoning map amendments by ward

**Question.** Which wards upzone and which downzone? This is aldermanic
prerogative in data form, and it fills the gap the scorecard left without the
editorial problem.

- **Data.** `dj47-wfun` (current zoning) carries `ordinance`, `ordinance_1`
  (a date), `case_numbe` and `case_type` on each polygon.
- **First step.** Profile those fields: how many polygons carry a date, and
  whether the *prior* class is recoverable. The current snapshot stores only
  the latest class, so direction of change may need ordinance text or archived
  zoning snapshots.
- **Output.** Rezonings per ward per year, classified up or down; share of
  residential land rezoned in each direction.

## 4. Refresh routine for the Affordable Units data

`src/data/lihtc.json` is rebuilt by hand. Write down (and where possible
script) a quarterly pass that catches what each source adds:

- **City Council ordinances** — the Clerk's API
  (`api.chicityclerkelms.chicago.gov/search`) returned every 2023–26
  housing-finance ordinance; filter to mayoral filings mentioning
  multi-family funds, bonds, tax credits, TIF or affordable housing, then
  read the attachments for units and AMI.
- **Building permits** for everything in the pipeline: a permit moves a
  development out of the pipeline and into the counts.
- **IHDA** — TEFRA notices, 9% awards, annual closing and progress reports.
- **The City's funding rounds** and year-end reports (none published after
  2023 so far).
- **HUD's annual LIHTC file.**

## Also queued

- **Zoning: share of each ward zoned single-family-only vs multi-family.**
  `dj47-wfun` intersected with wards, area-weighted in EPSG:3435. RS-1/2/3 is
  43% of zoned land; RS-3 alone is 21%, and whether it counts as SFH-only is
  the definitional call that swings the result. Group Planned Developments as
  MFH-allowed (`PD 0` alone is 5% of the city — most of downtown); B and C
  districts permit residential above ground floor. Use residentially-usable
  land as the denominator. Pairs naturally with permits per year, same layout
  as the income tab.
- **ADU applications by ward.** `j4h8-ug9m` (496 applications since the
  2026-03-31 ordinance change, with ward, zoning class and status) and
  `xbwc-ntpx` (1,038 from the 2021–26 pilot, with the pilot zone). Pre- vs
  post-ordinance by ward is straightforward. *Ward opt-in status is not a
  dataset* — it is ordinance reading and would be a hand-maintained table.
- **Historic ward boundaries on the map.** The "at time of permit" view leaves
  the map unshaded because it draws current outlines. When the selected years
  fall entirely within one map era, draw that era's polygons (2003 and 2015
  maps are on the portal). A range spanning two eras still cannot be drawn.
- **`permits.json` is now ~450 KB and inlined into the bundle.** Fetch it at
  runtime like the ward geometry.
- **PAC footer link returns 404** — acitythatworks.org has no `/pac` page.
- **Confirm `citythatworks@substack.com` is a monitored inbox** before the
  "let us know" link gets traffic.
- **OG / Twitter card tags** so shared links preview properly.
- **Pages overflow the screen at phone width** (seen at 390px on Permitting
  and Affordable Units): the toggle rows and summary stats do not wrap.
- **README route table** has drifted from `src/sections.ts` three times; it
  either needs generating or removing.
