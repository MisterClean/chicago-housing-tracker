#!/usr/bin/env python3
"""
LIHTC (Low-Income Housing Tax Credit) developments in Chicago, dated by
building permit.

Source: HUD's LIHTC property database (LIHTCPUB), the downloadable file rather
than HUD's ArcGIS layer, which stops at 2020. The download is refreshed each
spring and covers projects placed in service through roughly two years before.

HUD dates a project by the year it was placed in service (opened). Everything
else on the site is dated by permit, so each project is also matched to its
building permit, by address only, the same way the ARO importer does it. Both
years are kept; the permit year is the default.

Each project is also classed as new units or preserved units. A large share of
recent LIHTC money recapitalises existing affordable buildings, and HUD's own
construction-type code is often blank or "both", so the matched permit decides
where there is one: a new-construction permit, or a conversion of a building
that was not housing before, means new units; a renovation of existing housing
means preserved units. Unmatched projects fall back to HUD's code.

HUD's file lags and misses projects, so it is reconciled against the City's
funding-round awards and year-end housing reports and IHDA's award lists,
closing reports and bond hearing notices; those additions live in PIPELINE
below, each with its sources. Developments awarded credits but not yet
permitted are kept apart from the yearly series.

Writes src/data/lihtc.json for the Affordable Units page, and optionally a
review file with every field. Run by hand, not by the monthly refresh: the
reconciliation is hand-maintained.

Usage:
    python3 scripts/import_lihtc.py [review.json]
"""
import collections
import io
import json
import math
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_aro as aro  # noqa: E402  address and permit helpers are shared

HUD_URL = "https://www.huduser.gov/lihtc/lihtcpub.zip"
CACHE = ".lihtc_cache.json"
EXTRA_CACHE = ".lihtc_permits_extra.json"
PERMITS_API = "https://data.cityofchicago.org/resource/ydr8-5enu.json"

#: The shared permit cache keeps renovation permits only when their text
#: mentions units, dwellings, apartments or "residential". Preservation rehabs
#: are often worded otherwise ("SRO building", "life safety high rise"), so
#: large renovation and easy permits near each project are fetched as well.
EXTRA_RADIUS_M = 80
EXTRA_MIN_COST = 500_000
PERMIT_CACHE = ".permits_cache.json"

#: HUD's TYPE field
HUD_TYPE = {"1": "new", "2": "preserved", "3": "both", "4": "existing"}

#: Permits run from 2010. A project that opened before 2011 was permitted
#: before the record starts, so it is not looked for.
FIRST_PIS = 2011

#: A permit counts if it is issued between this many years before the project
#: opened and one year after (late permits are fit-outs and revisions).
YEARS_BEFORE_PIS = 6

#: The permit must state at least this share of the project's total units, so
#: a two-flat next door cannot stand in for a 90-unit building. Phased
#: projects permit several buildings, hence well under 1.
MIN_SHARE_OF_UNITS = 0.5

#: At the project's own address the address is the evidence, and phased
#: projects split their units across buildings ("33 units" of Woodlawn Center
#: South's 67), so the size test there is looser.
MIN_SHARE_AT_ADDRESS = 0.25

#: Preservation rehabs rarely restate the building's unit count, so at the
#: project's own address a large renovation is accepted without one. LIHTC
#: recapitalisations run to millions; porch and roof jobs do not.
REHAB_MIN_COST = 1_000_000

#: A renovation permit creates new units only when it turns a building that
#: was not housing into housing: "re-use from mercantile and storage", "convert
#: a warehouse to 27 units", "monastery to serve as affordable senior housing".
CONVERTS = re.compile(r"CONVER|RE-?\s?USE|ADAPTIVE|TO SERVE AS")
DECONVERTS = re.compile(r"DE-?\s?CONVER")
NOT_HOUSING = re.compile(
    r"\b(OFFICE|WAREHOUSE|FACTORY|SCHOOL|CHURCH|HOSPITAL|COMMERCIAL|INDUSTRIAL|"
    r"RETAIL|MERCANTILE|BANK|THEAT(?:ER|RE)|CONVENT|MONASTERY|RECTORY|"
    r"MANUFACTURING|STORAGE|FUNERAL|LOFT BUILDING)\b")
#: ...but not when the building already held homes ("existing 145 apartment
#: building", "de-convert 538 SRO units to 409").
WAS_HOUSING = re.compile(
    r"EXISTING[^.]{0,40}?\b(?:\d+\s*(?:UNITS?|APARTMENTS?|D\.?U\.?|SRO)|"
    r"APARTMENT|RESIDENTIAL|SENIOR|ELDERLY|SRO|DWELLING)")

#: A religious house had residents but no tenancies, so turning one into
#: apartments creates new homes even though people lived there.
RELIGIOUS = re.compile(r"\b(MONASTERY|CONVENT|RECTORY|FRIARY|ABBEY)\b")

#: HUD records that repeat another one. Dropped.
DUPLICATES = {
    "ILB0000X068": "Repeats ILB19980005, Senior Suites of Ravenswood (2800 W "
                   "Montrose, 80 units, opened 1998), with no year.",
    "ILB0000X084": "Repeats ILA00000113, Lawndale Plaza, under a misspelt "
                   "address ('3158 WEST ROOSEVLT RD') with the same 198 units.",
    "ILB20136005": "Repeats ILB20130100, Senior Suites at Midway Village: same "
                   "address, units, allocation and year.",
    # Same deal reported twice: the twin carries the same deal number, or
    # HUD's own note links the two. Checked 2026-10.
    "ILB00000003": "Repeats ILA19930255, Grand Boulevard Towers (4257 S King, "
                   "30 units, 1993), whose note reads 'AKA GRAND BLVD "
                   "RENAISSANCE 66593'; 66593 is this record's deal number.",
    "ILB0000X071": "Repeats ILB20089005, Spaulding + Trumbull (25 units, 2008); "
                   "the only rehab permits are the 2007-09 ones of that deal.",
    "ILA00000053": "Repeats ILB20100805, Englewood (901 W 63rd, 99 units, new, "
                   "opened 2010): one building, reported by both agencies.",
    "ILB0000X083": "Repeats ILB19980010, Evergreen Tower II (1343 N Cleveland, "
                   "101 units, 1998); its deal number T.C.97-009 is the 1997 "
                   "allocation.",
    "ILB0000X028": "Repeats ILA19930065, Central Pine Place II (238 N Pine, "
                   "1993); deal numbers 58992 and 589-92.",
    "ILB20114001": "Repeats ILA20110933, West End Phase II (2425 W Adams, 112 "
                   "units, identical bedroom mix): the same project reported by "
                   "the City as well as IHDA.",
    "ILB20132001": "Repeats ILB20130005, Borinquen Bella: identical bedroom mix "
                   "(3/19/25), units, allocation and opening year; one "
                   "scattered-site project listed at two of its addresses.",
}

#: Recorded by HUD as Chicago but elsewhere. Dropped.
NOT_IN_CHICAGO = {
    "ILA20140004": "Congress Parkway: Pedcor's 60-unit complex at 383 Ambassador "
                   "Circle, Crystal Lake (16/24/20 one- to three-bedroom, as in "
                   "HUD). HUD's '261 E Congress Pkwy, Chicago' does not exist. "
                   "Checked 2026-10.",
}

#: A second LIHTC deal on a building that already had one: a new deal number
#: and a bond or major rehab permit years after the original. Kept as a
#: preservation event in its own year, linked to the original so the stock
#: count can hold the building once. Checked 2026-10.
RESYNDICATIONS = {
    "ILB0000X070": "ILB20000802",  # Southland Village; St. Edmund's Village 2000
    "ILB0000X079": "ILB20000801",  # Van Buren Park, 2000
    "ILB0000X073": "ILA19960285",  # 150 W Maple; Maple Point 1996
    "ILB0000X076": "ILB19909003",  # Marshall Hotel, 1990
    "ILB0000X050": "ILB19920025",  # Miriam Apartments; Friendly Towers 1992
    "ILA00000114": "ILB19880075",  # Parkview Towers, 1988
    "ILB0000X037": "ILA20010030",  # Evergreen Terrace; Evergreen Sedgwick 2001
    "ILA20210004": "ILA20030900",  # Northpoint; Northpoint Preservation 2003
}

#: Projects checked by hand. "category" overrides the computed one; "units"
#: fills a low-income count HUD leaves blank. Each note says what was found.
HAND_REVIEWED = {
    "ILB0000X056": {"units": 84, "units_basis": 'All 84 under a Section 8 PBRA contract plus LIHTC (POAH); City DOH 2015: 84 at or under 80% AMI.'},
    "ILA20190008": {"units": 122, "units_basis": 'All 122 rent- and income-restricted: 90 at or under 50% AMI, 32 at 61-80% (Hyde Park Herald, City DOH 2018).'},
    "ILB20163001": {"units": 69, "units_basis": '61 at 30-60% AMI plus 8 at or under 80%; 1 manager (City, City DOH 2014).'},
    "ILB0000X021": {"units": 48, "units_basis": "38 at or under 60% AMI plus 10 at or under 30%, which HUD's count leaves out (CHA, City DOH 2015)."},
    "ILA20200005": {"units": 106, "units_basis": '64 + 42 supportive units, all restricted (POAH, City DOH 2018).'},
    "ILB0000X026": { "units": 449, "units_basis": '449 CHA senior apartments (City, City DOH 2018).',"category": "preserved", "cha": True, "note": (
        "Caroline Hedger Apartments, a CHA senior high-rise built 1963 (449 "
        "apartments by CHA's count). $45M rehab with $2M of city LIHTC, "
        "announced 2018. Also CHA. Dated by the $21M comprehensive-renovation "
        "permit of 2015-01-29, which covers the same scope; the LIHTC financing "
        "came in 2018. Checked 2026-10.")},
    "ILB0000X079": {"category": "preserved", "units": 299,
        "units_basis": "HUD's 300 low-income here is its fill-in for a missing count; its 2000 record for the same building reports 299 of 300.",
        "note": (
        "Van Buren Park Apartments: 300 units, 299 low-income, 4% LIHTC with "
        "bonds and project-based Section 8. Existing building. Checked 2026-10.")},
    "ILB0000X053": {"category": "preserved", "note": (
        "Morningside North: senior building from 1981, 256 Section 8 units. "
        "Checked 2026-10.")},
    "ILB0000X070": {"category": "preserved", "note": (
        "Southland Village: family high-rise from 1970, all 230 units project-"
        "based Section 8. Checked 2026-10.")},
    "ILB0000X029": {"category": "preserved", "note": (
        "Clarendon Court: 152 units, LIHTC plus project-based Section 8. "
        "Checked 2026-10.")},
    "ILB0000X025": {"category": "preserved", "units": 122, "note": (
        "Bethel Terrace: 123-unit senior building, 122 Section 8; $4M "
        "renovation by Evergreen. Checked 2026-10.")},
    "ILB0000X073": {"category": "preserved", "units": 343, "note": (
        "Standard Illinois Portfolio A, 150 W Maple: existing 20-story senior "
        "building. HUD has no unit count; 343 is from its 2018 rehab permit. "
        "Checked 2026-10.")},
    "ILB0000X074": {"category": "preserved", "units": 134, "note": (
        "Standard Illinois Portfolio C, 1734 N Paulina (Walsh Park): existing "
        "11-story senior building. HUD has no unit count; 134 is from its 2018 "
        "rehab permit. Checked 2026-10.")},
    # ---- Group C: no year in HUD. Researched 2026-10. ----
    "ILB0000X047": {"category": "preserved",
        "dated": ("permit", "2015-08-24", "100608380"), "note": (
        "Marshall Field Garden Apartments, 628 units on two Old Town blocks. "
        "Bought by Related Midwest in 2014; $175M rehab with IHDA LIHTC and "
        "historic credits from July 2015. Its buildings at 1418 and 1438 N "
        "Sedgwick and 413 W Blackhawk all have rehab permits of 2015-08-24.")},
    "ILB0000X052": {"category": "new", "dated": ("pre2010", "2001", None), "note": (
        "Montclare Senior Residences of Galewood: 335-unit senior building "
        "built 2001. Permitted before the permit record starts.")},
    "ILB0000X044": {"category": "preserved", "dated": ("permit", "2015-08-19", "100584042"), "note": (
        "Lake Village East (Ansonia): $25M substantial rehab of a 200-unit "
        "high-rise and 18 walk-up units with 4% LIHTC, IHDA and AFL-CIO HIT "
        "bonds (HIT financing June 2015). Every unit is restricted: about half "
        "Section 8, 20% LIHTC, 30% at or under 60% AMI. Dated by the $6.0M "
        "permit of 2015-08-19 (kitchens and baths in 200 units, lobby); a law "
        "firm's 2021-02-05 closing date looks wrong or is a later refinancing. "
        "Rechecked 2026-10.")},
    "ILB0000X022": {"category": "preserved",
        "dated": ("permit", "2015-01-16", None), "note": (
        "Academy Square: existing LIHTC and Section 8 property. Dated by its "
        "$1.4M 2015 repair permit (roof, windows, plumbing), the only large "
        "permit on record; medium confidence.")},
    "ILB0000X080": {"category": "preserved", "cha": True, "mixed_income": True, "units": 145,
        "units_basis": "City year-end appendix (Plan for Transformation commitments): 95 CHA, 50 affordable, 55 market of 200.",
        "dated": ("permit", "2016-07-01", "100636897"), "note": (
        "Villages of Westhaven (Henry Horner redevelopment): the 2016 LIHTC deal "
        "(City closing 2016-08-05) rehabbed existing two-storey buildings on W "
        "Randolph, converting some townhouses to add units; rehab permits of "
        "2016-06-24 and 2016-07-01 (2100-2153 W Randolph). Corrected 2026-10 "
        "from a pre-2010 date taken from the original 2006 buildings.")},
    "ILA00000057": {"category": "preserved", "dated": ("pre2010", "2008", None), "note": (
        "Hollywood House: 198-unit building renovated under a $9M permit of "
        "2008-11-21, before the permit record starts.")},
    "ILB0000X035": {"category": "preserved", "units": 150,
        "units_basis": "150 Section 8 units (Hispanic Housing; HUD's Section 8 contract); HUD's LIHTC 156 is its fill-in from the total.",
        "dated": ("permit", "2016-04-21", "100646224"), "note": (
        "Damen Court: 150 Section 8 units in three-storey walk-ups at 220-260 S "
        "Damen (HUD lists 2027 W Adams). Rehab permits on the complex of "
        "2016-04-21.")},
    "ILB0000X048": {"category": "preserved",
        "dated": ("permit", "2014-12-05", None), "note": (
        "Martin Luther King Jr. Plaza: 138 Section 8 family units built 1972. "
        "Rehab permits for its buildings on W Madison of December 2014.")},
    "ILA00000114": {"category": "preserved",
        "dated": ("permit", "2011-07-14", None), "note": (
        "Parkview Towers: existing 18-storey building; $3.5M fixture and "
        "kitchen replacement permit of 2011-07-14.")},
    "ILB0000X037": {"category": "preserved",
        "dated": ("permit", "2015-12-22", "100615280"), "note": (
        "Evergreen Terrace: 84 units built 1983. Bought by Vitus Group in 2015 "
        "with work from September; the 2015-12-22 permit replaces all kitchens, "
        "baths, windows and the roof. IHDA bond issued 2015-10-16.")},
    "ILA00000064": {"category": "preserved", "dated": ("pre2010", "2007", None), "note": (
        "Lorington Apartments preservation: rehab permit of 2007-05-16.")},
    "ILB0000X023": {"category": "new", "basis": "rehab of vacant buildings", "dated": ("estimate", "2015", None), "note": (
        "Access Housing (IFF): 54 homes for people with disabilities across 25 "
        "sites, new construction and substantial rehab, completed late 2016. "
        "Scattered, so no single permit; year estimated.")},
    "ILB0000X045": {"category": "new",
        "dated": ("permit", "2014-02-25", None), "note": (
        "Legends South C-3: new scattered buildings around 43rd and Michigan, "
        "permitted February 2014.")},
    "ILB0000X082": {"category": "new",
        "dated": ("permit", "2015-05-01", None), "note": (
        "Westside Village Phase VI: new 8-unit buildings on W Arthington, "
        "permitted May 2015.")},
    "ILB0000X067": {"category": "new", "basis": "rehab of vacant buildings", "dated": ("estimate", "2015", None), "note": (
        "Reclaiming Southwest Chicago (SWOP and Brinshore): rehab of vacant, "
        "foreclosed buildings in Chicago Lawn. IHDA approved the credits in "
        "its 2014 Round I (30 units at preliminary stage); the owner LLC's "
        "permits run December 2015 to February 2016 and 43 buildings were done "
        "by the end of 2016 (LISC). Scattered; year estimated as 2015. Later "
        "phases (II, III) used Illinois donation credits, not LIHTC.")},
    "ILA00000087": {"category": "new", "dated": ("pre2010", "2007", "100160632"), "note": (
        "Sankofa House: new 5-storey, 58-unit building at 4041 W Roosevelt "
        "(HUD lists 3824 W 16th) for kinship families, permitted 2007-01-31, "
        "opened 2008.")},
    "ILB0000X040": {"dated": ("pre2010", "1993", None), "note": (
        "Buena Vista Apartments, 3038-40 W North (Hispanic Housing), in "
        "service since 1993. Listings show 36 units against HUD's 50; HUD's "
        "figure is used. Only small repair permits on record.")},
    "ILB0000X033": {"category": "preserved", "units": 124,
        "dated": ("estimate", "2016", None), "note": (
        "Crossroads of East Ravenswood, 4206 N Ashland: LIHTC and Section 8. "
        "HUD has no unit count; 124 is from rental listings. IHDA bond of "
        "$19.5M issued 2016-06-24; no rehab permit on record, so the year is "
        "the bond closing.")},
    # ---- Group B: opened 2013 or later, unmatched. Researched 2026-10. ----
    "ILA20210004": {"category": "preserved", "dated": ("permit", "2020-06-25", "100859458"), "note": (
        "Northpoint: second LIHTC deal on Northpoint Preservation (2003). Rehab "
        "permits of 2020-06-25/26 at 7705 and 7717 N Paulina and 1700 W Jonquil.")},
    "ILA20140012": {"category": "preserved", "units": 100,
        "units_basis": "100 units, 96 one-bed and 4 two-bed (UPholdings); HUD's own bedroom counts sum to 100 against its 102.",
        "dated": ("permit", "2013-10-25", "100485315"), "note": (
        "Evergreen Tower I: 11-storey senior building, rehab completed 2014; "
        "permit filed at 1333 N Cleveland (HUD lists 1353).")},
    "ILA20130002": {"category": "preserved", "dated": ("permit", "2012-08-23", "100372615"), "note": (
        "Woodlawn Six: 100 Section 8 units in six buildings at 6201-6245 S "
        "Kimbark. City closed 2012-08-17, IHDA bond 2012-08-16, rehab permits "
        "2012-08-23. HUD has no address.")},
    "ILA20230010": {"category": "preserved", "dated": ("permit", "2022-05-27", "100973827"), "note": (
        "Jackson Manor: scattered-site rehab; IHDA bond 2022-07-20; $1.4M "
        "kitchens-and-baths permit at 3445 W Madison.")},
    "ILB20163002": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2015-05-05", "100563604"), "note": (
        "City Gardens: seven new buildings on S Maplewood and W Jackson, all "
        "permitted 2015-05-05, on the former Rockwell Gardens CHA site. 76 units: "
        "25 CHA public housing, 30 affordable, 21 market (CHA FY2016 MTW report).")},
    "ILB20162001": {"category": "new", "cha": True, "dated": ("permit", "2014-11-13", "100570292"), "note": (
        "Casa Queretaro: '4 story 45 unit affordable housing' at 1614 S Damen, "
        "the corner of HUD's 2012 W 17th. 15 CHA public housing and 30 affordable "
        "units (CHA FY2016 MTW report).")},
    "ILB20180009": {"category": "new", "dated": ("permit", "2017-03-30", "100676177"), "note": (
        "Tierra Linda: scattered new three-flats; permit at HUD's address.")},
    "ILA20130001": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2012-10-19", "100463394"), "note": (
        "Oakwood Shores 2D (Ida B. Wells redevelopment): new buildings at "
        "615-627 E 38th Pl and 630/636 E 38th St, permitted 2012-10-19.")},
    "ILB20195003": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2018-03-08", "100750232"), "note": (
        "Northtown Apartments: the CHA senior housing and library at 6800 N "
        "Western (HUD lists 2410 W Pratt): 44 senior units, 30 CHA and 14 "
        "affordable. Foundation permit 2018-03-08.")},
    "ILA20180001": {"category": "preserved", "dated": ("permit", "2017-07-28", "100699393"), "note": (
        "Anathoth Gardens: $800k interior, exterior and site rehab of the "
        "existing building at 34 N Keeler.")},
    "ILA20220047": {"category": "new", "dated": ("permit", "2020-12-15", "100891583"), "note": (
        "Schiller Place: three new 16-unit buildings at 711/731/749 W Schiller, "
        "permitted 2020-12-15, opened 2022; 40 of 48 units affordable. CHA "
        "vouchers only, not CHA housing.")},
    "ILB20222001": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2020-09-10", "100858422"), "note": (
        "508 Pershing at Oakwood Shores: new 4-storey building at 508 E "
        "Pershing (HUD lists 534).")},
    "ILB20147016": { "units": 79, "units_basis": 'CHA project-based vouchers on all 79 units (CHA, City); City DOH 2013 shows 75 at or under 80% AMI.',"category": "new", "basis": "conversion of a police station",
        "dated": ("permit", "2013-04-10", "100484564"), "note": (
        "Town Hall Apartments: the former Town Hall police station converted "
        "and extended for affordable seniors.")},
    "ILB20132003": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2012-06-28", "100447410"), "note": (
        "Lakefront Phase II (Lake Park Crescent): 3-unit building at 4130 S "
        "Oakenwald.")},
    "ILB20132006": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2012-06-28", "100447404"), "note": (
        "Lakefront Phase II (Lake Park Crescent): building at 4143 S Lake Park.")},
    "ILB20130005": {"category": "preserved", "dated": ("permit", "2010-11-18", "100317366"), "note": (
        "Borinquen Bella: scattered-site rehab (1414 and 1451 N Washtenaw among "
        "others); HUD notes earlier listings from 1988 and 1991. Rehab permits "
        "2010-11-18.")},
    "ILB20177090": {"category": "new", "dated": ("estimate", "2015", None), "note": (
        "Park Place Apartments, scattered (3630 W 51st is the office). Likely "
        "townhomes built for sale in 2006-07 plus new 9- and 3-unit buildings "
        "permitted late 2015; not confirmed. Year estimated, low confidence.")},
    "ILB20180005": { "units": 72, "units_basis": '72 units, all at or under 60% AMI (Bickerdike, City DOH 2015).',"category": "new", "dated": ("permit", "2015-12-02", "100613663"), "note": (
        "Nelson Mandela Apartments (Bickerdike): 72 new units in 13 buildings "
        "on W Franklin, N Troy, N Sawyer, W Ohio and N Spaulding, permitted "
        "2015-12-02 to 2016-01-28 (eleven 6-flats, two 3-flats); completed "
        "2017. HUD lists Bickerdike's office address.")},
    "ILB20177003": { "units_basis": "Woodlawn Roll-Up: 121 Section 8, 56 affordable, 19 market (City announcement, May 2017); the City's year-end table says 145.","category": "preserved", "units": 177,
        "dated": ("permit", "2017-07-02", "100711841"), "note": (
        "The Washington at Woodlawn Park (POAH) is one building of the 'Woodlawn "
        "Roll-Up': 16 vintage Woodlawn buildings, bought vacant or distressed "
        "after the crash and rehabbed with NSP funds, combined into one 2017 4% "
        "deal (City DOH 2017: 196 units, 16 scattered sites). HUD lists only "
        "27 units; the deal's 196 are counted, as preserved (decided 2026-10). "
        "Improvements permit at 6211 S Vernon.")},
    "ILB20177089": {"category": "preserved", "dated": ("permit", "2017-01-19", "100666777"), "note": (
        "East Park SRO, 3300 W Maypole: Habitat's $16M acquisition and rehab "
        "($7M permit 2017-01-19; reopened 2018). Originally developed ~1994 by "
        "the same partnership. The permit never says 'units', so it is not in "
        "the permit cache.")},
    "ILB20165016": { "units": 135, "units_basis": "135 low-income units with Section 8 and VASH (Interfaith); HUD's 15 is wrong.","category": "preserved", "dated": ("permit", "2015-01-26", "100572974"), "note": (
        "Jeffery Towers, 7020 S Jeffery: 4% LIHTC rehab of an existing "
        "135-unit building; $5.2M life-safety permit 2015-01-26 (and $2M facade "
        "work 2014-08-11).")},
    "ILB20136004": {"cha": True, "mixed_income": True, "note": (
        "Park Boulevard IIB: part of the Stateway Gardens CHA redevelopment; "
        "72 of 108 units low-income.")},
    # ---- Group A: opened 2011-12, unmatched. Researched 2026-10. ----
    "ILA20110906": {"category": "preserved", "dated": ("permit", "2011-07-27", "100404275"), "note": (
        "Parkway Gardens: rehab permits of 2011-07-27 on its buildings at 6514 "
        "and 6536 S King.")},
    "ILB20119812": {"category": "preserved", "dated": ("pre2010", "2009", "100324544"), "note": (
        "North Eastwood Shores: $13.2M remodel of the existing 16-storey, "
        "231-unit building, permitted 2009-12-21.")},
    "ILA20120901": {"category": "preserved", "dated": ("permit", "2011-10-19", "100402771"), "note": (
        "Renaissance Apartments (Woodlawn, scattered): repairs permit at 6107 S "
        "Ellis, one of its buildings. Medium confidence.")},
    "ILB20110808": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("pre2010", "2009", "100287273"), "note": (
        "Savoy Square: Legends South (Robert Taylor redevelopment) buildings on "
        "S State and S Dearborn permitted July 2009.")},
    "ILB20110807": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2010-08-24", "100359790"), "note": (
        "Park Douglas / Ogden North: 19 new buildings permitted August 2010; 137 "
        "units: 60 CHA public housing (Lawndale Gardens replacement), 49 LIHTC, "
        "28 market. Opened July 2012.")},
    "ILA20110933": { "units": 101, "units_basis": "City DOH 2010: 101 at or under 80% AMI; the extra units over HUD's 98 are likely CHA units outside the credit.","category": "new", "cha": True, "mixed_income": True,
        "dated": ("permit", "2010-05-06", "100342503"), "note": (
        "West End Phase II = Rockwell Gardens Phase II: small buildings on S "
        "Campbell, S Maplewood, W Jackson and W Van Buren permitted May-June 2010. "
        "2425 W Adams is the development's management address.")},
    "ILA20120001": {"note": (
        "The Viceroy (now Harvest Commons): built 1929 as an apartment hotel, an "
        "SRO by the 2000s; rehabbed into 89 supportive studios (permit "
        "2011-11-01), reopened 2013. Preserved: it was already housing.")},
    "ILA20110100": {"category": "new", "cha": True, "mixed_income": True,
        "dated": ("pre2010", "2008", "100272336"), "note": (
        "Westhaven Park IIC (Henry Horner redevelopment): buildings at 1900-1950 "
        "W Maypole permitted 2008-12-23.")},
    "ILA20110935": {"category": "new", "assisted_living": True, "dated": ("pre2010", "2007", "100213900"), "note": (
        "Victory Centre of South Chicago: new 112-unit supportive living "
        "facility permitted 2007-11-29. HUD also lists it as ILB20090808 (112 "
        "units, 2009); count once in the stock.")},
    "ILA20110001": {"category": "preserved", "dated": ("estimate", "2010", None), "note": (
        "O'Keefe Apartments, scattered sites in South Shore (HUD: 7150 S Euclid; "
        "a listing: 7156 S Bennett). Buildings and scope not found; year is the "
        "allocation and 'preserved' is the likelier reading. Low confidence.")},
    "ILA20120025": {"category": "new", "dated": ("permit", "2011-06-15", "100398727"), "note": (
        "Paul G. Stewart IV A (Bronzeville Associates Family): four new "
        "buildings of 18, 18, 12 and 18 units (424, 504, 516 and 528 E 41st) "
        "and a community center, all permitted 2011-06-15, replacing 66 of 90 "
        "Section 8 family units. Phase IV A-2 is DOH-BRONZEVILLE-FAMILY-II; "
        "other phases at 400 E 41st are separate records.")},
    "ILA20110931": {"category": "new", "dated": ("permit", "2010-03-23", "100335309"), "note": (
        "Woodlawn Center South: two new buildings (34 and 33 units) at 6227 and "
        "6230 S Cottage Grove permitted 2010-03-23.")},
    "ILB20110809": {"category": "new", "dated": ("pre2010", "2009", "100314618"), "note": (
        "Roseland Place: new 60-unit senior building permitted 2009-10-09.")},
    "ILA20110917": {"category": "new", "dated": ("permit", "2010-10-21", "100369371"), "note": (
        "Hope Manor (Volunteers of America): new 50-unit building for homeless "
        "veterans at 3053 W Franklin (HUD lists 3453 W Madison), permitted "
        "2010-10-21, completed 2011.")},
    "ILA20110908": {"category": "new", "dated": ("pre2010", "2009", "100320285"), "note": (
        "Dr. King Legacy Apartments: new 45-unit building at 3800 W 16th "
        "permitted 2009-11-16. CHA vouchers only.")},
    "ILB20110811": {"category": "new", "basis": "conversion of a commercial building",
        "dated": ("permit", "2010-03-23", "100334815"), "note": (
        "Hairpin Lofts: $8.9M restoration of the 6-storey Hairpin building with "
        "change of use from commercial to commercial/residential. HUD also lists "
        "it as ILB20109003 (2010); count once in the stock.")},
    # ---- Review of automatic matches, batch 1. Checked 2026-10. ----
    "ILB20231001": {"mixed_income": True, "note": (
        "43 Green Phase I (P3 Markets and Habitat), 4308 S Calumet: 99 units, "
        "51 affordable, 48 market (City DOH 2021).")},
    "ILB20250001": {"mixed_income": True, "note": (
        "43 Green Phase II (P3 Markets and Habitat), 4307 S Prairie: 80 units, "
        "44 affordable, 36 market (City DOH 2023); HUD's 47 total is low.")},
    "ILA20240009": {"units": 406, "note": (
        "Lawson House: HUD's one record shows 146 units; the building reopened "
        "in 2024 with 409 SRO studios, 406 low-income. Settled 2026-10: IHDA's "
        "FY2022 closing report lists one 9% deal of 409 units / 406 LIHTC "
        "(closed 2021-12-01), and the City's 2021 table shows the two 9% "
        "allocations (City and IHDA) behind it. No missing 4% deal.")},
    "ILB20159004": {"assisted_living": True, "note": (
        "New City Supportive Living: the former Goldblatt's store converted to "
        "101 units of assisted living (an Illinois Supportive Living Facility) "
        "under a 2011 permit; stalled, refinanced by the City in 2015.")},
    "ILB20120268": {"cha": True, "mixed_income": True, "note": (
        "Park Boulevard IIA: part of the Stateway Gardens CHA redevelopment.")},
    # ---- Review of automatic matches, batch 2. Checked 2026-10. ----
    "ILB0000X069": {"units": 72, "cha": True, "mixed_income": True, "note": (
        "Shops & Lofts at 47, 747 E 47th: 96 apartments over a Walmart, "
        "permitted 2013-02-05; 44 affordable, 28 CHA public housing, 24 market. "
        "HUD has no low-income count (its fallback is all 96).")},
    "ILB20159005": {"category": "split", "split": {"preserved": 44, "new": 14},
        "dated": ("permit", "2014-10-09", "100540767"), "note": (
        "Cornerstone Apartments (The Community Builders): HUD gives 747 E 47th, "
        "but it is a separate 59-unit project at 50th and Langley. Permits of "
        "2014-10-09/10: rehabs of 24 units at 731 E 50th Pl and 21 at 4958 S "
        "Langley, new buildings of 6 at 637 E 50th and 8 at 613 E 50th. HUD "
        "counts 58 of the 59 as low-income; split 44 preserved, 14 new.")},
    "ILB20119813": {"cha": True, "mixed_income": True, "units": 92, "note": (
        "Parkside 9 II: Parkside of Old Town, the Cabrini-Green redevelopment. "
        "HUD counts 85 low-income; the City (DOH 2010) shows 92 restricted and "
        "20 market, so 92 is used.")},
    "ILA20240010": {"cha": True, "mixed_income": True, "note": (
        "Ogden Commons A-1 (former Ogden Courts): 92 units, 37 CHA, 46 "
        "affordable, 9 market.")},
    # ---- Review of automatic matches, batch 3. Checked 2026-10. ----
    "ILA20120007": {"category": "new", "basis": "conversion of an office building",
        "dated": ("permit", "2010-10-29", "100366677"), "note": (
        "Randolph Tower City Apartments: conversion of the 45-storey Steuben "
        "Club office tower to 307 (later 313) apartments. First permitted "
        "2008-06-02 (100238556); stalled in the crash, re-permitted 2010-10-29 "
        "with the IHDA bond of 2010-12-22; opened 2012. Dated 2010, when the "
        "LIHTC deal and the work happened.")},
    "ILB20162003": {"cha": True, "mixed_income": True, "note": (
        "Parkside IIB: Parkside of Old Town, the Cabrini-Green redevelopment.")},
    "ILB20132005": { "units": 60, "units_basis": "HUD's 55 plus the 5 units at 61-80% AMI that the City (DOH 2012) shows for Lakefront Phase II as a whole.","cha": True, "mixed_income": True, "note": (
        "Lakefront Phase II, 1060 E 41st: Lake Park Crescent.")},
    "ILA20230021": {"cha": True, "mixed_income": True, "note": (
        "Southbridge Phase 1A: the Harold Ickes redevelopment.")},
    # ---- Review of automatic matches, batch 4. Checked 2026-10. ----
    "ILB20212001": {"cha": True, "mixed_income": True, "units": 64, "note": 'Parkside 4 II: Parkside of Old Town, the Cabrini-Green redevelopment. HUD counts 47 low-income; the City (DOH 2019) shows 64 at or under 60% AMI and 38 at 81-100%, so 64 is used.'},
    "ILA20220048": {"cha": True, "mixed_income": True, "note": 'Southbridge Phase 1B: the Harold Ickes redevelopment.'},
    "ILA20230011": {"cha": True, "mixed_income": True, "note": "Lathrop Homes IB: CHA's Lathrop Homes, renovated as mixed-income."},
    "ILB0000X031": {"cha": True, "mixed_income": True, "units": 52, "note": 'Clybourn 1200 (Clybourn and Division), 454 W Division: 84 units, about 30% CHA, 30% affordable, 40% market; on Cabrini-Green land. HUD counts 40 low-income; the City (DOH 2015) shows 52 restricted and 32 market, so 52 is used.'},
    "ILB0000X020": {"cha": True, "mixed_income": True, "units": 59, "note": '4400 Grove: first phase on the former Washington Park Homes site; includes CHA public housing units. HUD counts 38 low-income; the City (DOH 2018) shows 59 at or under 60% AMI and 25 market, so 59 is used.'},
    "ILB20195001": {"cha": True, "mixed_income": True, "note": 'Independence Apartments and library, 4022 N Elston: 44 senior units, 30 CHA and 14 affordable.'},
    "ILB0000X075": {"cha": True, "mixed_income": True, "units": 66, "note": 'Taylor Street Apartments and library, 1342 W Taylor: 73 units, 37 CHA, 29 affordable, 7 market. HUD counts only the 29; the CHA units are income-restricted too (City DOH 2017: 66 at or under 60% AMI).'},
    "ILB20132004": {"cha": True, "mixed_income": True, "note": 'Lakefront Phase II (Lake Park Crescent), 4131 S Lake Park.'},
    "ILB20132002": {"cha": True, "mixed_income": True, "note": 'Lakefront Phase II (Lake Park Crescent), 1140 E 42nd Pl.'},
    "ILB0000X066": {"note": (
        "Pullman Artspace Lofts: a new 26-unit building between two long-vacant "
        "1880s buildings rehabbed, 38 units. 6 reserved for CHA residents; "
        "whether public housing or vouchers is unconfirmed, so not tagged CHA.")},
    "ILB20153003": { "units": 53, "units_basis": '53 affordable at or under 60% AMI, 9 market, 1 manager (Landmarks Illinois, City DOH 2014).',"category": "new", "basis": "rehab of a vacant building", "note": (
        "The Historic Strand, 6315 S Cottage Grove: 1914 hotel vacant for nearly "
        "a decade, converted by Holsten to 63 apartments (2014 permit).")},
    "ILB0000X039": {"category": "preserved", "units": 36,
        "units_basis": "36-unit building (Evergreen, 2018); IHDA's 2014 award was for 37 and the 2015 permit turns one unit into a library. CHA vouchers cover 31.",
        "dated": ("permit", "2015-10-14", "100575029"), "note": (
        "Humboldt House (now Steven Michael Carroll House), 1819 N Humboldt: "
        "Thresholds supportive housing for people with mental illness. $2.4M "
        "rehab permit at its own address; reopened December 2018. HUD records "
        "no units. The matcher had picked a 2-to-3-unit conversion next door.")},
    "ILA20190017": {"note": (
        "Thresholds RAD 2: the 44-unit Grais Apartments at 6808 N Wayne, one of "
        "three buildings in IHDA's 146-unit 4% deal of 2017-12-28. The other "
        "two are IHDA4-THRESHOLDS-RAD2-AUSTIN and -ROWAN. Rechecked 2026-10.")},
    "ILA20230002": {"note": (
        "900 W Randolph: the 60 affordable units inside Related Midwest's "
        "300-unit tower at 164 N Peoria. Not in the city's ARO list, so not "
        "counted twice. Checked 2026-10.")},
}

#: HUD spellings that differ from the permit data's.
STREET_FIXES = [
    (r"\b(?:DR\s+)?(?:MARTIN\s+L(?:UTHER)?\s+)?KING(?:\s+JR)?\s+(?:DR|DRIVE)\b",
     "DR MARTIN L KING JR DR"),
    (r"\bROOSEVLT\b", "ROOSEVELT"),
    (r"\bLASALLE\b", "LA SALLE"),
    (r"\bHIGHWAY\b", "HWY"),
    (r"\bBOULAV\w*|\bBOULEVARD\b|\bBL\b", "BLVD"),
]

DIRECTIONS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


# ---- fetch -------------------------------------------------------------

def fetch_hud():
    """Chicago rows of LIHTCPUB, cached locally."""
    if os.path.exists(CACHE):
        print(f"  using cached {CACHE}")
        return json.load(open(CACHE))
    # huduser.gov answers a bare client with an empty 202 challenge page
    req = urllib.request.Request(HUD_URL, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
    ctx = aro._ssl_context()
    with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
        blob = r.read()
    outer = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in outer.namelist() if n.upper().endswith("LIHTCPUB.XLSX"))
    rows = read_xlsx(outer.open(name), keep=lambda r: (
        r.get("proj_st") == "IL" and str(r.get("proj_cty") or "").strip().upper() == "CHICAGO"))
    json.dump(rows, open(CACHE, "w"))
    return rows


def read_xlsx(fh, keep):
    """
    Rows of the first sheet as dicts, filtered by `keep`. Read straight from
    the XML: HUD's file carries a sheet property openpyxl rejects.
    """
    z = zipfile.ZipFile(io.BytesIO(fh.read()))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
            if el.tag == ns + "si":
                shared.append("".join(t.text or "" for t in el.iter(ns + "t")))
                el.clear()
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]

    def col(ref):
        n = 0
        for ch in re.match(r"[A-Z]+", ref).group():
            n = n * 26 + ord(ch) - 64
        return n - 1

    header, out = None, []
    for _, el in ET.iterparse(z.open(sheet)):
        if el.tag != ns + "row":
            continue
        row = {}
        for c in el.findall(ns + "c"):
            v, t = c.find(ns + "v"), c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(ns + "t"))
            elif v is None:
                val = None
            elif t == "s":
                val = shared[int(v.text)]
            else:
                val = v.text
            row[col(c.get("r"))] = val
        el.clear()
        if header is None:
            header = {i: str(v).lower() for i, v in row.items()}
            continue
        rec = {header[i]: v for i, v in row.items() if i in header}
        if keep(rec):
            out.append(rec)
    return out


def fetch_extra_permits(projects, have):
    """
    Renovation and easy permits of EXTRA_MIN_COST or more within
    EXTRA_RADIUS_M of each project, not already in the shared cache.
    Cached locally; delete EXTRA_CACHE to refresh.
    """
    if os.path.exists(EXTRA_CACHE):
        print(f"  using cached {EXTRA_CACHE}")
        return json.load(open(EXTRA_CACHE))
    ctx = aro._ssl_context()
    sel = ("permit_,permit_type,issue_date,ward,reported_cost,work_description,"
           "street_number,street_direction,street_name,latitude,longitude")
    found = {}
    for r in projects:
        if not (r.get("latitude") and r.get("longitude")):
            continue
        where = (f"within_circle(location, {r['latitude']}, {r['longitude']}, {EXTRA_RADIUS_M}) "
                 f"AND reported_cost >= {EXTRA_MIN_COST} AND issue_date >= '2010-01-01' "
                 "AND permit_type in('PERMIT - RENOVATION/ALTERATION','PERMIT - EASY PERMIT PROCESS')")
        q = urllib.parse.urlencode({"$where": where, "$limit": 200, "$select": sel})
        with urllib.request.urlopen(f"{PERMITS_API}?{q}", timeout=120, context=ctx) as resp:
            for p in json.load(resp):
                if p["permit_"] not in have:
                    found[p["permit_"]] = p
    rows = list(found.values())
    json.dump(rows, open(EXTRA_CACHE, "w"))
    print(f"  fetched {len(rows)} large renovation permits missing from the shared cache")
    return rows


# ---- addresses ---------------------------------------------------------

def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def year(x):
    """HUD codes unknown years as 8888 and 9999."""
    y = num(x)
    return y if 1980 <= y <= 2100 else None


def hud_address(text):
    """
    (low, high, street) from HUD's address field, or None.

    HUD writes ranges with a space ("1007 1157 E 47TH ST") or a dash, spells
    directions out ("34 NORTH KEELER AVE"), and sometimes drops the suffix.
    """
    t = re.sub(r"\(.*?\)", " ", str(text or "").upper())  # "(SCATTERED SITES)"
    t = re.sub(r"[.,]", " ", t).strip()
    if t and not t[0].isdigit():
        # "AUREA E MARTINEZ APARTMENTS 5525 W DIVERSEY AVE": the project name
        # written in front. Take the last number-then-direction in the text,
        # so "ALLOCATION 1 6221 S VERNON" finds 6221, not 1.
        found = list(re.finditer(r"\b\d+(?:\s*-\s*\d+)?\s+(?:[NSEW]|NORTH|SOUTH|EAST|WEST)\b", t))
        if found:
            t = t[found[-1].start():]
    for pat, rep in STREET_FIXES:
        t = re.sub(pat, rep, t)
    t = re.sub(r"\s+\d+\s*$", "", t)  # trailing suite number
    m = re.match(r"^(\d+)\s*(?:-|\s)\s*(\d+)\s+(.+)$", t)
    if m and m.group(3)[:1].isalpha():
        lo, hi, rest = int(m.group(1)), int(m.group(2)), m.group(3)
        if hi < lo and len(m.group(2)) < len(m.group(1)):
            # "3542-40" means 3540-3542
            hi = int(m.group(1)[:-len(m.group(2))] + m.group(2))
            lo, hi = min(lo, hi), max(lo, hi)
    else:
        m = re.match(r"^(\d+)\s+(.+)$", t)
        if not m:
            return None
        lo = hi = int(m.group(1))
        rest = m.group(2)
    toks = rest.split()
    if toks and toks[0] in DIRECTIONS:
        toks[0] = DIRECTIONS[toks[0]]
    k = aro.street_key(lo, " ".join(toks))
    if not k:
        return None
    if hi < lo or hi - lo > 400:
        hi = lo
    return lo, hi, k[1]


# ---- matching ----------------------------------------------------------


# ---- pipeline: LIHTC awards not (yet) in HUD's database ---------------------
#
# HUD records a project only once it is placed in service, and even then
# irregularly: none of the City's 2019-round winners were in HUD's file as of
# its 2024 data. These come from the City's award rounds (2019, 2021, 2023,
# 2025), IHDA's 9% awards in Chicago and IHDA bond closings. Each is dated by
# its building permit like everything else; an award with no permit yet is
# "awarded" and stays out of the yearly series.
#
# Fields: units / li_units (low-income), permit (date, id), category,
# opened (year, where known), cha / mixed_income, sources, note.
PIPELINE = [
    # ---- City of Chicago 2019 round (announced March 2020). Checked 2026-10.
    {"id": "CITY2019-ROOSEVELT-3B", "project": "Roosevelt Square 3B",
     "address": "1002 S Racine Ave; 1257 and 1357 W Roosevelt Rd", "units": 207, "li_units": 115,
     "permit": ("2022-12-07", "100917080"), "category": "new", "opened": 2025,
     "cha": True, "mixed_income": True, "sources": ["City 2019"],
     "note": "Three new buildings (67, 70, 70) on the former ABLA Homes site: 75 CHA, 40 affordable, 92 market. 4% LIHTC with City bonds."},
    {"id": "CITY2019-PARK-STATION", "project": "Park Station Lofts",
     "address": "6311 S Maryland Ave", "units": 58, "li_units": 41,
     "permit": ("2022-07-21", "100945010"), "category": "new", "opened": 2024,
     "sources": ["City 2019"],
     "note": "58 units incl. 2 live/work; 41 at 30-60% AMI, 17 market. City list gives 6300 S Blackstone."},
    {"id": "CITY2019-FOGLIA", "project": "Foglia Residences at the Chicago Lighthouse",
     "address": "1134 S Wood St", "units": 76, "li_units": 76,
     "permit": ("2022-05-17", "100924570"), "category": "new", "opened": 2024,
     "sources": ["City 2019"],
     "note": "9-storey building for people who are blind or visually impaired, 30-80% AMI; 19 units with CHA vouchers (not CHA housing)."},
    {"id": "CITY2019-GARFIELD-GREEN", "project": "Garfield Green Phase 1",
     "address": "3155 W Fifth Ave", "units": 43, "li_units": 43,
     "permit": ("2023-05-05", "100971195"), "category": "new", "opened": 2025,
     "sources": ["City 2019"],
     "note": "POAH, C40 Reinventing Cities site at Kedzie and Fifth; 43 rental units at 30-80% AMI. Phase 2 is for-sale/co-op."},
    {"id": "CITY2019-SIFUENTES", "project": "Maria Elena Sifuentes Apartments (Metropolitan Apartments)",
     "address": "4753 N Central Park Ave (3559 W Lawrence Ave)", "units": 50, "li_units": 42, "mixed_income": True,
     "permit": ("2021-12-13", "100899044"), "category": "new",
     "sources": ["City 2019"],
     "note": "Celadon with ROOTS / Communities United, Albany Park; renamed from Metropolitan Apartments."},
    {"id": "CITY2019-ENCUENTRO-1", "project": "Encuentro Square Phase I",
     "address": "3759 W Cortland St; 1844 N Ridgeway Ave", "units": 89, "li_units": 89,
     "permit": ("2023-06-01", "100984989"), "category": "new", "opened": 2025,
     "sources": ["City 2019"],
     "note": "LUCHA and Evergreen: two buildings of 32 and 57 units, all affordable."},
    {"id": "CITY2019-CASA-DURANGO", "project": "Casa Durango",
     "address": "1858 S Racine Ave", "units": 53, "li_units": 53,
     "permit": ("2021-08-27", "100920051"), "category": "new", "opened": 2022,
     "sources": ["City 2019"],
     "note": "The Resurrection Project, Pilsen; all 53 units up to 60% AMI: 37 at 1850-58 S Racine and 16 at 2008-12 S Ashland (permit 100909473, 2021-09-01). Opened February 2023."},
    {"id": "CITY2019-WESTHAVEN-IID", "project": "Westhaven Park Station (Westhaven Park IID)",
     "address": "145 N Damen Ave", "units": 96, "li_units": 63,
     "permit": ("2022-08-19", "100939990"), "category": "new", "opened": 2024,
     "sources": ["City 2019"],
     "cha": True, "mixed_income": True,
     "note": "Final phase of Westhaven Park (Henry Horner redevelopment): 38 CHA replacement units (project-based), 25 affordable, 33 market."},
    {"id": "CITY2019-PARK-BLVD-3B", "project": "Park Boulevard 3B",
     "address": "41 W 36th St; 42 W 37th St", "units": 80, "li_units": 68,
     "permit": ("2021-07-07", "100910793"), "category": "new", "opened": 2023,
     "cha": True, "mixed_income": True, "sources": ["City 2019"],
     "note": "Fifth sub-phase of the Stateway Gardens redevelopment: two 40-unit buildings; 68 affordable incl. 36 CHA replacement units, 12 market."},
    # Also City 2019, already in HUD's file: Lawson House (ILA20240009) and
    # 43 Green Phase I (ILB20231001, '43rd and Calumet Phase I').
    # ---- City of Chicago 2021 round (announced 2021-12-06). Checked 2026-10.
    {"id": "CITY2021-4715-WESTERN", "project": "4715 N Western Apartments",
     "address": "4713 N Western Ave", "units": 63, "li_units": 63,
     "permit": ("2023-07-13", "101007078"), "category": "new", "opened": 2024, "sources": ["City 2021"],
     "note": "The Community Builders, on a former City parking lot by the Western Brown Line; 100% affordable."},
    {"id": "CITY2021-LATHROP-1D", "project": "Lathrop Homes Phase 1D",
     "address": "2600-2768 N Hoyne Ave", "units": 309, "li_units": 213,
     "permit": ("2026-07-14", "101055536"), "category": "new", "basis": "rehab of vacant buildings and new construction",
     "cha": True, "mixed_income": True, "sources": ["City 2021"],
     "note": "Seven long-vacant Lathrop buildings rehabbed plus a new 59-unit building (permit 101055542): 121 CHA, 92 affordable, 96 market. Permitted 2026; under construction."},
    {"id": "CITY2021-PRAIRIE-DISTRICT-9", "project": "Prairie District Apartments (new building)",
     "address": "1800 S Michigan Ave", "units": 50, "li_units": 50,
     "permit": ("2024-05-23", "101024548"), "category": "new", "sources": ["City 2021"],
     "note": "Mercy Housing Lakefront; 9% credits; 30-80% AMI."},
    {"id": "CITY2021-PRAIRIE-DISTRICT-4", "project": "Prairie District Apartments (rehab)",
     "address": "1801 S Wabash Ave", "units": 100, "li_units": 100,
     "permit": ("2024-05-29", "101032971"), "category": "preserved", "sources": ["City 2021"],
     "note": "Mercy Housing Lakefront: the existing 170-room SRO (The Studios) rebuilt as 100 supportive studios; 4% credits."},
    {"id": "CITY2021-JIGZIBIK", "project": "Jigzibik (Visionary Ventures)",
     "address": "2909 W Irving Park Rd", "units": 45, "li_units": 45,
     "permit": ("2025-05-06", "101061450"), "category": "new", "sources": ["City 2021"],
     "note": "Full Circle Communities and Visionary Ventures: Chicago's first Native American-focused affordable housing; 15-60% AMI. Topped out January 2026."},
    {"id": "CITY2021-AUSTIN-UNITED", "project": "Austin United Alliance",
     "address": "5224 W Chicago Ave", "units": 78, "li_units": 50, "mixed_income": True,
     "permit": ("2022-12-15", "100986472"), "category": "new", "opened": 2026, "sources": ["City 2021"],
     "note": "Oak Park Regional Housing Center and Pivotal Housing Partners (INVEST South/West, by the Laramie State Bank). The 2021 award said 60 affordable; the City's ribbon-cutting release (September 2026) gives 50 affordable at 50-60% AMI and 28 market."},
    {"id": "CITY2021-IMANI", "project": "Imani Village Senior Residences Phase I",
     "address": "9621 S Cottage Grove Ave", "units": 70, "li_units": 70,
     "permit": ("2023-09-19", "101003932"), "category": "new", "opened": 2025, "sources": ["City 2021"],
     "note": "Trinity United Church of Christ and SPM; senior."},
    {"id": "CITY2021-LECLAIRE-NORTH", "project": "LeClaire Courts A-North",
     "address": "4809 W 44th St", "units": 138, "li_units": 124,
     "permit": ("2025-10-16", "101061913"), "category": "new", "cha": True, "mixed_income": True, "sources": ["City 2021", "IHDA 4% 2025"],
     "note": "Former LeClaire Courts CHA site (Habitat and Cabrera). Phase A is two buildings and two deals that do not line up: per IHDA's March 2026 TEFRA notice the A-North deal owns the 110-unit North building (4809 W 44th) plus 28 units in the South building (4442 S Cicero); A-South (IHDA2022-LECLAIRE-SOUTH) owns the South building's other 45. Counts are IHDA's (138 units, 124 affordable incl. CHA project-based); dated by the North building permit."},
    {"id": "CITY2021-LEGENDS-A3", "project": "Legends South A3",
     "address": "4520 S State St", "units": 52, "li_units": 42,
     "permit": ("2025-02-10", "101053169"), "category": "new", "cha": True, "mixed_income": True, "sources": ["City 2021"],
     "note": "Former Robert Taylor Homes site (Brinshore and Michaels): 21 CHA, 21 affordable, 10 market, in buildings of 40 and 12."},
    {"id": "CITY2021-SACRED", "project": "SACRED Apartments",
     "address": "9212 S Burley Ave", "units": 81, "li_units": 81,
     "permit": ("2024-11-13", "101027746"), "category": "new", "sources": ["City 2021"],
     "note": "Claretian Associates and Interfaith; under 60% AMI, 20% supportive."},
    {"id": "CITY2021-THRIVE", "project": "Thrive Englewood",
     "address": "6249 S Sangamon St", "units": 62, "li_units": 52,
     "permit": ("2023-02-23", "100988004"), "category": "new", "opened": 2025, "sources": ["City 2021"],
     "note": "DL3 Realty: 11 at 50% AMI, 40 at 60%, 10 market per the 2021 award; updated 2026-10 to 52 affordable of 62 (more restricted units)."},
    {"id": "CITY2021-1237-CALIFORNIA", "project": "1237 N California Apartments",
     "address": "1237 N California Ave", "units": 40, "li_units": 40,
     "permit": ("2024-07-22", "100994216"), "category": "new", "opened": 2026, "sources": ["City 2021"],
     "note": "Hispanic Housing Development Corp., Humboldt Park."},
    {"id": "CITY2021-BOULEVARD", "project": "Boulevard Apartments",
     "address": "1930-38 N Humboldt Blvd; 929-35 N Sacramento; 2212-14 N Sacramento", "units": 70, "li_units": 70,
     "permit": ("2023-02-15", "100989586"), "category": "preserved", "opened": 2025,
     "resyndication_of": "ILB19930010", "sources": ["City 2021"],
     "note": "Bickerdike's $40.5M rehab of three walk-ups first rehabbed in the early 1990s."},
    {"id": "CITY2021-PARKSIDE-3", "project": "Parkside of Old Town Phase 3",
     "address": "520 W Hobbie St", "units": 99, "li_units": 65,
     "permit": ("2024-05-23", "100994285"), "category": "new", "opened": 2026,
     "cha": True, "mixed_income": True, "sources": ["City 2021"],
     "note": "Cabrini-Green (Holsten and the Cabrini Green LAC): a 69-unit midrise at 520 W Hobbie (permit 100994289, 2024-05-24) and three walk-ups at 1118 N Cleveland, 1117 N Cambridge and 542 W Elm (permits from 2024-05-23). 37 CHA (RAD project-based), 28 affordable at 50-60% AMI, 34 market; opened October 2026."},
    {"id": "CITY2021-ALBANY-TERRACE", "project": "Albany Terrace Apartments",
     "address": "3030 W 21st Pl", "units": 350, "li_units": 350,
     "permit": ("2022-09-28", "100938160"), "category": "preserved", "cha": True, "sources": ["City 2021"],
     "note": "CHA senior high-rise, co-owned with Michaels since 2023; $47M rehab (the 'Albany-Gaines Redevelopment')."},
    {"id": "CITY2021-GAINES", "project": "Irene McCoy Gaines Apartments",
     "address": "3700 W Congress Pkwy", "units": 150, "li_units": 150,
     "permit": ("2022-10-31", "100938168"), "category": "preserved", "opened": 2024, "cha": True, "sources": ["City 2021"],
     "note": "CHA senior high-rise, co-owned with Michaels; $24M rehab, reopened October 2024."},
    {"id": "CITY2021-CHURCHVIEW", "project": "Churchview Supportive Living",
     "address": "2626 W 63rd St", "units": 86, "li_units": 86, "assisted_living": True,
     "permit": ("2023-02-06", "100983145"), "category": "preserved",
     "resyndication_of": "ILB20030806", "sources": ["City 2021"],
     "note": "Greater Southwest Development Corp.; Supportive Living Facility (assisted living) opened 2004."},
    {"id": "CITY2021-ISLAND-TERRACE", "project": "Island Terrace",
     "address": "6430 S Stony Island Ave", "units": 240, "li_units": 222, "mixed_income": True,
     "permit": ("2023-08-31", "100984805"), "category": "preserved", "sources": ["City 2021", "IHDA 9% 2021", "IHDA bond 2023"],
     "note": "POAH's near-gut rehab ($111M) of a 1969 21-storey building; completion due 2027. Restricted units rise from 143 to 222 (35-80% AMI) of 240, per POAH; the rest are unrestricted."},
    # Awarded in 2021, no permit found as of 2026-10:
    {"id": "CITY2021-MORGAN-PARK", "project": "East Morgan Park Commons (phase 1)",
     "address": "800-834 W 115th St", "units": 70, "li_units": 70, "permit": None, "category": "new",
     "sources": ["City 2021"], "note": "Far South CDC, DL3 and POAH, on the former Jewel site; rezoned December 2025, funding May 2026. Updated 2026-10 to 70 units, all affordable, at 800-834 W 115th, per the 2026 bond ordinance's site (the 2021 list had 66 affordable at 11400-11458 S Halsted)."},
    {"id": "CITY2021-REGENERATOR", "project": "The Regenerator",
     "address": "6206 S Racine Ave", "units": 42, "li_units": 42, "permit": None, "category": "new",
     "basis": "conversion of a school", "sources": ["City 2021"],
     "note": "Gorman and Go Green on Racine: the former Woods school converted with a fourth-floor addition. Updated 2026-10 to 42 units (the 2021 list had 48)."},
    {"id": "CITY2021-ARCHER-SENIOR", "project": "Archer Avenue Senior Residences",
     "address": "2928 S Archer Ave", "units": 55, "li_units": 55, "permit": None, "category": "preserved",
     "resyndication_of": "ILB20070808", "sources": ["City 2021"], "note": "POAH; no rehab permit found."},
    {"id": "CITY2021-NORTH-TOWN-VILLAGE", "project": "North Town Village 1",
     "address": "1311 N Halsted St", "units": 116, "li_units": 78, "permit": None, "category": "preserved",
     "cha": True, "mixed_income": True, "resyndication_of": "ILB20010803", "sources": ["City 2021"],
     "note": "Holsten, Cabrini-Green: 116 mixed-income rentals to preserve, 78 restricted (39 CHA, 39 affordable) and 38 market per the City's CHA appendix; no rehab permit found; portfolio for sale 2026."},
    # Also City 2021, already in HUD's file: 43 Green Phase II (ILB20250001,
    # '43rd and Prairie Phase II').
    # ---- City of Chicago 2023 round (announced March 2024). Checked 2026-10.
    {"id": "CITY2023-ABRAMS", "project": "Abrams Intergenerational Village",
     "address": "5300 S Calumet Ave", "units": 71, "li_units": 71,
     "permit": ("2026-01-27", "101069022"), "category": "new", "sources": ["City 2023", "IHDA 9% 2023"],
     "note": "The Renaissance Collaborative: seniors, grandfamilies and young adults; all affordable."},
    {"id": "CITY2023-HUB-32", "project": "Hub 32",
     "address": "136 N Kedzie Ave", "units": 51, "li_units": 51,
     "permit": ("2026-09-16", "101078161"), "category": "new", "sources": ["City 2023"],
     "note": "Michaels, KMW and TruDelta by the Kedzie Green Line; up to 60% AMI. Reduced from 63 units."},
    {"id": "CITY2023-PARK-MANOR", "project": "Park Manor Senior Residences of Chatham Phase I",
     "address": "8332 S Dr Martin Luther King Jr Dr", "units": 52, "li_units": 52,
     "permit": ("2026-10-09", "101081192"), "category": "new", "sources": ["City 2023"],
     "note": "Senior (63+); 44 one-bed, 8 two-bed."},
    {"id": "CITY2023-MARTHA-WASHINGTON", "project": "The Martha Washington",
     "address": "2324 W Irving Park Rd", "units": 103, "li_units": 103,
     "permit": ("2026-01-08", "101070838"), "category": "preserved",
     "resyndication_of": "ILB20059008", "sources": ["City 2023"],
     "note": "Evergreen: senior building, 30-80% AMI; City bonds and loan."},
    {"id": "CITY2023-HOMAN-SQUARE-IV", "project": "Homan Square Apartments Phase IV",
     "address": "906 and 908 S Central Park; 921 S Lawndale; 3607, 3621 and 3645 W Polk", "units": 107, "li_units": 107,
     "permit": ("2025-08-13", "101068604"), "category": "preserved", "sources": ["City 2023"],
     "note": "IFF: six walk-ups rehabbed under six permits of 2025-08-13 (18+18+18+17+18+18 units)."},
    # Awarded in 2023, no permit found as of 2026-10:
    {"id": "CITY2023-BELRAY", "project": "Belray Apartments",
     "address": "3150 N Racine Ave", "units": 86, "li_units": 86, "permit": None,
     "category": "split", "split": {"preserved": 51, "new": 35}, "sources": ["City 2023"],
     "note": "Mercy Housing Lakefront: the 70-room SRO rebuilt as 51 units plus a 35-unit addition; for people experiencing homelessness."},
    {"id": "CITY2023-5853-BROADWAY", "project": "5853 N Broadway",
     "address": "5853 N Broadway", "units": 90, "li_units": 90, "permit": None, "category": "new",
     "sources": ["City 2023"], "note": "Bickerdike, on a former Streets and San yard; approved 2025, move-ins targeted 2027."},
    {"id": "CITY2023-HARVEST-HOMES-II", "project": "Harvest Homes II",
     "address": "3415 W Jackson Blvd", "units": 50, "li_units": 50, "permit": None, "category": "new",
     "sources": ["City 2023"], "note": "NHP Foundation and PCDAC; construction planned November 2026."},
    {"id": "CITY2023-WBC-MANOR", "project": "WBC Manor",
     "address": "6151 S University Ave", "units": 46, "li_units": 37, "mixed_income": True, "permit": None, "category": "new",
     "sources": ["City 2023"], "note": "Woodlawn Baptist Church and 5T; senior; approved May 2026, awaiting financing. Updated 2026-10 to 37 affordable of 46."},
    {"id": "CITY2023-PILSEN-COC", "project": "Pilsen Migrant Continuum of Care Workforce Housing",
     "address": "1650 W 17th St", "units": 77, "li_units": 77, "permit": None, "category": "new",
     "sources": ["City 2023"], "note": "Metropolitan Housing Development Corp.; about 77 households in a new building and the old refectory on the former St. Adalbert campus (16th and Ashland; the church's address is used)."},
    {"id": "CITY2023-HILLIARD", "project": "Hilliard Homes",
     "address": "2031 S Clark St; 54 W Cermak Rd", "units": 327, "li_units": 327, "permit": None, "category": "preserved",
     "cha": True, "resyndication_of": "ILB20060809", "sources": ["City 2023"],
     "note": "Holsten's recap of Hilliard Homes Phase I on CHA land (99-year lease): the 2031 S Clark family tower and the 54 W Cermak senior tower, 327 units (153 CHA, 174 affordable) per the City's 2014 CHA appendix. Phase II (2111 S Clark and 30 W Cermak, HUD ILB20069001, another 327) is not part of this deal."},
    {"id": "CITY2023-RIVERSIDE-VILLAGE", "project": "Riverside Village",
     "address": "13215 S Indiana Ave", "units": 258, "li_units": 258, "permit": None, "category": "preserved",
     "follow_up": True, "sources": ["City 2023"],
     "note": "Habitat: rehab of 39 buildings (built 1968), $80M, scheduled 2025-26 with IFF predevelopment loan in May 2025. No permit of any kind on Indiana Ave or 130th-133rd Sts since 2024 (checked 2026-10). FOLLOW UP."},
    {"id": "CITY2023-JANET-SMITH", "project": "Janet L. Smith Apartments",
     "address": "505 W 119th St", "units": 36, "li_units": 36, "permit": None, "category": "new",
     "sources": ["City 2023"],
     "note": "Interfaith Housing; permanent supportive housing for formerly homeless families on vacant land in West Pullman. 36 units at 505-517 W 119th per the Sun-Times (March 2024); the 2023 award list said 39. No permit as of 2026-10. A separate 79-unit 'Janet L. Smith Apartments' at 410 N Monticello was on the City's 2025 invited list but not among the 2025 awards."},
    # ---- City of Chicago 2025 round (announced April 2026): none permitted as of 2026-10.
    # Units are from the City's list of projects invited to the final stage.
    {"id": "CITY2025-SOUTHBRIDGE-3A", "project": 'Southbridge 3A', "address": '2400 S State St', "units": 87, "li_units": 48, "permit": None, "category": "new", "cha": True, "mixed_income": True, "sources": ["City 2025"], "note": "The Community Builders: next phase of the Harold Ickes redevelopment. Updated 2026-10 to 48 affordable of 87 (the invited list had 80 units); 48 may leave out CHA units; check when permitted."},
    {"id": "CITY2025-HEART-SOUTH-SHORE", "project": 'Heart of South Shore', "address": '7050-56 S Stony Island Ave', "units": 55, "li_units": 44, "mixed_income": True, "permit": None, "category": "new", "sources": ["City 2025"], "note": "P3 Markets. Updated 2026-10 to 44 affordable of 55 (the invited list had 54)."},
    {"id": "CITY2025-GATEWAY-79", "project": 'Gateway 79', "address": '7901-19 S State St', "units": 59, "li_units": 59, "permit": None, "category": "new", "sources": ["City 2025"], "note": "The NHP Foundation, Chatham. Updated 2026-10 to 59 units, all affordable (the invited list had 50)."},
    {"id": "CITY2025-79TH-WOODLAWN", "project": '79th & Woodlawn Senior Living', "address": '7842 S Woodlawn Ave', "units": 52, "li_units": 52, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'SPM Properties; senior/supportive.'},
    {"id": "CITY2025-BONTEMPS", "project": 'Bontemps Apartments', "address": '1241 W 58th St', "units": 72, "li_units": 72, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'Chateaux 14 Development, West Englewood.'},
    {"id": "CITY2025-WOODLAWN-SOCIAL", "project": 'Woodlawn Social', "address": '1017 E 63rd St', "units": 60, "li_units": 60, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'POAH (9% component; the invited list numbers it 058-002).'},
    {"id": "CITY2025-KLEOS-CORNER", "project": "KLEO's Corner Apartments", "address": '119 E Garfield Blvd', "units": 42, "li_units": 42, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'KLEO Family Life Center, Washington Park.'},
    {"id": "CITY2025-18TH-PEORIA-M2", "project": '18th & Peoria M2', "address": '900 W 18th St', "units": 75, "li_units": 75, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'The Resurrection Project, on the 18th and Peoria site the City bought in 2021.'},
    {"id": "CITY2025-ENCUENTRO-2", "project": 'Encuentro Square Phase 2', "address": '3745 W Cortland St', "units": 98, "li_units": 98, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'LUCHA; phase 1 (89 units) is CITY2019-ENCUENTRO-1.'},
    {"id": "CITY2025-OGDEN-A3", "project": 'Ogden Commons A-3', "address": '1244 S Washtenaw Ave', "units": 42, "li_units": 37, "permit": None, "category": "new", "cha": True, "mixed_income": True, "sources": ["City 2025"], "note": "The Habitat Company, former Ogden Courts CHA site. Updated 2026-10 to 37 affordable of 42."},
    {"id": "CITY2025-WESTGATE", "project": 'Westgate', "address": '3801-11 W Madison St', "units": 55, "li_units": 55, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'East Lake Management, West Garfield Park.'},
    {"id": "CITY2025-METROPOLITAN-L", "project": 'Metropolitan L Apartments', "address": '2525 N Kedzie Blvd', "units": 96, "li_units": 96, "permit": None, "category": "new", "sources": ["City 2025"], "note": 'Bickerdike (4% and 9% twin), Logan Square.'},
    {"id": "CITY2025-LELAND", "project": 'The Leland', "address": '1207 W Leland Ave', "units": 107, "li_units": 107, "permit": None, "category": "preserved", "resyndication_of": 'ILA20050055', "sources": ["City 2025", "IHDA 9% 2025"], "note": "Mercy Housing Lakefront; SRO. IHDA calls it Leland Apartments II and its 2025 progress report gives 137 units; HUD's 2005 record (Leland Apts, 1201 W Leland) shows 136. 107 used here, as in the City's 2025 award (SRO rooms combined into larger units, presumably). City Council approved a sale and loan forgiveness in May 2025."},
    {"id": "CITY2025-HOLLYWOOD-HOUSE", "project": 'Hollywood House', "address": '5700 N Sheridan Rd', "units": 197, "li_units": 197, "permit": None, "category": "preserved", "resyndication_of": 'ILA00000057', "sources": ["City 2025"], "note": 'Full Circle Communities; senior. Last rehabbed under a 2008 permit.'},
    {"id": "CITY2025-GATEWAY-SNEIDER", "project": 'Gateway Apartments Preservation (James Sneider Apartments)', "address": '7450 N Rogers Ave', "units": 120, "li_units": 120, "permit": None, "category": "preserved", "resyndication_of": 'ILA20030040', "sources": ["City 2025"], "note": "Hispanic Housing Development Corp.; senior. HUD's 2003 record: Gateway Centre Apts, 119 units."},
    # ---- IHDA 9% awards in the City of Chicago set-aside that are on no City
    # list (2019-2026 approved allocations). Dated by the earliest permit in a
    # caisson/foundation/full sequence; units are the permit's where IHDA differs.
    {"id": "IHDA2019-VICTORY", "project": "Victory Apartments Preservation",
     "address": "1215 N Leavitt St", "units": 107, "li_units": 107,
     "permit": ("2019-12-19", "100855572"), "category": "preserved", "sources": ["IHDA 9% 2019"],
     "note": "Bickerdike; Section 8. Nine buildings rehabbed under permits of 2019-12-19 (1215 N Leavitt, 1219 N Wolcott, 2845/2901/2915 W Augusta, 3101 W Wabansia, 1003 N Mozart, 1700 N Humboldt) and 2020-01-27 (1951 N Humboldt), about $12.3M in all. 1700 N Humboldt is a 12-unit building, not HUD's 1704 N Humboldt (ILA20130003)."},
    {"id": "IHDA2020-CARLTON", "project": "Carlton Apartments",
     "address": "4626 N Magnolia Ave", "units": 68, "li_units": 68,
     "permit": ("2021-08-06", "100914415"), "category": "preserved", "resyndication_of": "ILB19930035", "sources": ["IHDA 9% 2020"],
     "note": "Mercy Housing Lakefront; supportive. Renovation converting 70 SRO units to 68 studios. HUD's 1993 record is Magnolia Arms (70 units)."},
    {"id": "IHDA2020-OAKWOOD-3-1", "project": "Oakwood Shores 3-1",
     "address": "624 E Pershing Rd", "units": 51, "li_units": 34,
     "permit": ("2022-08-05", "100947868"), "category": "new", "cha": True, "mixed_income": True, "sources": ["IHDA 9% 2020"],
     "note": "The Community Builders, Ida B. Wells redevelopment: 624 E Pershing (30 units) and 534 E 38th St (21 units, permit 100947807 of 2022-08-08); 34 affordable incl. CHA units, 17 market."},
    {"id": "IHDA2021-OAK-LARRABEE", "project": "Oak & Larrabee",
     "address": "955 N Larrabee St", "units": 78, "li_units": 54,
     "permit": ("2026-03-18", "101036007"), "category": "new", "cha": True, "mixed_income": True, "sources": ["IHDA 9% 2021"],
     "note": "Brinshore and Michaels on the former Cabrini-Green site; 54 affordable incl. CHA units. IHDA listed 90 units; the permit is for 78."},
    {"id": "IHDA2021-ALBIZU-CAMPOS", "project": "Pedro Albizu Campos Apartments",
     "address": "1203 N California Ave", "units": 64, "li_units": 64,
     "permit": ("2022-10-31", "100945137"), "category": "new", "opened": 2024, "sources": ["IHDA 9% 2021"],
     "note": "Hispanic Housing Development Corp.; IHDA calls it 1201 N. California Avenue Family Apartments. Caissons permit 2022-10-31, full permit 100959317 of 2022-12-06."},
    {"id": "IHDA2021-OGDEN-A2", "project": "Ogden Commons A-2 (The Junction)",
     "address": "1312 S Talman Ave", "units": 75, "li_units": 57,
     "permit": ("2024-09-17", "101051220"), "category": "new", "cha": True, "mixed_income": True, "sources": ["IHDA 9% 2021"],
     "note": "The Habitat Company, former Ogden Courts CHA site: 30 CHA, 27 affordable, 18 market. Caissons permit 2024-09-17, full permit 101046154 of 2024-10-02."},
    {"id": "IHDA2022-SOUTHBRIDGE-1C", "project": "Southbridge Phase 1C",
     "address": "2305 S State St", "units": 80, "li_units": 73,
     "permit": ("2025-10-16", "101070377"), "category": "new", "cha": True, "mixed_income": True, "sources": ["IHDA 9% 2022"],
     "note": "The Community Builders, Harold Ickes redevelopment: 29 CHA and 44 affordable. IHDA listed 105 units; the permit is for 80. Foundation permit 2025-10-16, full permit 101072150 of 2025-11-26."},
    {"id": "IHDA2022-EARLE-SCHOOL", "project": "Earle School Residences",
     "address": "1711 W 61st St", "units": 50, "li_units": 50,
     "permit": ("2024-06-12", "101034568"), "category": "new", "opened": 2026, "sources": ["IHDA 9% 2022"],
     "note": "Gorman & Company with the Phoenix Foundation; supportive. Conversion of the vacant Earle school. IHDA listed 63 units; the permit is for 50."},
    {"id": "IHDA2022-CALHOUN", "project": "Calhoun School Redevelopment",
     "address": "2833 W Adams St", "units": 60, "li_units": 60, "permit": None, "category": "new",
     "follow_up": True, "sources": ["IHDA 9% 2022"],
     "note": "Heartland Housing, on the former Calhoun North school (bought from CPS in 2017, with the gym and auditorium to be kept for community use). Plans went from a 64-unit school rehab (2019 application) and 51 units (LISC, 2021) to a 60-unit new building at the site (2022 award). No permit, demolition permit or news since. FOLLOW UP: possibly stalled; check whether the school building stays."},
    {"id": "IHDA2022-LECLAIRE-SOUTH", "project": "LeClaire Courts A-South",
     "address": "4442 S Cicero Ave", "units": 45, "li_units": 41,
     "permit": ("2025-10-22", "101061910"), "category": "new", "cha": True, "mixed_income": True, "sources": ["IHDA 9% 2022"],
     "note": "The A-South deal's 45 of the 73 units in LeClaire Phase A's South building (the other 28 belong to A-North; see CITY2021-LECLAIRE-NORTH). Counts are IHDA's."},
    {"id": "IHDA2022-HUMBOLDT-PASSIVE", "project": "Humboldt Park Passive Living",
     "address": "750 N Avers Ave", "units": 60, "li_units": 60,
     "permit": ("2025-01-28", "101061943"), "category": "new", "sources": ["IHDA 9% 2022"],
     "note": "548 Capital; IHDA calls it West Humboldt Park. IHDA counts 48 tax-credit units; press counts all 60 as affordable, used here."},
    {"id": "IHDA2023-CASA-YUCATAN", "project": "Casa Yucatan",
     "address": "1609 W 21st Pl", "units": 98, "li_units": 98,
     "permit": ("2024-12-17", "101050276"), "category": "new", "sources": ["IHDA 9% 2023"],
     "note": "The Resurrection Project, Pilsen. IHDA's 9% award covers 32 units of the 98-unit building. Caissons permit 2024-12-17, full permit 101053191 of 2025-05-09."},
    {"id": "IHDA2024-PIONEER-ARCADE", "project": "Pioneer Arcade Senior Apartments",
     "address": "1539 N Pulaski Rd", "units": 61, "li_units": 61,
     "permit": ("2025-12-10", "101064943"), "category": "new", "sources": ["IHDA 9% 2024"],
     "note": "Hispanic Housing Development Corp.; senior, HUD 202. IHDA calls it 1535 N. Pulaski Road Elderly. New 6-story building joined to the existing 2-story Pioneer Arcade."},
    {"id": "IHDA2024-SWOP-6301", "project": "6301 S Western",
     "address": "2355 W 63rd St", "units": 44, "li_units": 44,
     "permit": ("2025-10-07", "101067018"), "category": "new", "sources": ["IHDA 9% 2024"],
     "note": "Southwest Organizing Project; permit filed by SWOP 6301, LLC for 44 units, all affordable."},
    {"id": "IHDA2024-3959-LINCOLN", "project": "3959 N Lincoln",
     "address": "3959 N Lincoln Ave", "units": 64, "li_units": 64,
     "permit": ("2026-08-13", "101075726"), "category": "new", "sources": ["IHDA 9% 2024"],
     "note": "Brinshore, Orchard and Apna Ghar; supportive housing for survivors of gender-based violence. Reuse of the Lincoln Trust bank building plus a 5-story addition. Two IHDA deals applied 2024-03-29: a 9% deal of 22 units and a 4% deal of 42 (the 4% TEFRA notice of March 2026 says 44); the City gives 64 in all. Caissons permit 2026-08-13, full permit 101075727 of 2026-08-14."},
    {"id": "IHDA2025-WERNER", "project": "The Werner",
     "address": "7603 N Paulina St", "units": 80, "li_units": 80, "permit": None, "category": "new",
     "sources": ["IHDA 9% 2025"],
     "note": "Housing For All and Visionary Ventures at Howard and Paulina: an 8-story, 60-unit building plus 20 units in the Werner Bros. warehouse (7613 N Paulina). IHDA listed 52 units; City Council approved 80 in May 2026."},
    {"id": "IHDA2026-SARAHS-SUNNYSIDE", "project": "Sarah's on Sunnyside",
     "address": "4458 N Clarendon Ave", "units": 54, "li_units": 54, "permit": None, "category": "new",
     "sources": ["IHDA 9% 2026"], "note": "Sarah's Circle; supportive housing for women. Construction planned for 2027."},
    {"id": "IHDA2026-BELDEN", "project": "Belden Apartments",
     "address": "754 W Belden Ave", "units": 30, "li_units": 30, "permit": None, "category": "new",
     "sources": ["IHDA 9% 2026"], "note": "Over the Rainbow Association; people with disabilities, up to 30% AMI. Replaces an 8-unit building."},
    {"id": "IHDA2026-EL-ARCO", "project": "El Arco",
     "address": "2503 S Pulaski Rd", "units": 43, "li_units": 43, "permit": None, "category": "new",
     "sources": ["IHDA 9% 2026"], "note": "The Resurrection Project, Little Village. Also on the City's 2025 invited list."},
    # ---- IHDA 4% (bond) deals in Chicago not in HUD's file or on City lists:
    # the LIHTC closing reports in IHDA's FY2019-FY2025 Reports of Activities,
    # plus TEFRA notices since mid-2025 (deals too recent for a report). Each is
    # dated by its earliest rehab or construction permit.
    {"id": "IHDA4-JARRELL", "project": "Jarrell Washington Park",
     "address": "6048 S Michigan Ave", "units": 100, "li_units": 100,
     "permit": ("2018-10-05", "100759434"), "category": "preserved", "sources": ["IHDA 4% 2019"],
     "note": "6048-58 S Michigan; $1.1M systems and porch rehab. Bonds closed 2019-03-01."},
    {"id": "IHDA4-CAMPBELL-TERRACE", "project": "Campbell Terrace",
     "address": "2061 N Campbell Ave", "units": 249, "li_units": 249,
     "permit": ("2019-03-27", "100793482"), "category": "preserved", "resyndication_of": "ILA20040025", "sources": ["IHDA 4% 2019"],
     "note": "12-story senior building; $10.4M renovation. Bonds closed 2019-06-13."},
    {"id": "IHDA4-BRYN-MAWR-72ND", "project": "Bryn Mawr Apartments (South Shore)",
     "address": "1703 E 72nd St", "units": 100, "li_units": 99,
     "permit": ("2019-10-03", "100844263"), "category": "preserved", "sources": ["IHDA 4% 2019"],
     "note": "1703-1711 E 72nd St, not the Bryn Mawr/Belle Shore. Only permit is $337k of accessibility work in 24 units; same year as the 2019-11-25 bond closing."},
    {"id": "IHDA4-RAVENSWOOD-SENIOR", "project": "Ravenswood Senior Living",
     "address": "1922 W Sunnyside Ave", "units": 193, "li_units": 119,
     "permit": ("2019-11-15", "100773418"), "category": "new", "cha": True, "assisted_living": True, "sources": ["IHDA 4% 2019"],
     "note": "Evergreen and CHA: conversion of the vacant Ravenswood Hospital (4501 N Winchester) into 119 supportive living units (IHDA's tax-credit count) and 74 CHA independent senior apartments. Bonds closed 2019-12-12."},
    {"id": "IHDA4-SOUTH-CHICAGO-SALUD", "project": "South Chicago Salud Center & Senior Housing",
     "address": "3039 E 91st St", "units": 101, "li_units": 101,
     "permit": ("2020-07-29", "100868785"), "category": "preserved", "sources": ["IHDA 4% 2020"],
     "note": "$10.4M renovation of 101 existing affordable units in the former South Chicago YMCA. Bonds closed 2020-08-14."},
    {"id": "IHDA4-MAJOR-JENKINS", "project": "Major Jenkins Apartments",
     "address": "5012 N Winthrop Ave", "units": 156, "li_units": 156,
     "permit": ("2020-10-14", "100840164"), "category": "preserved", "resyndication_of": "ILB19950035", "sources": ["IHDA 4% 2020"],
     "note": "Mercy Housing Lakefront; supportive SRO, 160 rooms reconfigured to 156. $11.3M renovation. Bonds closed 2020-10-29."},
    {"id": "IHDA4-LAS-MORADAS", "project": "Las Moradas",
     "address": "1307 N California Ave", "units": 80, "li_units": 79,
     "permit": ("2020-10-20", "100895672"), "category": "preserved", "sources": ["IHDA 4% 2020"],
     "note": "1307-25 N California, existing 6-story building. Only permit is a $450k window and masonry job; same year as the 2020-10-29 bond closing."},
    {"id": "IHDA4-MARTIN-FARRELL", "project": "Martin Farrell House",
     "address": "1415 E 65th St", "units": 59, "li_units": 59,
     "permit": ("2020-11-13", "100890813"), "category": "preserved", "sources": ["IHDA 4% 2020"],
     "note": "Senior; $3M renovation. Bonds closed 2020-12-15."},
    {"id": "IHDA4-SPURLOCK-SAMPSON", "project": "Edith Spurlock Sampson Apartments",
     "address": "2640 N Sheffield Ave", "units": 405, "li_units": 405,
     "permit": ("2020-12-09", "100854944"), "category": "preserved", "cha": True, "sources": ["IHDA 4% 2021"],
     "note": "CHA senior buildings at 2640 (191 units) and 2720 N Sheffield (214 units, permit 100854945), with PIRHL. Bonds closed 2021-09-29. The new family building is IHDA4-SHEFFIELD-LINCOLN-PARK."},
    {"id": "IHDA4-MATTHEWS-SENIOR", "project": "Matthews Senior Housing",
     "address": "5040 S Indiana Ave", "units": 60, "li_units": 60,
     "permit": ("2021-01-13", "100890652"), "category": "preserved", "sources": ["IHDA 4% 2021"],
     "note": "POAH; senior. Bonds closed 2021-09-30."},
    {"id": "IHDA4-JONES-SENIOR", "project": "Jones Senior Housing",
     "address": "19 E 110th Pl", "units": 60, "li_units": 60,
     "permit": ("2021-02-09", "100890653"), "category": "preserved", "sources": ["IHDA 4% 2021"],
     "note": "POAH; senior. Bonds closed 2021-09-23."},
    {"id": "IHDA4-SHEFFIELD-LINCOLN-PARK", "project": "Sheffield of Lincoln Park",
     "address": "2700 N Sheffield Ave", "units": 80, "li_units": 60,
     "permit": ("2021-03-15", "100854947"), "category": "new", "cha": True, "mixed_income": True, "sources": ["IHDA 4% 2021"],
     "note": "New 6-story family building on the CHA Spurlock Sampson site: 50 CHA, 10 affordable, 20 market. IHDA calls it North Sheffield Development Project 2."},
    {"id": "IHDA4-NEW-VISTAS-II", "project": "New Vistas II",
     "address": "6852 S Paxton Ave", "units": 154, "li_units": 154,
     "permit": ("2021-04-03", "100916003"), "category": "preserved", "resyndication_of": "ILA20050085", "sources": ["IHDA 4% 2021"],
     "note": "Scattered South Shore buildings. No main rehab permit found; dated by the $831k roof and accessible-unit permit at 7015 S Crandon (medium confidence), same year as the 2021-08-30 bond closing."},
    {"id": "IHDA4-835-WILSON", "project": "835 Wilson",
     "address": "833 W Wilson Ave", "units": 73, "li_units": 73,
     "permit": ("2022-04-26", "100884329"), "category": "new", "sources": ["IHDA 4% 2022"],
     "note": "New 6-story independent senior building in Uptown. Bonds closed 2022-05-24."},
    {"id": "IHDA4-IKE-SIMS", "project": "Ike Sims Village",
     "address": "3333 W Maypole Ave", "units": 200, "li_units": 199,
     "permit": ("2022-05-10", "100963807"), "category": "preserved", "resyndication_of": "ILB20029001", "sources": ["IHDA 4% 2022"],
     "note": "8-story building (100 units, permit 100964760) plus eight 3-story buildings on Maypole, rehabbed May-July 2022."},
    {"id": "IHDA4-SOUTH-LAWNDALE-154", "project": "South Lawndale (Lawndale 154)",
     "address": "3324 W Douglas Blvd", "units": 154, "li_units": 154,
     "permit": ("2022-05-26", "100973701"), "category": "preserved", "sources": ["IHDA 4% 2022"],
     "note": "Unit rehabs at 3324, 3334 and 3352 W Douglas. Bonds closed 2022-07-19."},
    {"id": "IHDA4-BERRY-MANOR", "project": "Berry Manor",
     "address": "737 E 69th St", "units": 57, "li_units": 57,
     "permit": ("2022-04-01", "100941647"), "category": "preserved", "sources": ["IHDA 4% 2022"],
     "note": "The NHP Foundation; $4.3M renovation. Bonds closed 2022-09-16."},
    {"id": "IHDA4-DREXEL-LAKE-PARK", "project": "Drexel Court & Lake Park East",
     "address": "4420 S Drexel Blvd", "units": 156, "li_units": 156,
     "permit": ("2022-09-28", "100979448"), "category": "preserved", "sources": ["IHDA 4% 2022"],
     "note": "Hudson Valley Property Group: 4420, 4530 and 4700 S Drexel and 4725 S Ingleside; Section 8. Bonds closed 2022-10-25."},
    {"id": "IHDA4-CONCORDIA-PLACE", "project": "Concordia Place Apartments",
     "address": "13037 S Daniel Dr", "units": 297, "li_units": 297,
     "permit": ("2022-11-16", "100982096"), "category": "preserved", "resyndication_of": "ILB20049009", "sources": ["IHDA 4% 2022"],
     "note": "About 32 low-rise buildings on 130th St, 131st Pl, Daniel Dr and King Dr, rehabbed under permits of 2022-11-16/17. Bonds closed 2022-11-22."},
    {"id": "IHDA4-PARKWAYS", "project": "South Shore Apartments (The Parkways)",
     "address": "6718 S East End Ave", "units": 446, "li_units": 446,
     "permit": ("2023-02-01", "100971704"), "category": "preserved", "resyndication_of": "ILA20040205", "sources": ["IHDA 4% 2023"],
     "note": "Several 3-story buildings on East End rehabbed from 2023-02. Bonds closed 2023-06-07."},
    {"id": "IHDA4-SOUTH-PARK-PLAZA", "project": "South Park Plaza",
     "address": "2600 S Dr Martin Luther King Jr Dr", "units": 134, "li_units": 134,
     "permit": ("2023-07-13", "101005881"), "category": "preserved", "sources": ["IHDA 4% 2023"],
     "note": "Renovations at 2622 (2023-07-13) and 2600 S King Dr (permit 101005886). Bonds closed 2023-11-15."},
    {"id": "IHDA4-GREENWOOD-CITY-SENIOR", "project": "Greenwood City Senior Living",
     "address": "808 E 61st St", "units": 217, "li_units": 217,
     "permit": ("2023-07-14", "100983227"), "category": "preserved", "resyndication_of": "ILA20060175", "sources": ["IHDA 4% 2023"],
     "note": "Senior; $7.3M renovation. HUD's 2006 record is Greenwood Senior Living at 801 E 61st. Bonds closed 2023-09-07."},
    {"id": "IHDA4-6900-CRANDON", "project": "6900 Crandon (South Shore Crandon)",
     "address": "6900 S Crandon Ave", "units": 151, "li_units": 149,
     "permit": ("2023-11-20", "101027130"), "category": "preserved", "sources": ["IHDA 4% 2023"],
     "note": "Hispanic Housing; 10-story building, $13.2M renovation. Bonds closed 2023-12-21."},
    {"id": "IHDA4-SOUTH-SHORE-HHDC", "project": "South Shore HHDC (South Shore RYS)",
     "address": "6920 S Crandon Ave", "units": 126, "li_units": 126,
     "permit": ("2023-11-20", "101027134"), "category": "preserved", "sources": ["IHDA 4% 2023"],
     "note": "Hispanic Housing: 6920 S Crandon (65 units), 6850 S Ridgeland (21), 6800 S Ridgeland (12) and 6853 S East End (18) per the TEFRA notice. Dated by the 6920 S Crandon renovation permit, later transferred to this owner. Bonds closed 2023-12-21."},
    {"id": "IHDA4-400-LAKE-SHORE", "project": "400 Lake Shore Drive",
     "address": "400 N Lake Shore Dr", "units": 635, "li_units": 127,
     "permit": ("2023-12-06", "100960347"), "category": "new", "mixed_income": True, "sources": ["IHDA 4% 2023"],
     "note": "Related Midwest's 72-story tower (permits filed at 462 E North Water St); 127 of 635 units affordable under the bond financing. Caissons permit 2023-12-06, full permit 101041565 of 2024-08-06. Not on the City's ARO list."},
    {"id": "IHDA4-LAKEVIEW-LANDING", "project": "LakeView Landing",
     "address": "835 W Addison St", "units": 37, "li_units": 37,
     "permit": ("2023-12-12", "100978743"), "category": "new", "sources": ["IHDA 4% 2024"],
     "note": "Over the Rainbow Association; housing for people with disabilities. Bonds closed 2024-04-30."},
    {"id": "IHDA4-WILLA-RAWLS", "project": "Willa Rawls Manor",
     "address": "4120 S Indiana Ave", "units": 123, "li_units": 123,
     "permit": ("2024-08-26", "101042525"), "category": "preserved", "sources": ["IHDA 4% 2024"],
     "note": "11-story building; $4.9M rehab and accessibility work. Bonds closed 2024-09-10."},
    {"id": "IHDA4-HEART-OF-UPTOWN", "project": "Heart of Uptown Apartments",
     "address": "4431 N Clifton Ave", "units": 103, "li_units": 103,
     "permit": ("2024-12-06", "101059909"), "category": "preserved", "sources": ["IHDA 4% 2024"],
     "note": "Five buildings rehabbed 2024-12-06/10: 847 W Sunnyside (12), 4130 N Kenmore (14), 4431 N Clifton (18), 927 W Wilson (41) and 900 W Windsor (18). 927 W Wilson is HUD's 2008 Kosoh Housing (ILB20080801). Bonds closed 2024-12-20."},
    {"id": "IHDA4-DREXEL-TOWERS", "project": "Drexel Towers Apartments",
     "address": "4825 S Drexel Blvd", "units": 136, "li_units": 136,
     "permit": ("2025-07-18", "101063789"), "category": "preserved", "sources": ["IHDA 4% 2025"],
     "note": "4825 (77 units) and 4917 S Drexel (59 units, permit 101063790). Bonds issued 2025-07-31. HUD's 4915 S Drexel (1993, 31 units) is a different building."},
    {"id": "IHDA4-CONGRESSMAN-COLLINS", "project": "Congressman Collins Apartments",
     "address": "1401 S Blue Island Ave", "units": 195, "li_units": 194,
     "permit": ("2025-07-24", "101070367"), "category": "preserved", "sources": ["IHDA 4% 2025"],
     "note": "4-story senior building; $13.3M alteration. Bonds issued 2025-07-31."},
    {"id": "IHDA4-CORCORAN-PLACE", "project": "Corcoran Place",
     "address": "325 N Austin Blvd", "units": 94, "li_units": 93,
     "permit": ("2025-09-02", "101062375"), "category": "preserved", "sources": ["IHDA 4% 2025"],
     "note": "POAH; senior, Section 8. 325 N Austin (43 units) and 345 N Austin (51, permit 101062377). Bonds issued 2025-12-23."},
    {"id": "IHDA4-ELLIS-LAKEVIEW", "project": "Ellis Lakeview Apartments",
     "address": "4624 S Ellis Ave", "units": 105, "li_units": 105,
     "permit": ("2026-02-10", "101077832"), "category": "preserved", "sources": ["IHDA 4% 2026"],
     "note": "11-story building, Section 8; $14M alteration. Bonds issued 2026-05-14."},
    {"id": "IHDA4-WHITECHAPEL", "project": "Whitechapel Apartments",
     "address": "4910 N Sheridan Rd", "units": 119, "li_units": 119,
     "permit": ("2026-03-03", "101077152"), "category": "preserved", "sources": ["IHDA 4% 2026"],
     "note": "6- and 14-story buildings; $10.5M alteration. Bonds issued 2026-03-17."},
    {"id": "IHDA4-CONTINENTAL-PLAZA", "project": "Continental Plaza Apartments",
     "address": "1330 W 76th St", "units": 292, "li_units": 292,
     "permit": ("2026-07-17", "101080578"), "category": "preserved", "resyndication_of": "ILA20060150", "sources": ["IHDA 4% 2026 (TEFRA)"],
     "note": "164-unit main building plus 2-story townhome buildings on W 76th, permits July-August 2026. TEFRA hearing 2026-06-25; bond closing not yet in IHDA's July 2026 disclosure. HUD lists the 2006 deal at 1330 W 78th, likely a typo. HUD's 2006 LIHTC record counts 284 low-income, but HUD's Section 8 contract covers all 292, so all are income-restricted (decided 2026-10)."},
    {"id": "IHDA4-LOGAN-VISTAS", "project": "Logan Vistas Apartments",
     "address": "2600 N Kedzie Ave", "units": 49, "li_units": 48,
     "permit": ("2026-08-14", "101072351"), "category": "preserved", "sources": ["IHDA 4% 2026 (TEFRA)"],
     "note": "Hispanic Housing; 7-story senior building, all Section 8; 49 units including one manager's unit (TEFRA notice). Applied to IHDA 2025-03-07; TEFRA hearing 2026-05-14; bond closing not yet in IHDA's July 2026 disclosure."},
    {"id": "IHDA4-SUNNYDALE-COMMONS", "project": "Sunnydale Commons Apartments",
     "address": "810 E Hyde Park Blvd", "units": 104, "li_units": 103,
     "permit": ("2026-09-17", "101081675"), "category": "preserved", "sources": ["IHDA 4% 2026 (TEFRA)"],
     "note": "Senior (Lincoln Avenue Communities); $10.6M renovation. 104 units including one manager's unit (TEFRA notice). TEFRA hearing 2026-09-17; bond closing not yet in IHDA's July 2026 disclosure."},
    # ---- Deals in the City DOH's year-end progress reports (2019-2023 tax
    # credit and bond commitment tables) that no other source covered: mostly
    # the City's 2018 round and IHDA's 2018 awards, which predate our round
    # lists. "City DOH <year>" is the year of the City's commitment.
    {"id": "DOH-MONTCLARE-CALUMET", "project": "Montclare Senior Residences of Calumet Heights",
     "address": "9401 S Stony Island Ave", "units": 134, "li_units": 104,
     "permit": ("2018-11-29", "100760826"), "category": "new", "mixed_income": True, "sources": ["IHDA 9% 2018"],
     "note": "7-story independent senior building. IHDA 9% closed 2019-02-15."},
    {"id": "DOH-WARREN-APARTMENTS", "project": "Warren Apartments (Warren Ashland)",
     "address": "1533 W Warren Blvd", "units": 75, "li_units": 60,
     "permit": ("2019-02-20", "100773336"), "category": "new", "mixed_income": True, "sources": ["IHDA 9% 2018"],
     "note": "Heartland Housing, now POAH; 1527-33 W Warren and 1554 W Madison (the Viceroy at 1517-19 W Warren is a separate deal, ILA20120001). 25 CHA project-based vouchers (not CHA housing, so untagged). IHDA 9% closed 2019-04-24."},
    {"id": "DOH-CICERO-SENIOR-LOFTS", "project": "Cicero Senior Lofts",
     "address": "4835 S Cicero Ave", "units": 62, "li_units": 56,
     "permit": ("2019-03-13", "100790354"), "category": "new", "mixed_income": True, "sources": ["City DOH 2019"],
     "note": "MVAH Partners, 4801-57 S Cicero; City 9%."},
    {"id": "DOH-HOPE-MANOR-VILLAGE", "project": "Hope Manor Village",
     "address": "6101 S Sangamon St", "units": 36, "li_units": 36,
     "permit": ("2019-09-30", "100834007"), "category": "new", "sources": ["IHDA 9% 2019", "City DOH 2019"],
     "note": "Volunteers of America; veterans' families. 12 two-flats and 4 three-flats on Green, Peoria and Sangamon near 60th St, permitted 2019-09-30 to 10-10. IHDA 9% closed 2020-01-29."},
    {"id": "DOH-HUMBOLDT-PARK-RESIDENCE", "project": "Humboldt Park Residence",
     "address": "1152 N Christiana Ave", "units": 65, "li_units": 65,
     "permit": ("2019-08-22", "100815579"), "category": "preserved", "resyndication_of": "ILB19950025", "sources": ["City DOH 2020"],
     "note": "LUCHA; SRO, 68 units reconfigured to 65. City 4% and bonds."},
    {"id": "DOH-CASA-VERACRUZ", "project": "Casa Veracruz Apartments",
     "address": "2014 S Racine Ave", "units": 155, "li_units": 155,
     "permit": ("2020-01-27", "100854667"), "category": "preserved", "sources": ["City DOH 2020"],
     "note": "The Resurrection Project: about 15 buildings in Pilsen and Little Village (2014 S Racine, 963 W Cullerton, 967 and 1714 W 19th, 1650 S Throop, 2301 and 2700 S Drake, 4600 S Wood and others). TRP pulled rehab permits from 2018-04; dated 2020, when the tax-credit deal closed and those permits were revised to the full scope and moved to Casa Veracruz LLC (decided 2026-10)."},
    {"id": "DOH-EMMETT-STREET", "project": "Emmett Street Apartments (Lucy Gonzalez Parsons Apartments)",
     "address": "2614 N Emmett St", "units": 100, "li_units": 100,
     "permit": ("2020-08-18", "100855532"), "category": "new", "sources": ["City DOH 2020"],
     "note": "Bickerdike, by the Logan Square Blue Line; City 4% and bonds. HUD's file has it as ILB19909002 with a 1990 placed-in-service year, an error that kept it out of scope."},
    {"id": "DOH-LAKE-PARK-CRESCENT", "project": "Lake Park Crescent",
     "address": "1061 E 41st Pl", "units": 148, "li_units": 112, "mixed_income": True,
     "permit": ("2021-09-23", "100916107"), "category": "preserved", "resyndication_of": "ILA20040065", "sources": ["City DOH 2021"],
     "note": "Habitat and P3 Markets; City 4% and bonds."},
    {"id": "DOH-ENGLEWOOD-PHASE-I", "project": "Englewood Phase I",
     "address": "6100 S Halsted St", "units": 56, "li_units": 40, "permit": None, "category": "new", "mixed_income": True,
     "follow_up": True, "sources": ["City DOH 2021"],
     "note": "Keith B. Key Enterprises; 40 affordable, 16 market. City 9% committed 2021 and Council approval in 2021, but no permit or construction news since. FOLLOW UP: possibly stalled."},
    {"id": "DOH-BARBARA-JEAN-WRIGHT", "project": "Barbara Jean Wright Court Apartments",
     "address": "1350 S Morgan St", "units": 272, "li_units": 251,
     "permit": ("2022-03-21", "100936560"), "category": "preserved", "mixed_income": True, "resyndication_of": "ILB20000001", "sources": ["City DOH 2022"],
     "note": "Jonathan Rose: 27-building campus on Morgan, 14th St, 14th Pl and Maxwell, rehabbed March-August 2022. HUD's 2000 records are ILB20000001, ILB20000807 and ILB20009809. HUD tenant-protection vouchers (82 now CHA project-based), not CHA housing, so untagged. 21 units at 81-100% AMI."},
    {"id": "DOH-COVENT", "project": "Covent Apartments",
     "address": "2655 N Clark St", "units": 30, "li_units": 30,
     "permit": ("2022-06-29", "100896351"), "category": "preserved", "sources": ["City DOH 2022"],
     "note": "NHP Foundation; 64 SRO rooms rebuilt as 30 SRO units. City 4% and bonds."},
    {"id": "DOH-ROOSEVELT-SQUARE-1-REHAB", "project": "Roosevelt Square Phase 1 (rehab in the 3B deal)",
     "address": "905 S Lytle St", "units": 184, "li_units": 184,
     "permit": ("2022-08-17", "100960037"), "category": "preserved", "cha": True, "resyndication_of": "ILA20050005", "sources": ["City DOH 2022"],
     "note": "The Roosevelt Square 3B deal also reinvested in 184 existing affordable units from Phase 1 (former ABLA Homes): about 30 walk-ups on Washburne, Racine, Lytle, Taylor, Grenshaw and Roosevelt, permitted Aug-Sep 2022. The new buildings are CITY2019-ROOSEVELT-3B."},
    {"id": "DOH-AUBURN-GRESHAM", "project": "Auburn Gresham Apartments",
     "address": "753 W 79th St", "units": 58, "li_units": 58,
     "permit": ("2022-12-27", "100985136"), "category": "new", "sources": ["City DOH 2022"],
     "note": "Evergreen: 753 W 79th (30 units) and 838 W 79th (28, permit 100985140). City 4% and bonds."},
    {"id": "DOH-GRACE-MANOR", "project": "Grace Manor Apartments",
     "address": "3413 W Ogden Ave", "units": 65, "li_units": 65,
     "permit": ("2023-09-14", "100999487"), "category": "new", "sources": ["City DOH 2022"],
     "note": "East Lake Management and Grace at Jerusalem CDC, 3401-23 W Ogden; City 4% and bonds."},
    {"id": "DOH-UNITED-YARDS-1A", "project": "United Yards 1A",
     "address": "4703 S Justine St", "units": 51, "li_units": 51,
     "permit": ("2023-12-08", "100991844"), "category": "new", "opened": 2026, "sources": ["City DOH 2023"],
     "note": "Celadon and Blackwood, Back of the Yards; opened October 2026. The permit says 45 units; the City and the opening say 51. City 4% and bonds."},
    # ---- From the City's 2010-2018 year-end reports (2012 via the Wayback
    # Machine, 2018 via the Chicago Rehab Network): LIHTC commitments that HUD's
    # file lacks entirely (checked against all 1,556 Illinois records) or
    # misdates. "City DOH <year>" is the year of the City's commitment.
    {"id": "DOH-OAKWOOD-SHORES-SENIOR", "project": "Oakwood Shores Senior Apartments",
     "address": "3750 S Cottage Grove Ave", "units": 76, "li_units": 75,
     "permit": ("2010-09-02", "100361640"), "category": "new", "mixed_income": True, "sources": ["City DOH 2010"],
     "note": "The Community Builders, Ida B. Wells redevelopment; senior. The permit calls it non-assisted senior housing, so no CHA units. Bonds."},
    {"id": "DOH-OAKWOOD-TERRACE", "project": "Oakwood Terrace",
     "address": "3753 S Cottage Grove Ave", "units": 48, "li_units": 36,
     "permit": ("2011-03-23", "100385661"), "category": "new", "mixed_income": True, "sources": ["City DOH 2010"],
     "note": "Mercy, Oakwood Shores; 36 affordable, 12 market, with two floors of medical offices. City 9%."},
    {"id": "DOH-HAZEL-WINTHROP", "project": "Hazel Winthrop Apartments",
     "address": "4426 N Magnolia Ave", "units": 30, "li_units": 30,
     "permit": ("2011-05-06", "100380049"), "category": "preserved", "resyndication_of": "ILA19870012", "sources": ["City DOH 2011"],
     "note": "Community Housing Partners XV, Uptown: 4426 N Magnolia, 912 W Montrose, 4813 N Winthrop (6 units each) and 852 W Sunnyside (12), rehabbed under permits of 2011-05-06. Bonds and TIF."},
    {"id": "DOH-CHURCHVIEW-MANOR", "project": "Churchview Manor Senior Apartments",
     "address": "6250 S Talman Ave", "units": 60, "li_units": 60, "permit": None, "estimated": 2012,
     "category": "preserved", "resyndication_of": "ILB19920020", "sources": ["City DOH 2012"],
     "note": "Greater Southwest Development Corp.; senior, 2600-26 W 63rd St. No permit found; dated to the City's 2012 commitment (decided 2026-10). Separate from Churchview Supportive Living at 2626 W 63rd (CITY2021-CHURCHVIEW)."},
    {"id": "DOH-STERLING-PARK", "project": "Sterling Park Apartments",
     "address": "3301 W Arthington St", "units": 181, "li_units": 181,
     "permit": ("2013-09-19", "100482528"), "category": "new", "basis": "conversion of a non-residential building", "sources": ["City DOH 2014"],
     "cha": True,
     "note": "Mercy Housing Lakefront, Homan Square: conversion of a former Sears building; 66 CHA public housing and 115 affordable units (CHA FY2017 MTW report). City 4% and historic credits."},
    {"id": "DOH-MONTCLARE-AVALON-PARK", "project": "Montclare Senior Residences of Avalon Park",
     "address": "1210 E 78th St", "units": 122, "li_units": 109,
     "permit": ("2013-08-23", "100505125"), "category": "new", "mixed_income": True, "sources": ["City DOH 2013"],
     "note": "Senior; a 122-unit addition to the existing 102-unit building at 1200 E 78th. Foundation permit 2013-08-23, full permit 100517288 of 2013-11-13. City 9%."},
    {"id": "DOH-BRONZEVILLE-FAMILY-II", "project": "Bronzeville Family Apartments II",
     "address": "420 E 40th St", "units": 24, "li_units": 24,
     "permit": ("2013-09-20", "100453144"), "category": "new", "sources": ["City DOH 2013"],
     "note": "Bronzeville Associates: two 12-unit buildings at 420 and 440 E 40th. HUD lists it as ILB19909001 with a 1990 placed-in-service year (allocated 2013), which kept it out of scope. City 9%."},
    {"id": "DOH-WOODLAWN-PARK-SENIOR", "project": "Woodlawn Park Senior Apartments",
     "address": "6134 S Cottage Grove Ave", "units": 65, "li_units": 65,
     "permit": ("2014-05-19", "100510467"), "category": "new", "sources": ["City DOH 2014"],
     "note": "POAH; senior, 6116-44 S Cottage Grove. City 9%."},
    {"id": "DOH-ROSENWALD-COURTS", "project": "Rosenwald Courts",
     "address": "4600 S Michigan Ave", "units": 239, "li_units": 239,
     "permit": ("2014-12-23", "100559028"), "category": "new", "basis": "rehab of vacant buildings", "sources": ["City DOH 2013"],
     "cha": True,
     "note": "The vacant Rosenwald Apartments complex: 120 senior and 86 family units at 4639 S Wabash (permit 100575841 of 2014-12-30) plus 33 at 4600 S Michigan; 60 of the senior units are CHA public housing (CHA FY2016 MTW report). City 4% and historic credits; TIF."},
    {"id": "DOH-MONTCLARE-LAWNDALE", "project": "Montclare Supportive Living of Lawndale",
     "address": "4339 W 18th Pl", "units": 120, "li_units": 108, "mixed_income": True,
     "permit": ("2015-07-27", "100606209"), "category": "new", "assisted_living": True, "sources": ["City DOH 2015"],
     "note": "MR Properties; Supportive Living Facility. City 9%."},
    {"id": "DOH-ST-EDMUNDS-OASIS", "project": "St. Edmund's Oasis",
     "address": "6118 S Prairie Ave", "units": 58, "li_units": 58,
     "permit": ("2015-09-29", "100578842"), "category": "new", "sources": ["City DOH 2015"],
     "cha": True,
     "note": "Four new buildings permitted 2015-09-29: 6118 S Prairie (27), 6153 S Indiana (14), 310 E 61st (12) and 223 E 61st (5); 19 CHA public housing and 39 affordable units, Washington Park replacement housing (CHA FY2017 MTW report). City 4%."},
    {"id": "DOH-LAWN-TERRACE", "project": "Lawn Terrace Apartments",
     "address": "3214 W 63rd Pl", "units": 102, "li_units": 102,
     "permit": ("2016-02-05", "100611489"), "category": "preserved", "resyndication_of": "ILB19970800", "sources": ["City DOH 2016"],
     "note": "Greater Southwest Development Corp.; senior. $3.2M interior renovation. City 4%."},
    {"id": "DOH-EL-ZOCALO", "project": "El Zócalo",
     "address": "3246 W 47th St", "units": 30, "li_units": 30,
     "permit": ("2016-11-29", "100627244"), "category": "new", "sources": ["City DOH 2016"],
     "note": "UP Development, Brighton Park; 6-story mixed-use. City 9%."},
    {"id": "DOH-WOODLAWN-STATION", "project": "Woodlawn Station",
     "address": "6253 S Cottage Grove Ave", "units": 70, "li_units": 55,
     "permit": ("2017-02-09", "100671202"), "category": "new", "mixed_income": True, "sources": ["City DOH 2016"],
     "note": "POAH, Choice Neighborhoods: 55 units at 63rd and Cottage Grove plus 15 at 6408 and 6432 S Maryland built to meet the ARO (not on the City's ARO list). 55 units at or under 60% AMI, 15 at 61-80%."},
    {"id": "DOH-LATHROP-1A", "project": "Lathrop Homes Phase 1A",
     "address": "2000 W Diversey Pkwy", "units": 413, "li_units": 252,
     "permit": ("2017-05-25", "100648854"), "category": "new", "basis": "rehab of vacant buildings and new construction",
     "cha": True, "mixed_income": True, "sources": ["City DOH 2017"],
     "note": "Lathrop Community Partners: vacant Lathrop buildings on Diversey and Hoyne rehabbed (permits from 2017-05-25) plus a new 59-unit building at 2737 N Hoyne (permit 100684005). 252 CHA and affordable, 161 market. Classified like Phase 1D (decided 2026-10)."},
    {"id": "DOH-MARK-TWAIN", "project": "Mark Twain Apartments",
     "address": "111 W Division St", "units": 148, "li_units": 148,
     "permit": ("2017-10-17", "100717510"), "category": "preserved", "sources": ["City DOH 2018"],
     "note": "NHP Foundation; SRO, 151 rooms reconfigured to 148. City 4%."},
    {"id": "DOH-PENNYCUFF", "project": "John Pennycuff Memorial Apartments",
     "address": "2025 N Milwaukee Ave", "units": 88, "li_units": 88,
     "permit": ("2017-11-02", "100711521"), "category": "new", "sources": ["City DOH 2017"],
     "note": "Metropolitan Housing Development Corp., Logan Square; 7-story. City 4%."},
    {"id": "DOH-MONTCLARE-ENGLEWOOD", "project": "Montclare Senior Residences of Englewood",
     "address": "6342 S Green St", "units": 102, "li_units": 102,
     "permit": ("2018-11-13", "100742141"), "category": "new", "sources": ["City DOH 2017"],
     "note": "MR Properties; 7-story independent senior building, 6320-52 S Green. City 9%."},
    # ---- The other two buildings of the Thresholds RAD 2 deal (HUD's
    # ILA20190017 has only 6808 N Wayne). IHDA FY2018 4% closing of
    # 2017-12-28: 146 units, 146 LIHTC, at 334 N Menard, 6808 N Wayne and
    # 500 W Englewood. One record per building so each ward gets its own.
    {"id": "IHDA4-THRESHOLDS-RAD2-AUSTIN", "project": "Thresholds RAD 2: Austin Apartments",
     "address": "334 N Menard Ave", "units": 57, "li_units": 57,
     "permit": ("2017-12-13", "100717686"), "category": "preserved", "resyndication_of": "ILB19950040", "sources": ["IHDA 4% 2017"],
     "note": "Thresholds supportive SRO, 57 units; $2.25M renovation permit of 2017-12-13. Part of the 146-unit Thresholds RAD 2 deal (see ILA20190017). CHA project-based vouchers on all 57."},
    {"id": "IHDA4-THRESHOLDS-RAD2-ROWAN", "project": "Thresholds RAD 2: Rowan Trees Apartments",
     "address": "500 W Englewood Ave", "units": 45, "li_units": 45,
     "permit": ("2017-12-13", "100717633"), "category": "preserved", "resyndication_of": "ILB19970035", "sources": ["IHDA 4% 2017"],
     "note": "Thresholds supportive SRO, 45 units; $2.25M renovation permit of 2017-12-13. Part of the 146-unit Thresholds RAD 2 deal (see ILA20190017). CHA project-based vouchers on all 45."},
    # ---- IHDA bond deals missing from HUD's file. Found 2026-10 via IHDA's
    # FY2018 closing report and its list of outstanding conduit bonds.
    {"id": "IHDA4-CONCORD-SHERIDAN", "project": "The Concord at Sheridan",
     "address": "6438 N Sheridan Rd", "units": 111, "li_units": 65, "mixed_income": True,
     "permit": ("2017-09-19", "100716069"), "category": "new", "sources": ["IHDA 4% 2017"],
     "note": "Three Corners / Habitat, Rogers Park: new 7-story building, 111 units over a Target; IHDA 4% closing 2017-11-02 with 65 LIHTC units (IHDA lists 6418 N Sheridan), 46 market. CHA project-based vouchers on the 65. Previously misfiled as City-subsidized without credits."},
    {"id": "IHDA4-WEST-TOWN-PRESERVATION", "project": "West Town Housing Preservation",
     "address": "2550 W North Ave", "units": 318, "li_units": 315, "permit": None, "estimated": 2017,
     "category": "preserved", "resyndication_of": "ILA19880076", "sources": ["IHDA 4% 2017"],
     "note": "Bickerdike: 318 units in townhomes and small buildings scattered from Ashland to Kedzie, Division to North, all at or under 60% AMI. IHDA 4% closing 2017-07-14 (315 LIHTC); moderate rehab finished 2019. Owner permits begin 2017-04-28 (porches at 1910 W Crystal, 1023 N Rockwell, 2656 W Thomas, 1941 N Humboldt, 1901 N Richmond); interiors had no large permits, so the year is estimated. Placed at the owner's address on W North Ave; its units span several wards."},
    {"id": "IHDA4-PETERSON-PLAZA", "project": "Peterson Plaza",
     "address": "5969 N Ravenswood Ave", "units": 189, "li_units": 189,
     "permit": ("2015-08-06", "100608208"), "category": "preserved", "sources": ["IHDA bonds 2015"],
     "note": "Related's 2015 Illinois preservation portfolio: 189 units (an 11-story senior tower of 101 and low-rise family buildings), built 1982, Section 8. IHDA bonds 2015-08-11; $5.6M renovation permit 2015-08-06; $10M renovation celebrated 2016-09-08. Listings show LIHTC units at 50% AMI. HUD has no record, and IHDA's closing report for that year is not online, so the LIHTC count is not confirmed (medium confidence, decided 2026-10)."},
    # ---- City-financed tax-credit deals closed 2024-26, found 2026-10. The
    # City's year-end reports stop at 2023 and IHDA's bond notices cover only
    # bonds IHDA issues, so these were missed.
    {"id": "CITYPL-INDIAN-TRAILS", "project": "Indian Trails Apartments",
     "address": "221 E 121st Pl", "units": 180, "li_units": 179,
     "permit": ("2025-12-16", "101075476"), "category": "preserved", "sources": ["City ordinance 2025 (bonds)", "Building permits 2025"],
     "note": "Existing 180-unit Section 8 complex in West Pullman, bought by Transcend Development Group (January 2026 press) for a $25M full rehab financed with 4% LIHTC, tax-exempt bonds and City money. Three renovation permits of 2025-12-16, 60 units each: 221 E 121st Pl, 251 E 121st Pl (101075479) and 12141 S Indiana (101075482). 179 affordable of 180."},
    {"id": "CITYPL-THE-AVE", "project": "The Ave",
     "address": "3601-25 W Chicago Ave", "units": 52, "li_units": 52,
     "permit": ("2025-03-13", "101057754"), "category": "new", "sources": ["City ordinances 2024 (bonds, TIF)"],
     "note": "POAH, Humboldt Park: new 4-story building of 52 units at 30-60% AMI over two commercial spaces (permit at 3607 W Chicago). City-issued tax-exempt bonds, 4% LIHTC and $18M TIF."},
    {"id": "CITYPL-CARE-MANOR", "project": "C.A.R.E. Manor",
     "address": "4531-55 W Washington Blvd", "units": 44, "li_units": 44,
     "permit": ("2025-11-13", "101045789"), "category": "new", "sources": ["City ordinances 2023-24 (bonds, TIF)"],
     "note": "United for Better Living, West Garfield Park: new 3-story walk-up of 44 affordable units (15-60% AMI). 4% LIHTC equity, bonds and $10M TIF (CDC approval); groundbreaking with the mayor."},
    {"id": "CITYPL-THRIVE-EXCHANGE", "project": "Thrive Exchange",
     "address": "7905 S Exchange Ave", "units": 43, "li_units": 43,
     "permit": ("2024-11-27", "101054966"), "category": "new", "opened": 2026, "sources": ["City ordinances 2023-24 (bonds, land, TIF)"],
     "note": "DL3 Realty, South Chicago (INVEST South/West): new 6-story building of 43 units at 60% AMI over retail; $15.1M LIHTC equity, $13.1M TIF, a $6.3M Chicago Recovery Plan loan and a City lot. Completed June 2026."},
    # Awarded City financing, no construction permit yet (checked 2026-10-19
    # against City ordinances and the building-permit data):
    {"id": "CITYPL-111-W-MONROE", "project": "111 W Monroe (LaSalle Reimagined)",
     "address": "111 W Monroe St", "units": 345, "li_units": 104, "mixed_income": True, "permit": None,
     "category": "new", "basis": "conversion of an office building",
     "sources": ["City ordinance SO2025-0016002"],
     "note": "The Prime Group and Capri: office-to-apartment conversion, 104 affordable units at or under 80% AMI (at least 40% of them at or under 60%) and 241 market-rate. City bonds (up to $40M), TIF (up to $40M) and 4% LIHTC (the ordinance's financing exhibit includes a LIHTC bridge loan); approved March 2025. Only interior-demolition permits so far (2024-11, 2026-04)."},
    {"id": "CITYPL-208-S-LASALLE", "project": "208 S LaSalle (LaSalle Reimagined)",
     "address": "208 S LaSalle St", "units": 226, "li_units": 68, "mixed_income": True, "permit": None,
     "category": "new", "basis": "conversion of an office building",
     "sources": ["City ordinance SO2025-0016003"],
     "note": "The Prime Group: office-to-apartment conversion with the same bond, TIF and tax-credit structure as 111 W Monroe (an affordable LP owning the affordable units). The March 2025 ordinance described 51 affordable of 168; updated 2026-10 to 68 of 226, used here. Interior-demolition permit only (2026-05-28)."},
    {"id": "CITYPL-TEACHERS-VILLAGE", "project": "Teachers Village (Humboldt Village)",
     "address": "2620 W Hirsch St", "units": 103, "li_units": 103, "cha": True, "permit": None,
     "category": "new", "basis": "conversion of a vacant school",
     "sources": ["City ordinances July 2026 (TCB Humboldt 4 and 9)"],
     "note": "The Community Builders: the vacant Von Humboldt school (1417 N Talman / 2620 W Hirsch) converted to rental units, all at or under 80% AMI, including CHA RAD project-based replacement units (number not stated); a 9% deal of about 28 units and a 4% bond deal for the rest. City loans approved July 2026; about 103 units. No permit yet."},
    {"id": "CITYPL-LEVY-HOUSE", "project": "Levy House", "address": "1221 W Sherwin Ave",
     "units": 57, "li_units": 57, "permit": None, "category": "preserved", "resyndication_of": None,
     "sources": ["City ordinance O2026-0026255 (in committee)"],
     "note": "POAH: 8-story building of 57 apartments for low- and very-low-income seniors, bought in 2020 with a City HOME loan (purchase only, not counted). A 2023 bond inducement and a June 2026 ordinance (still in committee) for up to $15M in bonds, 4% LIHTC and City funds for a roughly $22M renovation. No permit yet."},
    {"id": "CITYPL-LAWNDALE-REDEFINED", "project": "Lawndale ReDefined",
     "address": "3400-3436 W Ogden Ave", "units": 59, "li_units": 48, "mixed_income": True, "permit": None,
     "category": "new", "follow_up": True,
     "sources": ["INVEST South/West"],
     "note": "Griggs Real Estate Ventures, 548 Development and Imagine Group: new 6-story building on vacant land, 48 affordable of 59, with a requested 9% LIHTC allocation and $1.8M TIF. Rezoning approved July 2026; no tax-credit award found yet. FOLLOW UP."},
]

# Projects found during research that are probably LIHTC but can't be placed
# yet. Not counted anywhere; printed on each run so they don't get lost.
HELD = [
    {"project": "Western Gunnison Apartments (Related Midwest)", "address": "Lincoln Square", "units": 0,
     "note": "On the City's 2021 Stage 1 approval list only. No address, unit count, permit, later award list or news found as of 2026-10; removed from the awarded set."},
    {"project": "Winthrop Argyle Apartments (Related Midwest)", "address": "Uptown", "units": 0,
     "note": "Preservation, on the City's 2021 Stage 1 approval list only. No address, unit count, permit, later award list or news found as of 2026-10; removed from the awarded set."},
    {"project": "Fullerton Court Apartments (Related)", "address": "1543 W Fullerton Ave", "units": 196,
     "note": "LIHTC in 2000 (HUD ILA20000025, pre-series). IHDA bonds 2015-01 and a $6M renovation permit of 2015-01-16 (100568759), but no sign the 2015 deal took new credits. Held 2026-10."},
    {"project": "Lafayette Plaza Housing Cooperative", "address": "50 W 71st St", "units": 196,
     "note": "Section 8 co-op. Applied to IHDA for 4% LIHTC and bonds 2022-06-06; $18.45M renovation permit 2023-01-20 (100980636). No IHDA closing in its FY2022-2025 reports and no IHDA bond outstanding. Held 2026-10."},
]

# City- and State-funded income-restricted rental with no federal tax
# credits: City multifamily loans, TIF and land, from the City's year-end
# housing reports (2010-2023) and later announcements, plus IHDA-funded
# projects from its closing reports (HOME, trust funds, donation credits). Shown on the page as a third
# program. Units are the restricted units only (the City's income-band
# columns; market-rate "over 100%" units left out). Same new/preserved rule
# as LIHTC: an existing residential building rehabbed is preserved; new
# construction, or a building that was not housing before, is new.
# Decided 2026-10: refinancing-only deals are left out (no units built or
# rehabbed), and state-donation-credit deals with City land or loans are in.
# Decided 2026-10-19: a deal that puts new income limits on existing homes
# counts as preserved even with no renovation (it adds restricted units); a
# refinancing or transfer of an already-restricted building still does not.
# Only units at or under 80% AMI are counted. Where a source gives units
# restricted at 81-120% AMI, they are kept in "over80" (not counted) so the
# line can be revisited.
CITY_FUNDED = [
    {"id": "CITYF-CONSERVATORY", "project": "Conservatory Apartments",
     "address": "414 N Central Park Ave", "units": 43, "li_units": 43,
     "permit": ("2022-10-11", "100935932"), "category": "new", "sources": ["City DOH 2021 (TIF)"],
     "note": "Interfaith Housing; 43 supportive SRO units for women and families leaving homelessness (22 at or under 30% AMI, 21 at or under 50%). Passive House; opened October 2024."},
    {"id": "CITYF-SARAHS-LAKESIDE", "project": "Sarah's on Lakeside",
     "address": "4737 N Sheridan Rd", "units": 28, "li_units": 28,
     "permit": ("2022-08-22", "100935466"), "category": "new", "sources": ["City DOH 2022 (loan)"],
     "note": "Sarah's Circle; new 5-story building of 28 SRO units for women, 14 at or under 30% AMI and 14 at or under 60%. Illinois donation credits, no federal credits."},
    {"id": "CITYF-ENGLEWOOD-FAMILY", "project": "Englewood Family Housing",
     "address": "1650 W 63rd St", "units": 13, "li_units": 13,
     "permit": ("2021-10-19", "100893527"), "category": "preserved", "sources": ["City DOH 2021 (loan)"],
     "note": "Chicago House; renovation of an existing 13-unit residential building (permit at 1654 W 63rd), supportive."},
    {"id": "CITYF-LAZARUS", "project": "Lazarus Apartments (Pulaski building)",
     "address": "1859 S Pulaski Rd", "units": 33, "li_units": 33,
     "permit": ("2021-12-15", "100913840"), "category": "preserved", "sources": ["City DOH 2021 (loan)"],
     "note": "Lawndale Christian Development Corp.; interior rehab and new roof of an existing 33-unit building, 18 at or under 50% AMI and 15 at or under 60%."},
    {"id": "CITYF-SARAHS-SHERIDAN", "project": "Sarah's on Sheridan",
     "address": "1005 W Leland Ave", "units": 38, "li_units": 38,
     "permit": ("2019-06-20", "100769143"), "category": "new", "sources": ["City DOH 2019 (TIF)", "IHDA IAHTC 2019"],
     "note": "Sarah's Circle; new 6-story building with 38 SRO apartments for women plus a 50-bed interim shelter floor. $3.5M TIF and Illinois donation credits."},
    {"id": "CITYF-LA-HERENCIA", "project": "La Herencia (Humboldt Park United Methodist Church)",
     "address": "2120 N Mozart St", "units": 22, "li_units": 22,
     "permit": ("2025-06-23", "100999300"), "category": "split", "split": {"preserved": 13, "new": 9},
     "sources": ["City 2025 (loan, HOME)"],
     "note": "LUCHA: 13 existing apartments renovated and the sanctuary converted to 9 more, 22 in all; groundbreaking December 2025. Applied for IHDA bonds in FY2024 but did not use tax credits."},
    {"id": "CITYF-RENAISSANCE-WABASH", "project": "Renaissance Apartments (Wabash YMCA)",
     "address": "3757 S Wabash Ave", "units": 101, "li_units": 101,
     "permit": ("2021-11-03", "100924556"), "category": "preserved", "sources": ["City DOH 2023 (TIF)"],
     "note": "The Renaissance Collaborative; 101 SRO units in the historic Wabash YMCA, 100 at or under 30% AMI. $4.5M alteration permit; $5.3M TIF committed 2023."},
    {"id": "CITYF-NAOMI-SMITH", "project": "Naomi & Sylvester Smith Senior Living Center",
     "address": "8031 S Halsted St", "units": 60, "li_units": 60,
     "permit": ("2010-09-22", "100364564"), "category": "new", "sources": ["City DOH 2010 (loan)"],
     "note": "New Pisgah Missionary Baptist Church; new 5-story, 60-unit affordable senior building (City lists 8019 S Halsted)."},
    {"id": "CITYF-MARX", "project": "Edward M. Marx Apartments",
     "address": "5801 N Pulaski Rd", "units": 31, "li_units": 31,
     "permit": ("2011-01-12", "100368780"), "category": "new", "basis": "conversion of a City-owned campus building",
     "sources": ["City DOH 2010 (loan, land)"],
     "note": "EHDOC; HUD Section 202 senior housing in Building F of the City-owned North Park Village campus, converted to 31 apartments at or under 50% AMI with HUD rental assistance."},
    {"id": "CITYF-NEW-MOMS", "project": "New Moms",
     "address": "5317 W Chicago Ave", "units": 40, "li_units": 40,
     "permit": ("2011-10-19", "100401494"), "category": "new", "sources": ["City DOH 2011 (loan)"],
     "note": "New Moms, Inc., Austin; new 4-story building of 40 supportive units for young mothers (City lists 5327 W Chicago)."},
    {"id": "CITYF-ALL-SAINTS", "project": "All Saints Residence",
     "address": "11701 S State St", "units": 42, "li_units": 42,
     "permit": ("2011-09-23", "100410694"), "category": "new", "sources": ["City DOH 2011 (loan, land)"],
     "note": "Catholic Charities; new 5-story wing of 42 HUD Section 202 senior apartments on the Roseland Manor campus (City lists 17 E 117th St); opened 2013."},
    {"id": "CITYF-SARAHS-HOUSE", "project": "Sarah's House",
     "address": "4836 N Sheridan Rd", "units": 10, "li_units": 10,
     "permit": ("2012-07-13", "100424330"), "category": "preserved", "sources": ["City DOH 2012 (loan)"],
     "note": "Sarah's Circle; alterations and additions to an existing 3-story building for 10 supportive units for women. Prior use not confirmed, so counted as preserved; low confidence."},
    {"id": "CITYF-PORTA-COELI", "project": "Porta Coeli Senior Residence",
     "address": "2260 E 99th St", "units": 86, "li_units": 86,
     "permit": ("2013-04-02", "100484038"), "category": "new", "sources": ["City DOH 2013 (loan)"],
     "note": "New 4-story, 86-unit senior building, South Chicago."},
    {"id": "CITYF-WEST-HUMBOLDT-PLACE", "project": "West Humboldt Place",
     "address": "3543 W Chicago Ave", "units": 13, "li_units": 13,
     "permit": ("2014-04-01", "100510436"), "category": "new", "sources": ["City DOH 2013 (TIF)"],
     "note": "The Children's Place Association; new 3-story building of 13 units for families affected by HIV and other illness (City lists 3533-45 W Chicago)."},
    {"id": "CITYF-MAGNOLIA-PARK", "project": "Magnolia Park Apartments",
     "address": "4878 N Magnolia Ave", "units": 60, "li_units": 50, "mixed_income": True,
     "permit": None, "estimated": 2016, "category": "preserved", "sources": ["City DOH 2016 (loan)"],
     "note": "Existing 60-unit courtyard building (1906), Uptown; City loan 2016 for 50 units at or under 60% AMI and 10 market. No rehab permit on record, so dated to the City's commitment."},
    {"id": "CITYF-TRIANON-LOFTS", "project": "Trianon Lofts",
     "address": "803 E 61st St", "units": 24, "li_units": 12, "mixed_income": True,
     "permit": ("2016-07-11", "100584422"), "category": "new", "sources": ["City DOH 2016 (loan)"],
     "note": "POAH, Woodlawn; new 4-story building, 24 units over retail: 5 at or under 50% AMI, 7 at or under 80%, 12 market."},
    {"id": "CITYF-NEW-WEST-ENGLEWOOD", "project": "New West Englewood Homes",
     "address": "2101 W 63rd St", "units": 12, "li_units": 12,
     "permit": ("2017-06-13", "100691670"), "category": "new", "sources": ["City DOH 2017 (loan)"],
     "note": "Interfaith Housing; two new 3-story, 6-unit buildings at 2101-11 W 63rd, 6 at or under 50% AMI and 6 at or under 60%."},
    {"id": "CITYF-PIERCE-HOUSE", "project": "La Casa Norte: Pierce House",
     "address": "3527 W North Ave", "units": 25, "li_units": 25,
     "permit": ("2017-06-12", "100688121"), "category": "new", "sources": ["City DOH 2017 (loan, TIF)"],
     "note": "La Casa Norte; new 5-story building of 25 supportive units for young people and families, at or under 30% AMI, with a community center (City lists 3533 W North)."},
    {"id": "CITYF-LINCOLN-PARK-SHELTER", "project": "Lincoln Park Community Shelter",
     "address": "1521 N Sedgwick St", "units": 20, "li_units": 20,
     "permit": ("2017-12-22", "100731109"), "category": "new", "sources": ["City DOH 2017 (loan, TIF)"],
     "note": "Lincoln Park Community Services; a new 5-story, 20-unit apartment building (5 at or under 30% AMI, 15 at or under 50%) beside its renovated interim-housing building."},
    # From IHDA's closing reports (FY2018-2025), non-tax-credit programs:
    # projects the state funded without federal credits or City money. Added
    # 2026-10 after a sweep of IHDA's HOME, Trust Fund, NHTF, donation-credit
    # and Capital Bill closings; capital-repair grants, refinancing and
    # for-sale homes from those tables are left out.
    {"id": "CITYF-OGDEN-PARK", "project": "Ogden Park Apartments",
     "address": "6705 S Elizabeth St", "units": 25, "li_units": 25,
     "permit": ("2020-05-22", "100868243"), "category": "new", "basis": "conversion of a convent",
     "sources": ["IHDA Trust Fund 2019", "IHDA NHTF 2020"],
     "note": "Interfaith Housing; the closed St. Brendan's convent converted to 25 studios of permanent supportive housing for formerly homeless adults and people on the State Referral Network; services by Trilogy; placed in service 2021."},
    {"id": "CITYF-CRESSEY-HOUSE", "project": "Cressey House (ReVive Center)",
     "address": "1668 W Ogden Ave", "units": 28, "li_units": 28,
     "permit": ("2024-04-16", "101007904"), "category": "preserved", "sources": ["IHDA NHTF 2023", "IHDA IAHTC 2024"],
     "note": "ReVive Center for Housing and Healing; renovation of its existing 28 units of permanent supportive housing for chronically homeless people with disabilities and their children."},
    {"id": "CITYF-RECLAIMING-SW-III", "project": "Reclaiming Southwest III",
     "address": "6000 S Talman Ave", "units": 10, "li_units": 3,
     "permit": ("2024-04-18", "101020982"), "category": "preserved",
     "sources": ["IHDA IAHTC 2024"],
     "note": "SWOP; complete renovation of an existing 10-unit building, Chicago Lawn. Illinois donation credits on 3 units (IHDA FY2024); only those are counted as restricted. Preserved: unlike the first Reclaiming Southwest phase, no source documents the building as vacant (decided 2026-10-19)."},
    # Found 2026-10 (2024-26 deals, after
    # the City's last year-end report):
    {"id": "CITYF-SAN-MIGUEL", "project": "San Miguel Apartments",
     "address": "907 W Argyle St", "units": 71, "li_units": 53, "mixed_income": True,
     "permit": ("2026-03-10", "101073826"), "category": "preserved", "sources": ["IHDA IAHTC 2025"],
     "note": "Argyle Neighborhood Development Corp., Uptown: City sale/transfer and debt forgiveness keep 53 of 71 units restricted 15 more years (18 at 50% AMI, 35 at 80%); Illinois donation credits ($1.5M, March 2025). $3M renovation of 57 units, permit 2026-03-10."},
    {"id": "CITYF-VILLA-GUADALUPE", "project": "Villa Guadalupe Senior Apartments",
     "address": "3201 E 91st St", "units": 53, "li_units": 53,
     "permit": ("2026-09-29", "101084500"), "category": "preserved", "sources": ["City ordinance 2026 (TIF)", "Building permits 2026"],
     "note": "Existing 5-story, 53-unit senior building, South Chicago; City financing with an IHDA refinancing; $10M interior and exterior renovation, permit 2026-09-29."},
    # Found 2026-10 in the wider search (City year-end reports, the City's
    # affordable rental list, Council ordinances and IHDA's 2025 report):
    {"id": "CITYF-ST-EDMUNDS-ANNEX", "project": "St. Edmund's Tower Annex",
     "address": "6151 S Michigan Ave", "units": 34, "li_units": 34,
     "permit": ("2014-09-23", "100539015"), "category": "new", "sources": ["City DOH 2014 (land, IAHTC)"],
     "note": "St. Edmund's Redevelopment Corp., Washington Park: new 5-story building of 34 one-bedroom apartments for seniors 62+, all at 31-50% AMI, beside the 1987 St. Edmund's Tower. City land write-down and Illinois donation credits (City 2014 report; closed 2014-09-26); completed January 2017."},
    {"id": "CITYF-BRONZEVILLE-ARTIST-LOFTS", "project": "Bronzeville Artists Lofts",
     "address": "436 E 47th St", "units": 16, "li_units": 8, "over80": 8, "mixed_income": True,
     "permit": ("2012-11-23", "100443844"), "category": "new", "basis": "rehab of a long-vacant commercial building",
     "sources": ["City DOH 2012 (NSP2, TIF)"],
     "note": "Revere Properties and Three Corners: $5.9M renovation of a vacant 1920s commercial building into 16 artist live-work apartments over 12,200 sq ft of gallery and retail; $4.4M City NSP2 grant and TIF (redevelopment agreement 2013-04-10); opened 2014. Half the units at or under 60% AMI (counted), half at or under 120% AMI (over80, not counted)."},
    {"id": "CITYF-AUGUSTA-HOME", "project": "Augusta Home (Chicago House)",
     "address": "2544 W Augusta Blvd", "units": 12, "li_units": 12,
     "permit": ("2026-06-26", "101078566"), "category": "preserved", "sources": ["IHDA PSH Round XI 2025 (IAHTF)"],
     "note": "Chicago House and Social Service Agency, West Town: gut renovation of its existing 3-story group home (16 sleeping rooms) into 12 units of permanent supportive housing for seniors living with HIV/AIDS, all at or under 30% AMI. $5.15M Illinois Affordable Housing Trust Fund (IHDA, May 2025); $4.4M permit 2026-06-26."},
    {"id": "CITYF-HOMEKEEP", "project": "Homekeep (Communities United)",
     "address": "2537 N Lowell Ave", "units": 8, "li_units": 8,
     "permit": None, "estimated": 2024, "category": "preserved", "basis": "existing homes newly restricted",
     "sources": ["City ordinances O2023-0006387, O2024-0008343 (PEAR loan)"],
     "note": "City Preservation of Existing Affordable Rentals (PEAR) loan of up to $1M (approved December 2023 to Duo Development, reassigned April 2024 to Homekeep LLC) to buy three small buildings, 2537 N Lowell (Hermosa), 2904 N Linder (Belmont Cragin) and 4510 N Central Park (Albany Park), 8 units in all, and restrict every unit at or under 80% AMI for 30 years. No renovation permits, so dated to the loan; placed at the first address."},
    {"id": "CITYF-RESURRECTION-HOMES", "project": "Resurrection Homes Rental Project",
     "address": "1910 S Albany Ave", "units": 7, "li_units": 7,
     "permit": None, "estimated": 2012, "category": "preserved", "basis": "existing homes newly restricted",
     "sources": ["City DOH 2011 (loan)"],
     "note": "The Resurrection Project: seven unsold New Homes for Chicago units in three buildings (1910 S Albany, 2243 S Central Park, 2124 W 19th St) converted to rentals at 50% AMI with a $780k City loan (approved 2011-12-14, closed 2012-04-05). No construction, so dated to the closing; placed at the first address."},
    {"id": "CITYF-79-W-MONROE", "project": "79 W Monroe (LaSalle Reimagined)",
     "address": "79 W Monroe St", "units": 117, "li_units": 41, "mixed_income": True,
     "permit": ("2025-04-08", "101062067"), "category": "new", "basis": "conversion of an office building",
     "sources": ["City ordinance SO2024-0012437 (TIF)"],
     "note": "Brown Derby LLC and R2: floors 7-14 of the Weatherbell Building converted from offices to 117 apartments, 41 affordable (7 each at 40%, 50%, 70% and 80% AMI and 13 at 60%) and 76 market-rate. City TIF grant up to $28M plus federal historic credits (no LIHTC). Change-of-use permit 2025-04-08 ($42M)."},
    # LaSalle Reimagined and INVEST South/West deals approved by the City but
    # not yet permitted: shown in the pipeline, not counted.
    {"id": "CITYF-135-S-LASALLE", "project": "Field Building, 135 S LaSalle (LaSalle Reimagined)",
     "address": "135 S LaSalle St", "units": 386, "li_units": 116, "mixed_income": True, "permit": None,
     "category": "new", "basis": "conversion of an office building",
     "sources": ["City ordinance SO2025-0018762 (TIF)"],
     "note": "Field Building Revitalization Partners: 386 apartments in the Field Building, 116 affordable at 40-80% AMI and 270 market-rate; TIF up to $98M and federal historic credits (no LIHTC). Approved July 2025, amended 2026. No permit yet."},
    {"id": "CITYF-30-N-LASALLE", "project": "30 N LaSalle (LaSalle Reimagined)",
     "address": "30 N LaSalle St", "units": 349, "li_units": 105, "mixed_income": True, "permit": None,
     "category": "new", "basis": "conversion of an office building",
     "sources": ["City ordinances December 2025 and SO2026-0027303 (TIF)"],
     "note": "30 N LaSalle MF Redevelopment: floors 2-18 converted to 349 apartments, 105 affordable (weighted average 60% AMI, a third at or under 50%) and 244 market-rate; City TIF. Window-replacement permit 2026-07 only."},
    {"id": "CITYF-105-W-ADAMS", "project": "Clark Adams Building, 105 W Adams (LaSalle Reimagined)",
     "address": "105 W Adams St", "units": 400, "li_units": 121, "mixed_income": True, "permit": None,
     "category": "new", "basis": "conversion of an office building",
     "sources": ["City ordinance SO2025-0018754 (TIF)"],
     "note": "105 Adams Development: about 400 apartments, 121 affordable (30%); TIF forgivable loan up to $67.5M and federal historic credits (no LIHTC). Approved July 2025. The ordinance's figures are used. No permit yet."},
    {"id": "CITYF-GALLERIA-89", "project": "Galleria 89", "address": "8840-8854 S Commercial Ave",
     "units": 58, "li_units": 58, "permit": None, "category": "split", "split": {"new": 46, "preserved": 12},
     "follow_up": True, "sources": ["INVEST South/West"],
     "note": "548 Enterprise and partners, South Chicago: a new 5-story building of 46 apartments on City-owned land and 12 apartments in a rehabbed 3-story building at 8840, all at or under 60% AMI. Rezoned May 2023; financing award not confirmed; no permit. FOLLOW UP."},
    {"id": "CITYF-PIONEROS", "project": "Pioneros (Pioneer Bank site)", "address": "1616-38 N Pulaski Rd",
     "units": 75, "li_units": 75, "permit": None, "category": "new", "follow_up": True,
     "sources": ["INVEST South/West"],
     "note": "Team Pioneros: a new 9-story building on the parking lot north of the landmark Pioneer Bank, 75 apartments all affordable (43 at 60% AMI, 32 at 30%); the bank itself becomes a cultural and office center (City TIF, 2023-24). Applied for 9% LIHTC in 2023 (31 units); no award found. FOLLOW UP."},
    # Found 2026-10-20 checking the City's TIF-funded RDA list (mex4-ppfc):
    # TIF approved, affordable units required, no construction permit yet.
    {"id": "CITYF-ARMOUR-BUILDING", "project": "Armour Building", "address": "3300 S Federal St",
     "units": 102, "li_units": 21, "mixed_income": True, "permit": None, "category": "new",
     "basis": "conversion of a non-residential building", "follow_up": True,
     "sources": ["City TIF RDA 2022 (mex4-ppfc)"],
     "note": "Renovation of the historic Armour Building into 102 apartments (19 one-bedroom, 83 micro units), 21 affordable and 81 market-rate, with $5.5M in City TIF (CDC 2022). No conversion permit as of 2026-10. FOLLOW UP."},
    {"id": "CITYF-CONGRESS-THEATER", "project": "Congress Theater", "address": "2135 N Milwaukee Ave",
     "units": 16, "li_units": 16, "permit": None, "category": "new",
     "basis": "residential units in a restored theater building", "follow_up": True,
     "sources": ["City TIF RDA 2023, amended 2024 and 2026", "HUD Section 108 loan 2026"],
     "note": "Restoration of the Congress Theater as a music venue with offices, storefronts and 16 affordable apartments; up to $27.25M in City TIF and a 2026 HUD Section 108 loan. Only roof and masonry permits so far (2015, 2024). FOLLOW UP."},
]

# NSP rental buildings: vacant or foreclosed multifamily buildings the City
# bought and rehabbed with federal Neighborhood Stabilization Program money
# (NSP1-3, 2010-2015) through Mercy Portfolio Services, then rented. Source:
# the City's "Status of NSP Properties" table (2015 Q4 report), properties
# marked "Rented", dated by each building's rehab permit. Added 2026-10-19.
# Left out here: the five POAH buildings (6200 S Vernon, 6456 S Maryland,
# 5615 S Prairie, 6323 and 6428 S Ingleside), already counted in the 2017
# Woodlawn Roll-Up deal (ILB20177003), and Bronzeville Artists Lofts (its own
# record above); 1-4 unit buildings that were sold are homeownership.
# NSP rental units are restricted either at or under 50% AMI or at or under
# 120% AMI; only the first kind counts. Where a published split exists it is
# used (NSP_SPLITS); otherwise the at-or-under-80% share is estimated from
# the City's NSP multifamily units-by-income table for the rehab year (2011:
# 315 of 481; 2012: 78 of 173; 2013: 45 of 91), and the rest are kept in
# "over80". Treated as preserved: existing residential buildings rehabbed
# (as with the NSP-funded Woodlawn Roll-Up), though many stood vacant.
NSP_SHARE = {2010: (315, 481), 2011: (315, 481), 2012: (78, 173), 2013: (45, 91)}
NSP_SPLITS = {   # address: (units at or under 80% AMI, units at 81-120% AMI, source)
    "6015-31 S Indiana Ave": (34, 12, "Ironwood Court: 34 at 50% AMI, 12 at 120% (Interra Realty sale listing)"),
    "4800-14 S Calumet Ave": (12, 9, "Winterberry Place: 12 at 50% AMI, 9 at 120% (Interra Realty sale listing); restrictions run 2013-2028"),
    "347-51 S Central Ave": (19, 3, "11 at or under 50% AMI and 8 at or under 80% (owner's listing); the other 3 presumably up to 120%"),
}
# Decided 2026-10-19: preserved unless a source documents the building as
# long vacant when bought, the same standard as everywhere else.
NSP_VACANT = {
    "5921-39 S Wabash Ave": "St. Edmund's Court was a vacant courtyard apartment building when acquired through NSP (CHA and St. Edmund's descriptions)",
}
NSP_RENTALS = [   # address, units, developer, rehab permit
    ("1003 W 77th St", 6, "KMW Communities", ("2013-03-11", "100466060")),
    ("1015 N Pulaski Rd", 30, "Celadon Holdings", ("2011-12-06", "100409764")),
    ("1055-57 N Kilbourn Ave", 4, "CDG II", None),
    ("1122-24 N Monticello Ave", 4, "CDG II", ("2011-09-12", "100410501")),
    ("12013-15 S Eggleston Ave", 13, "KMA Holdings", ("2011-06-30", "100378876")),
    ("1214 W 52nd St", 3, "New West Realty", ("2011-12-09", "100421339")),
    ("1641-43 N Lamon Ave", 4, "KMA Holdings", None),
    ("1863 S Lawndale Ave", 15, "Lawndale Christian Development Corp.", ("2011-04-29", "100383608")),
    ("220-222 S Lotus Ave", 4, "Karry L. Young Development", ("2012-09-06", "100444985")),
    ("2501-05 W 63rd St", 12, "KMA Holdings", ("2011-09-30", "100401131")),
    ("2925 W 59th St", 9, "New Directions Housing Corp.", ("2011-12-07", "100413890")),
    ("3252-56 W Leland Ave", 6, "Chicago Metropolitan Housing Development Corp.", None),
    ("3302-08 W Huron St", 8, "KMW Communities", ("2011-06-28", "100390273")),
    ("3351 W Ohio St", 14, "KMW Communities", ("2013-06-21", "100466054")),
    ("347-51 S Central Ave", 22, "Karry L. Young Development", ("2011-03-03", "100375344")),
    ("3550 W Lyndale St", 7, "Hispanic Housing Development Corp.", None),
    ("3818 W Ohio St", 3, "KMW Communities", ("2011-09-06", "100405982")),
    ("3847 W Huron St", 3, "KMW Communities", ("2011-08-30", "100405939")),
    ("4231 W Division St", 12, "IFF", ("2011-10-25", "100405687")),
    ("4800-14 S Calumet Ave", 21, "Brinshore Development", ("2012-12-19", "100453545")),
    ("5520 S Prairie Ave", 18, "New West Realty", ("2011-03-15", "100379011")),
    ("5521 W Gladys Ave", 8, "Three Corners", ("2010-08-02", "100356768")),
    ("5655 S Indiana Ave", 22, "Jarrell Lawndale Restoration", ("2011-08-24", "100395725")),
    ("5727 S Calumet Ave", 7, "1600 Investment Group", ("2012-01-12", "100421191")),
    ("5840 S King Dr", 8, "IFF", None),
    ("5921-39 S Wabash Ave", 36, "St. Edmund's Redevelopment Corp. (St. Edmund's Court)", ("2011-04-15", "100377921")),
    ("6015-31 S Indiana Ave", 46, "Brinshore Development (Ironwood Court)", ("2010-08-24", "100340577")),
    ("6034-52 S Prairie Ave", 30, "Three Corners (Prairie Court)", ("2011-12-29", "100414191")),
    ("6205-15 S Langley Ave", 19, "Brinshore Development (Boxelder Court)", ("2012-01-19", "100421472")),
    ("6443-59 S Yale Ave", 15, "Karry L. Young Development", ("2012-09-20", "100442533")),
    ("7014 S Kimbark Ave", 4, "Celadon Holdings", ("2011-07-19", "100378798")),
    ("730 N Springfield Ave", 2, "KMW Communities", ("2011-08-03", "100374801")),
    ("7543-45 S Phillips Ave", 7, "New Homes by New Pisgah", ("2011-01-05", "100372165")),
    ("7914 S Carpenter St", 4, "New Homes by New Pisgah", ("2012-04-20", "100432047")),
    ("7953 S Vernon Ave", 15, "Celadon Holdings", ("2011-04-21", "100378788")),
    ("8031-35 S Drexel Ave", 12, "PMG Chicago Group II", ("2013-04-25", "100477223")),
    ("8322 S Houston Ave", 4, "Bronzeville Renovations", ("2012-03-09", "100423207")),
    ("8637 S Saginaw Ave", 4, "Bronzeville Renovations", ("2012-01-27", "100414973")),
    ("9100 S Burley Ave", 7, "Claretian Associates", ("2011-11-14", "100358384")),
]
for _a, _u, _dev, _permit in NSP_RENTALS:
    _year = int(_permit[0][:4]) if _permit else 2012
    if _a in NSP_SPLITS:
        _li, _over, _how = NSP_SPLITS[_a]
    else:
        _n, _d = NSP_SHARE[_year]
        _li = round(_u * _n / _d)
        _over = _u - _li
        _how = (f"split not published; {_li} of {_u} estimated at or under 80% AMI from the "
                f"City's {_year} NSP multifamily totals ({_n} of {_d})")
    CITY_FUNDED.append({
        "id": "NSP-" + re.sub(r"[^A-Z0-9]+", "-", _a.upper()).strip("-"),
        "project": (f"St. Edmund's Court (NSP rental)" if _a.startswith("5921") else f"NSP rental: {_a}"), "address": _a, "units": _u, "li_units": _li,
        "over80": _over, "mixed_income": _over > 0, "nsp_estimated": _a not in NSP_SPLITS,
        "permit": _permit, **({} if _permit else {"estimated": 2012}),
        "category": "new" if _a in NSP_VACANT else "preserved", "sources": ["City NSP status report 2015"],
        "note": f"{_dev}: {_u}-unit building bought and rehabbed with City NSP funds and rented "
                + (f"(rehab permit {_permit[0]})" if _permit else "(no rehab permit found; year estimated)")
                + f". {_how}." + (f" Counted as new: {NSP_VACANT[_a]}." if _a in NSP_VACANT else ""),
    })

# Looked at and not counted, printed on each run.
CITY_SET_ASIDE = [
    {"project": "Palmer Place Apartments", "address": "3301 W Palmer St", "units": 36,
     "note": "Sale of an existing 1993 HOME-funded affordable building to a new owner with a City acquisition loan (ordinance in committee, October 2026); no renovation and no new restrictions."},
    {"project": "ABLA Brooks Homes", "address": "1300-1400 W 13th St", "units": 330,
     "note": "Chicago Housing Authority renovation of its 330-unit public housing complex (2025-27), with City TIF for site infrastructure under a July 2026 agreement. CHA public housing without tax credits is outside the page's scope."},
    {"project": "Midwest Apartments", "address": "6 N Hamlin Blvd", "units": 276,
     "note": "Refinancing only: 2016 City preservation loan on HUD's 1996 Midwest Athletic Club LIHTC deal."},
    {"project": "Heartland Phoenix House", "address": "1251 S Sawyer Ave", "units": 32,
     "note": "Refinancing only: debt restructuring (2020)."},
    {"project": "Albany Park Initiative", "address": "18 scattered buildings", "units": 42,
     "note": "Chicago Metropolitan Housing Development Corp., City loan 2018: 4 at or under 60% AMI, 11 at or under 80%, 27 market, in 18 buildings across wards 30/31/36/37/40/50 with no addresses given. Cannot be placed."},
    {"project": "Roseland Village Intergenerational Apartments", "address": "10400 S Michigan Ave", "units": 10,
     "note": "Neighborhood Housing Services, City loan 2010. No permit or later record found."},
    {"project": "Reclaiming Southwest Chicago II", "address": "various", "units": 17,
     "note": "SWOP, IHDA donation credits and NSP 2019-2020; 5 of 17 units carry the credit's restrictions. Addresses not listed."},
    {"project": "IHDA capital grants and refinancing (2018-2025)", "address": "various", "units": 0,
     "note": "Capital Bill preservation and re-entry (HJIIP) grants of up to $475k for repairs to existing buildings, trust-fund loans with no rehab (Buena Vista and Central Park, 81 units) and credit-enhancement refinancings: nothing built or newly restricted."},
    {"project": "IHDA donation-credit single-family homes (South Side, West Pullman)", "address": "various", "units": 22,
     "note": "New single-family homes for sale (2018-2024): homeownership, outside the rental definition."},
    {"project": "Reclaiming Chicago (scattered sites)", "address": "various", "units": 25,
     "note": "Donation credits 2024-2025 and City land, but homes for sale at 80-120% AMI: outside the rental, at-or-under-80% definition."},
]
# The City's TIF Purchase+Rehab multifamily program also reports unit totals
# with no project list: 64 units in 2022, 28 in 2023, none in 2019-2021.


# Who a development serves, for the page's "Who it serves" filter. Classed by
# building, decided 2026-10 from IHDA's elderly/non-elderly column, the City's
# Family/Senior/SRO/Supportive labels, CHA's project-based voucher list, HUD's
# assisted-housing client groups and LIHTC target-population fields, and the
# developers' own descriptions. Senior: age-restricted (55+ or 62+), including
# supportive living facilities. Supportive: built for people leaving
# homelessness or living with a disability, usually with services on site,
# including nonprofit SROs. Everything else is general occupancy, open to any
# eligible household; so is a building that mixes populations (e.g. a senior
# tower with family townhomes, or a family building with some supportive
# units). ARO units are general.
SENIOR = set("""
    CITY2021-ALBANY-TERRACE CITY2021-ARCHER-SENIOR CITY2021-CHURCHVIEW
    CITY2021-GAINES CITY2021-IMANI CITY2023-ABRAMS
    CITY2023-MARTHA-WASHINGTON CITY2023-PARK-MANOR CITY2023-WBC-MANOR
    CITY2025-79TH-WOODLAWN CITY2025-GATEWAY-SNEIDER
    CITY2025-HOLLYWOOD-HOUSE CITYF-ALL-SAINTS CITYF-MARX CITYF-NAOMI-SMITH
    CITYF-PORTA-COELI CITYPL-LEVY-HOUSE CITYF-ST-EDMUNDS-ANNEX CITYF-VILLA-GUADALUPE DOH-CHURCHVIEW-MANOR DOH-CICERO-SENIOR-LOFTS
    DOH-LAWN-TERRACE DOH-MONTCLARE-AVALON-PARK DOH-MONTCLARE-CALUMET
    DOH-MONTCLARE-ENGLEWOOD DOH-MONTCLARE-LAWNDALE
    DOH-OAKWOOD-SHORES-SENIOR DOH-WOODLAWN-PARK-SENIOR
    IHDA2024-PIONEER-ARCADE IHDA4-835-WILSON IHDA4-BERRY-MANOR
    IHDA4-CAMPBELL-TERRACE IHDA4-CONGRESSMAN-COLLINS IHDA4-CORCORAN-PLACE
    IHDA4-GREENWOOD-CITY-SENIOR IHDA4-JONES-SENIOR IHDA4-LAS-MORADAS
    IHDA4-LOGAN-VISTAS IHDA4-MARTIN-FARRELL IHDA4-MATTHEWS-SENIOR
    IHDA4-RAVENSWOOD-SENIOR IHDA4-SOUTH-CHICAGO-SALUD
    IHDA4-SPURLOCK-SAMPSON IHDA4-SUNNYDALE-COMMONS IHDA4-WILLA-RAWLS
    ILA00000057 ILA20110920 ILA20110935 ILA20140012 ILA20149843
    ILA20170001 ILA20180001 ILA20220040 ILA20220042 ILA20230015
    ILA20230016 ILA20240004 ILB0000X025 ILB0000X026 ILB0000X038
    ILB0000X041 ILB0000X049 ILB0000X052 ILB0000X053 ILB0000X073
    ILB0000X074 ILB20110803 ILB20110809 ILB20130100 ILB20136002
    ILB20138101 ILB20140015 ILB20147016 ILB20149058 ILB20153001
    ILB20153002 ILB20154003 ILB20159003 ILB20159004 ILB20163001
    ILB20171001 ILB20180004 ILB20195001 ILB20195003 ILB20195053
    ILB20240001
""".split())
SUPPORTIVE = set("""
    CITYF-OGDEN-PARK CITYF-CRESSEY-HOUSE CITYF-AUGUSTA-HOME
    CITY2019-FOGLIA CITY2021-PRAIRIE-DISTRICT-4 CITY2023-BELRAY
    CITY2023-JANET-SMITH CITY2025-LELAND CITYF-CONSERVATORY
    CITYF-ENGLEWOOD-FAMILY CITYF-LINCOLN-PARK-SHELTER CITYF-NEW-MOMS
    CITYF-PIERCE-HOUSE CITYF-RENAISSANCE-WABASH CITYF-SARAHS-HOUSE
    CITYF-SARAHS-LAKESIDE CITYF-SARAHS-SHERIDAN CITYF-WEST-HUMBOLDT-PLACE
    DOH-COVENT DOH-HOPE-MANOR-VILLAGE DOH-HUMBOLDT-PARK-RESIDENCE
    DOH-MARK-TWAIN IHDA2020-CARLTON IHDA2024-3959-LINCOLN IHDA2026-BELDEN
    IHDA2026-SARAHS-SUNNYSIDE IHDA4-LAKEVIEW-LANDING IHDA4-MAJOR-JENKINS
    IHDA4-THRESHOLDS-RAD2-AUSTIN IHDA4-THRESHOLDS-RAD2-ROWAN ILA20110917
    ILA20120001 ILA20158126 ILA20158127 ILA20190017 ILA20210007
    ILA20240009 ILB0000X021 ILB0000X023 ILB0000X039 ILB20120801
    ILB20136003 ILB20141001 ILB20177089
""".split())


# CHA units (public housing, or CHA project-based replacement units) inside
# each development, for the page's "CHA units only" view. Every one is also
# counted in li_units. Sources: the City's year-end appendix "Commitments
# under CHA's Plan for Transformation and Plan Forward" (2023 Q4), HUD's
# public-housing development file (ACC units, IL002), CHA's MTW annual
# reports and announcements. "est." marks a total CHA reports for a phase
# that HUD splits across records, divided in proportion to low-income units.
# Gathered 2026-10.
CHA_UNITS = {
    "ILB0000X026": 449, "IHDA4-SPURLOCK-SAMPSON": 405, "CITY2021-ALBANY-TERRACE": 350,
    "CITY2021-GAINES": 150,                       # all-CHA senior buildings
    "DOH-LATHROP-1A": 161, "ILA20230011": 28, "CITY2021-LATHROP-1D": 121,
    "DOH-ROSENWALD-COURTS": 60, "DOH-STERLING-PARK": 66,
    "CITY2019-ROOSEVELT-3B": 75, "DOH-ROOSEVELT-SQUARE-1-REHAB": 132,  # City: 207 in the 3B deal
    "CITY2021-LECLAIRE-NORTH": 68, "IHDA2022-LECLAIRE-SOUTH": 23,      # est.: 91 project-based in Phase A
    "IHDA4-RAVENSWOOD-SENIOR": 74, "ILB20110807": 60, "ILA20110933": 65,
    "ILB20120268": 46, "ILB20136004": 37, "CITY2019-PARK-BLVD-3B": 36,
    "ILB20119813": 39, "ILB20162003": 36, "ILB20212001": 35, "CITY2021-PARKSIDE-3": 37,
    "ILA20240010": 37, "IHDA2021-OGDEN-A2": 30,
    "IHDA2022-SOUTHBRIDGE-1C": 29, "ILA20230021": 37, "ILA20220048": 31,  # est.: 68 in Southbridge 1A+1B
    "ILB0000X069": 28, "ILB0000X075": 37, "CITY2019-WESTHAVEN-IID": 38,
    "ILB20132005": 29, "ILB20132003": 8, "ILB20132006": 6, "ILB20132004": 2, "ILB20132002": 2,  # est.: 47 at Sullivan Station
    "IHDA4-SHEFFIELD-LINCOLN-PARK": 50, "ILB0000X020": 38, "DOH-ST-EDMUNDS-OASIS": 19,
    "ILB20163002": 25, "IHDA2021-OAK-LARRABEE": 17, "ILB0000X031": 26, "ILB20162001": 15,
    "ILA20130001": 22, "ILB20195001": 30, "ILB20195003": 30, "CITY2021-LEGENDS-A3": 21,
    "ILB20222001": 20, "IHDA2020-OAKWOOD-3-1": 19,
    "ILB20110803": 104, "DOH-OAKWOOD-SHORES-SENIOR": 59, "DOH-OAKWOOD-TERRACE": 19,
    "ILB20144002": 11, "ILB0000X045": 30, "DOH-LAKE-PARK-CRESCENT": 60,
    "CITY2023-HILLIARD": 153, "CITY2021-NORTH-TOWN-VILLAGE": 39,
    "ILB0000X080": 95, "ILB20110808": 60, "ILA20110100": 46,
}

# Developments that received City tax-increment financing (TIF), for the
# page's TIF label. TIF is never a development's primary program: every TIF
# deal with income-restricted units is also tax-credit, Other City/State or
# ARO (checked 2026-10-20). Sources: the TIF multifamily commitment tables
# in the City's year-end housing reports (2010-2023; none in 2019), the
# City's list of TIF-funded RDAs and IGAs (mex4-ppfc) matched by location,
# 2023-26 City Council ordinances, and records' own notes. A TIF deal for an
# earlier phase of the same building counts (e.g. Hollywood House 2009).
TIF = set("""
    ILB20110811 ILA20110920 ILB20120801 ILB20119812 ILA20110929 ILA20110933
    ILB20119813 ILA20120025 ILA20120007 DOH-OAKWOOD-TERRACE
    DOH-MONTCLARE-AVALON-PARK ILA00000057
    DOH-HAZEL-WINTHROP ILB20159004 ILA20130826 ILA20120001 ILA20120006
    ILB20136002 ILB0000X069 CITYF-BRONZEVILLE-ARTIST-LOFTS
    ILB20141001 ILB20149058 ILB20136004 ILA20140017 DOH-ROSENWALD-COURTS
    ILB0000X045 CITYF-WEST-HUMBOLDT-PLACE
    ILB20162003 ILB20159003 ILB20163001 ILB20159005 ILA20158126 ILB20153003
    ILB20162001 ILB0000X082
    ILB20162002 DOH-MONTCLARE-LAWNDALE ILB20163002 ILB0000X031 ILB20171001
    ILB20180005
    ILB20195053 ILB0000X049 ILB20177089 DOH-PENNYCUFF
    CITYF-PIERCE-HOUSE ILB20195052 ILB0000X075 CITYF-LINCOLN-PARK-SHELTER
    DOH-MONTCLARE-CALUMET ILB0000X020
    ILA20230021 ILA20220048 CITYF-SARAHS-SHERIDAN ILB20212001 ILB20231002
    ILA20210007
    DOH-EMMETT-STREET DOH-HUMBOLDT-PARK-RESIDENCE
    CITY2019-SIFUENTES ILB20231001 CITY2019-PARK-BLVD-3B CITYF-CONSERVATORY
    IHDA2020-OAKWOOD-3-1
    CITY2019-ROOSEVELT-3B DOH-ROOSEVELT-SQUARE-1-REHAB CITY2019-PARK-STATION
    CITY2019-WESTHAVEN-IID DOH-AUBURN-GRESHAM DOH-BARBARA-JEAN-WRIGHT
    DOH-GRACE-MANOR CITY2021-AUSTIN-UNITED CITY2021-THRIVE CITY2021-IMANI
    CITY2019-GARFIELD-GREEN
    CITY2019-ENCUENTRO-1 ILB20250001 CITY2021-4715-WESTERN
    CITYF-RENAISSANCE-WABASH IHDA2022-HUMBOLDT-PASSIVE DOH-UNITED-YARDS-1A
    CITY2021-LATHROP-1D CITYPL-CARE-MANOR CITY2021-LEGENDS-A3 CITY2021-SACRED
    CITYPL-THRIVE-EXCHANGE IHDA2021-OGDEN-A2 IHDA4-HEART-OF-UPTOWN
    CITYF-79-W-MONROE IHDA2022-EARLE-SCHOOL CITY2021-PARKSIDE-3 CITYPL-THE-AVE
    CITY2021-LECLAIRE-NORTH IHDA2021-OAK-LARRABEE CITYF-GALLERIA-89
    CITYPL-111-W-MONROE CITYPL-208-S-LASALLE CITYF-135-S-LASALLE
    CITYF-105-W-ADAMS CITYF-30-N-LASALLE IHDA2022-SOUTHBRIDGE-1C
    CITY2023-5853-BROADWAY IHDA2024-PIONEER-ARCADE CITY2023-HOMAN-SQUARE-IV
    CITY2025-LELAND CITYF-VILLA-GUADALUPE CITYF-ARMOUR-BUILDING CITYF-CONGRESS-THEATER
""".split())


def cha_split(dev_id, new, pres):
    """CHA units split between new and preserved in the development's own proportion."""
    n = CHA_UNITS.get(dev_id)
    if n is None:
        return {}
    chn = round(n * new / (new + pres)) if new + pres else 0
    return {"chu": n, "chn": chn, "chp": n - chn}


def population(dev_id):
    return "senior" if dev_id in SENIOR else "supportive" if dev_id in SUPPORTIVE else "general"


def main():
    review_path = sys.argv[1] if len(sys.argv) > 1 else None
    cls = aro.load_classifier()
    hud = fetch_hud()
    print(f"  {len(hud)} Chicago LIHTC projects in HUD's database")

    NEWISH = re.compile(r"NEW CONSTRUCTION|PROPOSED NEW|ERECT NEW|CONSTRUCTION OF A NEW")

    def kind_of(p):
        if (p.get("permit_type") or "") == "PERMIT - NEW CONSTRUCTION":
            return "new"
        return "new" if NEWISH.search((p.get("work_description") or "").upper()) else "renovation"

    # Matching is by address, and the project's location comes from HUD, so
    # permits the city never geocoded are still usable here.
    permits = json.load(open(PERMIT_CACHE))
    in_scope = [r for r in hud if (year(r.get("yr_pis")) or 9999) >= FIRST_PIS
                and r.get("hud_id") not in DUPLICATES and r.get("hud_id") not in NOT_IN_CHICAGO]
    permits += fetch_extra_permits(in_scope, {p["permit_"] for p in permits})
    by_street = collections.defaultdict(list)
    for p in permits:
        k = aro.street_key((p.get("street_number") or "").strip(),
                           f"{(p.get('street_direction') or '').strip()} "
                           f"{(p.get('street_name') or '').strip()}")
        if k:
            by_street[k[1]].append((k[0], p, kind_of(p)))
            core = aro.street_core(k[1])
            if core != k[1]:
                by_street[core].append((k[0], p, kind_of(p)))

    def pool(street):
        found = by_street.get(street) or by_street.get(aro.street_core(street), [])
        if found or street.split()[0] in ("N", "S", "E", "W"):
            return found
        # HUD sometimes leaves out the direction ("3720 DEARBORN", "8920
        # LOOMIS"). Try all four; the house number, date window and size test
        # still have to agree, which rules out the far side of the city.
        return [x for d in "NSEW" for x in
                (by_street.get(f"{d} {street}") or by_street.get(aro.street_core(f"{d} {street}"), []))]

    claimed = {}

    def big_rehab(p):
        t = cls.CROSS_REF.sub(" ", (p.get("work_description") or "").upper())
        if aro.NOT_A_BUILDING.search(t) or cls.REVISION.search(t):
            return False
        return num(p.get("reported_cost")) >= REHAB_MIN_COST

    def match(rec):
        addr = hud_address(rec.get("proj_add"))
        if not addr:
            return None, "no usable address"
        lo, hi, street = addr
        total = num(rec.get("n_units")) or num(rec.get("li_units"))
        need_near = max(2, math.ceil(total * MIN_SHARE_OF_UNITS))
        need_at = max(2, math.ceil(total * MIN_SHARE_AT_ADDRESS))
        pis, alloc = year(rec.get("yr_pis")), year(rec.get("yr_alloc"))
        if pis:
            window = (pis - YEARS_BEFORE_PIS, pis + 1)
        elif alloc:
            window = (alloc - 1, alloc + YEARS_BEFORE_PIS)
        else:
            window = (2010, 2100)

        best = None
        for n, p, kind in pool(street):
            y = int(p["issue_date"][:4])
            if not window[0] <= y <= window[1]:
                continue
            # one permit dates one project, except where HUD lists two
            # allocations in the same building
            if p["permit_"] in claimed and claimed[p["permit_"]] != (lo, street):
                continue
            if n in (lo, hi):
                tier, gap = 1, 0
            elif lo < n < hi and n % 2 == lo % 2:
                tier, gap = 2, 0
            else:
                gap = min(abs(n - lo), abs(n - hi))
                if gap > aro.ADDR_TOLERANCE or n % 2 != lo % 2:
                    continue
                tier = 3
            need = need_at if tier < 3 else need_near
            units = aro.plausible(p, need, cls, require_count=(kind == "renovation"))
            if units is None and tier < 3 and kind == "renovation" and big_rehab(p):
                units = 0  # dated by cost, not by a stated count
            if units is None:
                continue
            # New buildings rank by size; renovations by cost, since the
            # recapitalisation is the big job and later fit-outs restate the
            # unit count just as often.
            size = units if kind == "new" else num(p.get("reported_cost"))
            rank = (tier, 0 if kind == "new" else 1, -size, p["issue_date"])
            if best is None or rank < best[0]:
                best = (rank, p, kind, units, tier, gap)
        if not best:
            return None, "no plausible permit"
        _, p, kind, units, tier, gap = best
        claimed[p["permit_"]] = (lo, street)
        return {"permit": p, "kind": kind, "units": units, "tier": tier, "gap": gap}, None

    def classify(rec, m):
        """'new' or 'preserved', and what decided it."""
        if m:
            if m["kind"] == "new":
                return "new", "new-construction permit"
            t = (m["permit"].get("work_description") or "").upper()
            if (CONVERTS.search(t) and NOT_HOUSING.search(t)
                    and not DECONVERTS.search(t) and not WAS_HOUSING.search(t)):
                return "new", "conversion of a non-residential building"
            if RELIGIOUS.search(t):
                return "new", "conversion of a religious house"
            return "preserved", "renovation permit"
        hud_type = HUD_TYPE.get(str(rec.get("type") or ""))
        if hud_type in ("new", "preserved"):
            return hud_type, "HUD construction type"
        return "unknown", "HUD construction type is " + (hud_type or "blank")

    def li_units(rec, m):
        """
        Low-income units and where the number came from. HUD's li_units is
        blank on some records; li_unitr is HUD's own filled-in version.
        """
        hand = HAND_REVIEWED.get(rec.get("hud_id"), {})
        if hand.get("units"):
            return hand["units"], "hand-checked"
        for field in ("li_units", "li_unitr"):
            if num(rec.get(field)):
                return num(rec.get(field)), "HUD " + field
        return 0, "none recorded"

    # ARO buildings by address, so a building in both programs is tagged with
    # both and counted once (the overlap rule: CHA > supportive > LIHTC > ARO).
    aro_at = {}
    for ward_list in json.load(open("src/data/aro.json"))["projects"].values():
        for b in ward_list:
            k = aro.parse_address(b["a"])
            if k:
                aro_at[(k[0], aro.street_core(k[1]))] = b["n"]

    hud = [r for r in hud if r.get("hud_id") not in DUPLICATES
           and r.get("hud_id") not in NOT_IN_CHICAGO]
    scope = [r for r in hud if (year(r.get("yr_pis")) or 9999) >= FIRST_PIS]
    print(f"  {len(scope)} opened {FIRST_PIS} or later, or with no year recorded")

    # Biggest projects first: a permit can only date one project.
    out = []
    for r in sorted(scope, key=lambda r: -num(r.get("n_units"))):
        hand = HAND_REVIEWED.get(r.get("hud_id"), {})
        # A hand-checked date always wins over the matcher.
        m, why = (None, None) if hand.get("dated") else match(r)
        if not m and hand.get("dated"):
            how, when, pid = hand["dated"]
            m = {"permit": {"permit_": pid or "", "issue_date": when if len(when) > 4 else when + "-01-01",
                            "street_number": "", "street_direction": "", "street_name": "",
                            "work_description": ""},
                 "kind": "hand", "units": None, "tier": {"permit": "hand", "estimate": "estimate",
                                                         "pre2010": "pre2010"}[how], "gap": 0}
            why = None
        cat, basis = classify(r, m) if not (m and m["kind"] == "hand") else ("unknown", "")
        if hand.get("category"):
            cat, basis = hand["category"], hand.get("basis", "hand-checked")
        units_li, units_src = li_units(r, m)
        p = m["permit"] if m else None
        programs = ["LIHTC"]
        a = hud_address(r.get("proj_add"))
        if a:
            for n in range(a[0], a[1] + 1, 2 if a[1] > a[0] else 1):
                if (n, aro.street_core(a[2])) in aro_at:
                    programs.append("ARO")
                    break
        # CHA where the units are CHA housing: CHA-owned buildings, or CHA
        # replacement units on a CHA redevelopment site whatever their subsidy
        # (public housing or CHA's own project-based contracts). Not where CHA
        # vouchers are used in a building that is not a CHA redevelopment.
        if hand.get("cha"):
            programs.append("CHA")
        out.append({
            "hud_id": r.get("hud_id"),
            "project": (r.get("project") or "").strip(),
            "address": (r.get("proj_add") or "").strip(),
            "units": num(r.get("n_units")),
            "li_units": units_li,
            "li_units_source": units_src,
            "yr_pis": year(r.get("yr_pis")),
            "yr_alloc": year(r.get("yr_alloc")),
            "hud_type": HUD_TYPE.get(str(r.get("type") or ""), "blank"),
            "scattered": r.get("scattered_site_cd"),
            "tier": m["tier"] if m else None,
            "gap": m["gap"] if m else None,
            "unmatched_reason": why,
            "permit": p and {
                "id": p["permit_"], "date": p["issue_date"][:10],
                "address": f"{p.get('street_number')} {p.get('street_direction')} {p.get('street_name')}",
                "type": m["kind"], "units": m["units"],
                "text": (p.get("work_description") or "")[:300],
            },
            "category": cat,
            "basis": basis,
            "programs": programs,
            "mixed_income": bool(hand.get("mixed_income")),
            "assisted_living": bool(hand.get("assisted_living")),
            # low-income units by type where one project is part rehab, part new
            "split": hand.get("split"),
            "follow_up": bool(hand.get("follow_up")),
            "resyndication_of": RESYNDICATIONS.get(r.get("hud_id")),
            "note": hand.get("note"),
        })

    for p in PIPELINE:
        date, pid = p["permit"] if p.get("permit") else (None, None)
        tier = "award" if date else "awarded"
        if not date and p.get("estimated"):
            # Done but no permit found: dated to a stated year, like HUD estimates.
            date, pid, tier = f"{p['estimated']}-01-01", "", "estimate"
        out.append({
            "hud_id": p["id"], "project": p["project"], "address": p["address"],
            "units": p["units"], "li_units": p["li_units"], "li_units_source": "award research",
            "yr_pis": p.get("opened"), "yr_alloc": None, "hud_type": "n/a", "scattered": None,
            "tier": tier, "gap": 0, "unmatched_reason": None,
            "permit": date and {"id": pid, "date": date, "address": p["address"], "type": "hand",
                                "units": None, "text": ""},
            "category": p["category"], "basis": p.get("basis", "award research"),
            "programs": ["LIHTC"] + (["CHA"] if p.get("cha") else []),
            "mixed_income": bool(p.get("mixed_income")),
            "assisted_living": bool(p.get("assisted_living")),
            "split": p.get("split"), "follow_up": bool(p.get("follow_up")),
            "resyndication_of": p.get("resyndication_of"),
            "sources": p["sources"], "note": p["note"],
        })

    # HUD lists some projects twice (once per allocation, or with a typo in
    # the address). Flagged for review rather than merged.
    seen = collections.defaultdict(list)
    for o in out:
        a = hud_address(o["address"])
        if a:
            seen[(a[0], aro.street_core(a[2]))].append(o["project"])
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    print(f"  addresses listed more than once: {len(dupes)}")
    for k, v in dupes.items():
        print(f"    {k[0]} {k[1]}: {v}")

    tiers = collections.Counter(
        ("tier %d" % o["tier"]) if isinstance(o["tier"], int) else (o["tier"] or "unmatched")
        for o in out)
    print("  matching:", dict(sorted(tiers.items())))
    print("  reasons unmatched:", dict(collections.Counter(o["unmatched_reason"] for o in out if not o["tier"])))
    cats = collections.Counter()
    for o in out:
        if o["split"]:
            cats.update(o["split"])
        else:
            cats[o["category"]] += o["li_units"]
    print("  low-income units by category:", dict(cats))
    print("  dropped as duplicates:", len(DUPLICATES))
    both = [(o["project"], o["programs"]) for o in out if len(o["programs"]) > 1]
    print(f"  also in another program: {both}")
    print("  low-income units taken from:",
          dict(collections.Counter(o["li_units_source"] for o in out)))
    for h in HELD:
        print(f"  HELD, not counted: {h['project']} ({h['address']}, {h['units']} units)")
    print(f"  City/State-funded, no tax credits: {len(CITY_FUNDED)} projects, "
          f"{sum(c['li_units'] for c in CITY_FUNDED if c.get('permit') or c.get('estimated'))} restricted units counted, "
          f"{sum(c['li_units'] for c in CITY_FUNDED if not (c.get('permit') or c.get('estimated')))} in the pipeline")
    for c in CITY_SET_ASIDE:
        print(f"  City/State-funded, not counted: {c['project']} ({c['units']} units): {c['note'][:70]}")
    if review_path:
        json.dump(out, open(review_path, "w"), indent=1)
        print(f"  wrote review file {review_path}")
    write_site(out, hud)


# ---- site output --------------------------------------------------------

SITE_OUT = "src/data/lihtc.json"
WARDS = "https://data.cityofchicago.org/resource/p293-wvbd.geojson"
COORD_CACHE = ".lihtc_coords.json"


def permit_coords(ids):
    """Coordinates of permits by number, cached; the portal answers in batches."""
    cache = json.load(open(COORD_CACHE)) if os.path.exists(COORD_CACHE) else {}
    todo = sorted({i for i in ids if i and i not in cache})
    for k in range(0, len(todo), 40):
        batch = todo[k:k + 40]
        rows = aro.get(PERMITS_API, {
            "$where": "permit_ in(%s)" % ",".join(f"'{i}'" for i in batch),
            "$select": "permit_,latitude,longitude", "$limit": 200})
        for r in rows:
            if r.get("latitude"):
                cache[r["permit_"]] = [float(r["latitude"]), float(r["longitude"])]
        for i in batch:
            cache.setdefault(i, None)
    json.dump(cache, open(COORD_CACHE, "w"))
    return cache


def address_coords(address):
    """Coordinates of any permit at this street address: the fallback for
    projects that have no permit of their own yet."""
    a = hud_address(address.split(";")[0].strip())
    if not a:
        return None
    toks = a[2].split()
    dirn = toks[0] if toks and toks[0] in ("N", "S", "E", "W") else None
    core = aro.street_core(" ".join(toks[1:]) if dirn else a[2])
    where = (f"street_number='{a[0]}' AND starts_with(street_name,'{core}') "
             "AND latitude IS NOT NULL")
    if dirn:
        where += f" AND street_direction='{dirn}'"
    rows = aro.get(PERMITS_API, {"$where": where, "$select": "latitude,longitude", "$limit": 1})
    if rows:
        return [float(rows[0]["latitude"]), float(rows[0]["longitude"])]
    return census_coords(f"{a[0]} {a[2]}")


def census_coords(street):
    """Last resort for an address with no permit at all: the Census Bureau's
    public geocoder. Cached with the permit coordinates."""
    cache = json.load(open(COORD_CACHE)) if os.path.exists(COORD_CACHE) else {}
    key = "census:" + street
    if key not in cache:
        res = aro.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress", {
            "address": f"{street}, Chicago, IL", "benchmark": "Public_AR_Current", "format": "json"})
        m = res.get("result", {}).get("addressMatches") or []
        cache[key] = [m[0]["coordinates"]["y"], m[0]["coordinates"]["x"]] if m else None
        json.dump(cache, open(COORD_CACHE, "w"))
    return cache[key]


def write_site(out, hud):
    """
    The file the Affordable Units page reads. One record per development;
    the page aggregates by ward and year itself, so filters (new vs preserved,
    permit vs opening year) need no extra tables here.
    """
    from shapely.geometry import shape, Point
    from shapely.strtree import STRtree

    geo = aro.get(WARDS, {"$limit": 60})
    polys = [shape(f["geometry"]) for f in geo["features"]]
    nums = [int(f["properties"]["ward"]) for f in geo["features"]]
    tree = STRtree(polys)

    def ward_of(ll):
        if not ll:
            return None
        pt = Point(ll[1], ll[0])
        for i in tree.query(pt):
            if polys[i].contains(pt):
                return nums[i]
        return None

    hud_ll = {}
    for r in hud:
        try:
            hud_ll[r["hud_id"]] = [float(r["latitude"]), float(r["longitude"])]
        except (KeyError, TypeError, ValueError):
            pass
    pc = permit_coords([(o.get("permit") or {}).get("id") for o in out]
                       + [c["permit"][1] for c in CITY_FUNDED if c.get("permit")])

    def coords(o):
        p = (o.get("permit") or {}).get("id")
        return pc.get(p) if p and pc.get(p) else hud_ll.get(o["hud_id"]) or address_coords(o["address"])

    STATUS = {"award": "permit", "hand": "permit", "estimate": "estimate",
              "pre2010": "preSeries", "awarded": "awarded"}
    projects, unplaced = [], []
    for o in out:
        tier = o["tier"]
        status = "permit" if isinstance(tier, int) else STATUS.get(tier, "unmatched")
        ll = coords(o)
        w = ward_of(ll)
        if w is None:
            unplaced.append(o["project"])
        date = (o.get("permit") or {}).get("date")
        split = o.get("split") or {}
        li = o["li_units"]
        new = split.get("new", li if o["category"] == "new" else 0)
        pres = split.get("preserved", li if o["category"] == "preserved" else 0)
        projects.append({
            "id": o["hud_id"],
            "n": o["project"],
            "a": o["address"],
            "w": w,
            "ll": [round(ll[0], 5), round(ll[1], 5)] if ll else None,
            "u": o["units"],
            "li": li,
            "new": new,
            "pres": pres,
            "y": int(date[:4]) if date and status != "awarded" else None,
            "yo": o.get("yr_pis"),
            "s": status,
            "cha": "CHA" in o["programs"] or o["hud_id"] in CHA_UNITS,
            "tif": o["hud_id"] in TIF,
            "aro": "ARO" in o["programs"],
            "mix": bool(o.get("mixed_income")),
            "sl": bool(o.get("assisted_living")),
            "fu": bool(o.get("follow_up")),
            "rs": bool(o.get("resyndication_of")),
            "pop": population(o["hud_id"]),
            **cha_split(o["hud_id"], new, pres),
        })
    projects.sort(key=lambda p: -p["li"])
    by = collections.Counter(p["s"] for p in projects)

    city = []
    for c in CITY_FUNDED:
        date = (c["permit"][0] if c.get("permit")
                else f"{c['estimated']}-01-01" if c.get("estimated") else None)
        ll = (pc.get(c["permit"][1]) if c.get("permit") else None) or address_coords(c["address"])
        w = ward_of(ll)
        if w is None:
            unplaced.append(c["project"])
        li, split = c["li_units"], c.get("split") or {}
        city.append({
            "id": c["id"], "n": c["project"], "a": c["address"], "w": w,
            "ll": [round(ll[0], 5), round(ll[1], 5)] if ll else None,
            "u": c["units"], "li": li,
            "new": split.get("new", li if c["category"] == "new" else 0),
            "pres": split.get("preserved", li if c["category"] == "preserved" else 0),
            "y": int(date[:4]) if date else None, "yo": None,
            "s": "permit" if c.get("permit") else "estimate" if date else "awarded",
            "cha": bool(c.get("cha")), "tif": c["id"] in TIF, "aro": False, "mix": bool(c.get("mixed_income")),
            "sl": False, "fu": bool(c.get("follow_up")), "rs": False,
            "pop": population(c["id"]),
        })
    city.sort(key=lambda p: -p["li"])
    payload = {
        "meta": {
            "source": "HUD LIHTC database (LIHTCPUB), City of Chicago and IHDA award, "
                      "closing and progress reports; dates from City building permits",
            "hudUrl": HUD_URL,
            "built": __import__("datetime").date.today().isoformat(),
            "projects": len(projects),
            "byStatus": dict(by),
            "liUnits": sum(p["li"] for p in projects),
            "unplaced": unplaced,
            "cityProjects": sum(1 for p in city if p["s"] != "awarded"),
            "cityUnits": sum(p["li"] for p in city if p["s"] != "awarded"),
            "citySetAside": [{"n": c["project"], "u": c["units"], "note": c["note"]} for c in CITY_SET_ASIDE],
        },
        "projects": projects,
        "city": city,
    }
    os.makedirs(os.path.dirname(SITE_OUT), exist_ok=True)
    json.dump(payload, open(SITE_OUT, "w"), indent=0, separators=(",", ":"))
    print(f"  wrote {SITE_OUT}: {len(projects)} developments, {dict(by)}, {len(city)} City-funded; "
          f"{len(unplaced)} without a ward: {unplaced[:8]}")


if __name__ == "__main__":
    main()
