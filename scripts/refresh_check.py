#!/usr/bin/env python3
"""
What's new since the last Affordable Units refresh: a report, not an edit.

The tax-credit, City/State and ARO records behind the Affordable Units page
are reconciled by hand (scripts/import_lihtc.py, import_aro.py), so nothing
moves them on its own. This script checks each source for what has changed
since the last completed refresh and prints a Markdown checklist to work
through:

  1. Awarded developments that may have pulled a building permit, from the
     City's permit data near each one (a permit moves its units into the
     counts). 1b. The same for developments on file as stalled or proposed,
     plus any City Council ordinance naming them: the check-in that decides
     whether one goes back to awarded.
  2. City Council housing-finance ordinances introduced since the last
     refresh, from the City Clerk's API; ones already cited in the importer
     are marked.
  3. Whether HUD has posted a new annual LIHTC file.
  4. New documents on the IHDA, City and CHA pages the reconciliation draws
     on, and whether the City's ARO map data has been updated.
  5. Newsletter posts tagged Housing that the site doesn't list yet.

Only completed refreshes move the baseline: run with --mark-done once the
report has been worked through, and the date, HUD file and page snapshots in
scripts/refresh_state.json are updated (commit that file). A report nobody
acted on never hides anything from the next one. Each check fails on its own,
so a site that refuses the request is reported, not fatal.

    python3 scripts/refresh_check.py               # report to stdout
    python3 scripts/refresh_check.py --out r.md    # report to a file
    python3 scripts/refresh_check.py --mark-done   # record a finished refresh
"""
import argparse
import datetime
import html
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "scripts", "refresh_state.json")
LIHTC = os.path.join(ROOT, "src", "data", "lihtc.json")
IMPORTER = os.path.join(ROOT, "scripts", "import_lihtc.py")

PERMITS_API = "https://data.cityofchicago.org/resource/ydr8-5enu.json"
CLERK_API = "https://api.chicityclerkelms.chicago.gov/search"
HUD_URL = "https://www.huduser.gov/lihtc/lihtcpub.zip"
#: The permit data posts with a lag, so each permit search reaches back this
#: far before the last refresh rather than starting on its date.
PERMIT_OVERLAP_DAYS = 45
#: Metres around an awarded development's point to look for its permit. A
#: permit counts as a candidate if it is on one of the development's own
#: streets, or within NEAR_M of its point; the rest are next-door buildings.
PERMIT_RADIUS_M = 100
NEAR_M = 40
#: Below this a renovation permit is upkeep, not the rehab that starts a deal.
MIN_RENOVATION_COST = 250_000
#: Searches that between them surfaced every housing-finance ordinance in
#: the 2023-26 reconciliation.
CLERK_TERMS = ['"multi-family"', '"affordable housing"', '"low-income housing"',
               '"housing revenue bonds"', '"tax increment"']
#: Pages whose linked documents the reconciliation draws on.
WATCHED_PAGES = {
    "IHDA tax credit awards and applications": "https://www.ihda.org/developers/tax-credits/low-income-tax-credit/",
    "IHDA TEFRA bond hearings": "https://www.ihda.org/legal/tefra-hearings/",
    "IHDA planning reports (ACHP, Governor's reports)": "https://www.ihda.org/about-ihda/state-housing-planning-reports/",
    # The City's quarterly progress reports aren't linked from any page (none
    # published after 2023 as of 2026-10); these catch the annual report,
    # program database and funding-round plans.
    "City DOH data and annual reports": "https://www.chicago.gov/city/en/depts/doh/provdrs/goals_reports/svcs/data.html",
    "City DOH plans and funding rounds": "https://www.chicago.gov/city/en/depts/doh/provdrs/goals_reports/svcs/plans.html",
    "CHA Moving to Work plans and reports": "https://www.thecha.org/transparency-action-cha-data-impact-hub/policies/moving-work-agreement-annual-plan-and-reports",
    "CHA Moving to Work archive": "https://www.thecha.org/transparency-action-cha-data-impact-hub/policies/moving-work-agreement-annual-plan-and-reports/moving-work-reports-archive",
}
DOC = re.compile(r'href="([^"]+\.(?:pdf|xlsx?|docx?|csv|zip))"', re.I)

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # the system store works on most machines
    CTX = None


def get(url, params=None, method="GET", raw=False):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0 (chicago-housing-tracker refresh check)"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        if method == "HEAD":
            return dict(r.headers)
        body = r.read()
        return body if raw else json.loads(body)


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"lastRefresh": None, "hud": {}, "pages": {}, "aro": {}}


# ---- 1. awarded developments -------------------------------------------------

def metres(lat1, lon1, lat2, lon2):
    """Distance on the ground, close enough at city scale."""
    import math
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)

def check_permits(since, statuses=("awarded",)):
    """Permits near each development with one of `statuses`, issued on or after `since`."""
    data = json.load(open(LIHTC))
    awarded = [p for p in data["projects"] + data["city"] if p["s"] in statuses and p.get("ll")]
    lines = []
    for p in sorted(awarded, key=lambda p: p["n"]):
        lat, lon = p["ll"]
        rows = get(PERMITS_API, {
            "$select": ("permit_,issue_date,permit_type,reported_cost,work_description,"
                        "street_number,street_direction,street_name,latitude,longitude"),
            "$where": (f"within_circle(location, {lat}, {lon}, {PERMIT_RADIUS_M}) AND issue_date >= '{since}' "
                       "AND permit_type in('PERMIT - NEW CONSTRUCTION', 'PERMIT - RENOVATION/ALTERATION')"),
            "$order": "issue_date",
            "$limit": 50,
        })
        hits = []
        for r in rows:
            cost = float(r.get("reported_cost") or 0)
            if r["permit_type"] != "PERMIT - NEW CONSTRUCTION" and cost < MIN_RENOVATION_COST:
                continue
            street = (r.get("street_name") or "").strip().upper()
            same_street = bool(street) and re.search(rf"\b{re.escape(street)}\b", p["a"].upper())
            try:
                dist = metres(lat, lon, float(r["latitude"]), float(r["longitude"]))
            except (KeyError, TypeError, ValueError):
                dist = None
            if not same_street and (dist is None or dist > NEAR_M):
                continue
            addr = " ".join(str(r.get(k) or "").strip() for k in ("street_number", "street_direction", "street_name"))
            desc = re.sub(r"\s+", " ", (r.get("work_description") or "")).strip()
            hits.append(f"  - permit {r['permit_']}, {r['issue_date'][:10]}, {r['permit_type'].replace('PERMIT - ', '').lower()}, "
                        f"${cost:,.0f}, {addr.title()}: {desc[:140]}")
        if hits:
            tag = "" if p["s"] == "awarded" else f", {p['s']}"
            lines.append(f"- **{p['n']}** ({p['a']}, ward {p['w']}, `{p['id']}`{tag})")
            lines += hits
    head = (f"{len(awarded)} {' or '.join(statuses)} developments searched for permits issued since {since} on "
            f"their own street or within {NEAR_M} m (new construction, or renovation of ${MIN_RENOVATION_COST:,}+).")
    if not lines:
        return head + " None found.", 0
    return head + " Check each against the development; a match moves it into the counts:\n\n" + "\n".join(lines), \
        sum(1 for l in lines if l.startswith("- "))


def check_dormant(since):
    """Stalled and proposed developments: new permits, and ordinances naming them."""
    data = json.load(open(LIHTC))
    dormant = sorted((p for p in data["projects"] + data["city"] if p["s"] in ("stalled", "proposed")),
                     key=lambda p: (p["s"], p["n"]))
    if not dormant:
        return "No developments on file as stalled or proposed.", 0
    listing = "\n".join(f"- {p['n']} ({p['s']}, {p['li']} units, ward {p['w']})" for p in dormant)
    permits, n_permits = check_permits(since, ("stalled", "proposed"))
    found = []
    for p in dormant:
        name = re.sub(r"\s*\(.*?\)", "", p["n"]).strip()
        d = get(CLERK_API, {"search": f'"{name}"', "filter": f"introductionDate ge {since}T00:00:00Z",
                            "top": "50", "sort": "introductionDate desc"})
        for m in d.get("data", []):
            if m.get("type") == "Ordinance":
                found.append(f"- {p['n']}: {(m.get('introductionDate') or '')[:10]} {m['recordNumber']}: "
                             f"{(m.get('title') or '').strip()}")
    text = (f"{len(dormant)} developments on file but not counted as coming. Check in on each; if one has "
            "moved (award, permit, Council action), change its status in `scripts/import_lihtc.py`:\n\n"
            + listing + "\n\n" + permits + "\n\nCity Council ordinances naming them since "
            + since + (":\n\n" + "\n".join(found) if found else ": none."))
    return text, n_permits + len(found)


# ---- 2. City Council ordinances ---------------------------------------------

def check_ordinances(since):
    cited = set(re.findall(r"\bS?O20\d{2}-\d{7}\b", open(IMPORTER).read()))
    found = {}
    for term in CLERK_TERMS:
        d = get(CLERK_API, {"search": term, "filter": f"introductionDate ge {since}T00:00:00Z",
                            "top": "500", "sort": "introductionDate desc"})
        for m in d.get("data", []):
            if m.get("type") == "Ordinance":
                found[m["recordNumber"]] = m
    if not found:
        return f"No housing-finance ordinances introduced since {since}.", 0
    lines = []
    for m in sorted(found.values(), key=lambda m: m.get("introductionDate") or "", reverse=True):
        mark = " *(already cited in the importer)*" if m["recordNumber"] in cited else ""
        lines.append(f"- {(m.get('introductionDate') or '')[:10]} [{m['recordNumber']}]"
                     f"(https://chicityclerkelms.chicago.gov/Matter/?matterId={m['matterId']}): "
                     f"{(m.get('title') or '').strip()} ({m.get('status')}){mark}")
    new = sum(1 for m in found.values() if m["recordNumber"] not in cited)
    return (f"{len(found)} ordinances introduced since {since} match a housing-finance search "
            f"({new} not cited in the importer). Most TIF items are not housing; read the "
            "attachments of the housing ones for units and AMI:\n\n" + "\n".join(lines)), new


# ---- 3. HUD's annual file ---------------------------------------------------

def check_hud(state):
    h = get(HUD_URL, method="HEAD")
    now = {"lastModified": h.get("Last-Modified"), "length": h.get("Content-Length")}
    before = state.get("hud") or {}
    if not before:
        return f"HUD's file: {now['lastModified']} (baseline, recorded on --mark-done).", 0, now
    if now != before:
        return (f"**HUD has posted a new LIHTC file** ({now['lastModified']}; was {before.get('lastModified')}). "
                "Delete `.lihtc_cache.json` and rerun the importer."), 1, now
    return f"HUD's file is unchanged ({now['lastModified']}).", 0, now


# ---- 4. watched pages and the ARO data --------------------------------------

def page_docs(url):
    body = get(url, raw=True).decode("utf-8", "ignore")
    return sorted({urllib.parse.urljoin(url, html.unescape(h)) for h in DOC.findall(body)})


def check_pages(state):
    lines, snapshot, new_total = [], {}, 0
    for label, url in WATCHED_PAGES.items():
        try:
            docs = page_docs(url)
        except Exception as e:  # noqa: BLE001 - one page failing shouldn't stop the rest
            lines.append(f"- {label}: couldn't reach it from here ({e.__class__.__name__}); check by hand: {url}")
            snapshot[url] = (state.get("pages") or {}).get(url, [])
            continue
        snapshot[url] = docs
        before = (state.get("pages") or {}).get(url)
        if before is None:
            lines.append(f"- {label}: {len(docs)} documents (baseline, recorded on --mark-done)")
            continue
        added = [d for d in docs if d not in set(before)]
        new_total += len(added)
        lines.append(f"- {label}: {len(added)} new" + ("" if not added else ":"))
        lines += [f"  - {d}" for d in added]
    return "\n".join(lines), new_total, snapshot


def check_aro(state):
    import import_aro
    info = get(import_aro.ARO_SERVICE, {"f": "json"})
    edited = (info.get("editingInfo") or {}).get("lastEditDate")
    page = get(import_aro.ARO_MAP_PAGE, raw=True).decode("utf-8", "ignore")
    services = sorted(set(re.findall(r"https://[^\"' ]*arcgis[^\"' ]*", page)))
    now = {"lastEditDate": edited, "service": import_aro.ARO_SERVICE, "mapPageLinks": services}
    before = state.get("aro") or {}
    if not before:
        return "ARO map data: baseline, recorded on --mark-done.", 0, now
    changed = []
    if edited != before.get("lastEditDate"):
        changed.append("the service's data was edited since the last refresh")
    if services != before.get("mapPageLinks"):
        changed.append("the City's ARO map page links to different ArcGIS content (a new service?)")
    if changed:
        return ("**ARO data may have changed:** " + "; ".join(changed) +
                ". Rerun `python3 scripts/import_aro.py` (point ARO_SERVICE at any new service first)."), 1, now
    return "ARO map data unchanged.", 0, now


# ---- 5. newsletter ------------------------------------------------------------

def check_posts():
    import import_posts
    tagged = {p["slug"] for p in import_posts.archive()
              if "Housing" in [t.get("name") for t in p.get("postTags") or []] and p.get("audience") == "everyone"}
    listed = {p["slug"] for p in json.load(open(os.path.join(ROOT, import_posts.OUT)))}
    add, drop = sorted(tagged - listed), sorted(listed - tagged)
    if not add and not drop:
        return "Newsletter list is current.", 0
    return (f"Newsletter list out of date: {len(add)} to add, {len(drop)} to drop "
            f"({', '.join(add + drop)}). Run `python3 scripts/import_posts.py` and commit."), 1


# ------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="write the report here as well as stdout")
    ap.add_argument("--mark-done", action="store_true",
                    help="record today as the last completed refresh, with today's HUD, page and ARO snapshots")
    args = ap.parse_args()

    state = load_state()
    today = datetime.date.today().isoformat()
    last = state.get("lastRefresh") or "2026-10-21"
    permits_since = (datetime.date.fromisoformat(last) - datetime.timedelta(days=PERMIT_OVERLAP_DAYS)).isoformat()

    sections, flags, snapshots = [], 0, {}

    def run(title, fn):
        nonlocal flags
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001 - report and carry on
            sections.append(f"## {title}\n\nCouldn't run from here ({e.__class__.__name__}: {e}). Run locally.")
            return None
        text, n = res[0], res[1]
        flags += n
        sections.append(f"## {title}\n\n{text}")
        return res

    run("1. Awarded developments: new permits?", lambda: check_permits(permits_since))
    run("1b. Stalled and proposed developments: check in", lambda: check_dormant(permits_since))
    run("2. City Council housing-finance ordinances", lambda: check_ordinances(last))
    r = run("3. HUD's annual LIHTC file", lambda: check_hud(state))
    if r:
        snapshots["hud"] = r[2]
    r = run("4. Source documents", lambda: check_pages(state))
    if r:
        snapshots["pages"] = r[2]
    r = run("4b. City ARO map data", lambda: check_aro(state))
    if r:
        snapshots["aro"] = r[2]
    run("5. Newsletter coverage", check_posts)

    report = (f"# Affordable Units refresh check, {today}\n\n"
              f"Changes since the last completed refresh ({last}). {flags} item(s) to look at. "
              "Work through them with the open follow-ups in the backlog, rerun the importers, "
              "then `python3 scripts/refresh_check.py --mark-done` and commit "
              "`scripts/refresh_state.json`.\n\n" + "\n\n".join(sections) + "\n")
    print(report)
    if args.out:
        open(args.out, "w").write(report)

    if args.mark_done:
        state["lastRefresh"] = today
        state.update(snapshots)
        with open(STATE, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
            f.write("\n")
        print(f"Recorded {today} as the last completed refresh in {os.path.relpath(STATE, ROOT)}.", file=sys.stderr)


if __name__ == "__main__":
    main()
