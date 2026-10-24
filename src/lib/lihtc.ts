/**
 * LIHTC (Low-Income Housing Tax Credit) data access, and the combined
 * affordable-units view that sets it beside ARO and City- and State-funded projects
 * that used no tax credits.
 *
 * One record per development. Sources are HUD's LIHTC database plus the
 * City's and IHDA's award, closing and progress reports, which cover the
 * recent years HUD has not caught up to; each development is dated by its
 * building permit. See scripts/import_lihtc.py.
 */
import { PERMIT_RAMP, PERMIT_NO_DATA, type PermitsData } from "./permits";
import type { AroData } from "./aro";

export type LihtcStatus = "permit" | "estimate" | "preSeries" | "awarded" | "stalled" | "proposed";

export interface LihtcProject {
  id: string;
  /** Name */
  n: string;
  /** Address */
  a: string;
  /** Ward on the current map; null only if the site could not be placed. */
  w: number | null;
  ll: [number, number] | null;
  /** All units, including any market-rate ones. */
  u: number;
  /** Low-income units. */
  li: number;
  /** Low-income units that are new: new construction, or a building that was not housing before. */
  new: number;
  /** Low-income units in existing housing that was rehabbed and recommitted. */
  pres: number;
  /** Permit year; null when awarded but not yet permitted. */
  y: number | null;
  /** Year it opened (HUD's placed-in-service year), where known. */
  yo: number | null;
  /**
   * permit: dated by a building permit. estimate: done, but no permit found,
   * so dated from other records. preSeries: permitted before the series
   * starts. awarded: tax credits awarded, no permit yet. stalled and proposed:
   * kept on file for the quarterly check, never shown or counted.
   */
  s: LihtcStatus;
  /** CHA housing: a CHA building, or CHA replacement units on a redevelopment site. */
  cha: boolean;
  /** Received City tax-increment financing. */
  tif?: boolean;
  /** Also on the City's ARO list. */
  aro: boolean;
  /** Includes market-rate units. */
  mix: boolean;
  /** Supportive living facility (assisted living). */
  sl: boolean;
  fu: boolean;
  /** A re-syndication: an existing tax-credit property given a new allocation. */
  rs: boolean;
  /** Who the building serves; see POPULATION in scripts/import_lihtc.py. */
  pop: "general" | "senior" | "supportive";
  /** CHA units (public housing or CHA project-based replacement), where CHA housing is part of it. */
  chu?: number;
  /** CHA units split new / preserved in the development's own proportion. */
  chn?: number;
  chp?: number;
  /**
   * Units with CHA project-based vouchers: rent subsidy tied to the unit, not
   * the tenant. Already counted in li; a label, never a bucket of its own in "All".
   */
  pbv?: number;
}

export interface LihtcData {
  meta: {
    source: string;
    hudUrl: string;
    built: string;
    projects: number;
    byStatus: Record<string, number>;
    liUnits: number;
    unplaced: string[];
    cityProjects: number;
    cityUnits: number;
    /** City-funded projects looked at and left out, with the reason. */
    citySetAside: { n: string; u: number; note: string }[];
  };
  projects: LihtcProject[];
  /** City-funded income-restricted projects with no federal tax credits; same shape. */
  city: LihtcProject[];
}

export type Program = "all" | "lihtc" | "cha" | "pbv" | "tif" | "city" | "aro";
export type LihtcKind = "new" | "both" | "preserved";
export type YearBasis = "permit" | "opened";
export type Population = "all" | "general" | "senior" | "supportive";

/**
 * Button order follows the waterfall, with CHA vouchers beside CHA-owned units;
 * vouchers and TIF are labels only.
 */
export const PROGRAM_LABELS: Record<Program, string> = {
  all: "All",
  cha: "CHA owned",
  pbv: "CHA voucher",
  lihtc: "Tax credit (LIHTC)",
  city: "Other City/State",
  aro: "ARO",
  tif: "TIF",
};

export const KIND_LABELS: Record<LihtcKind, string> = {
  new: "New units",
  both: "New + preserved",
  preserved: "Preserved only",
};

export const POP_LABELS: Record<Population, string> = {
  all: "All",
  general: "General",
  senior: "Senior",
  supportive: "Supportive",
};

export const BASIS_LABELS: Record<YearBasis, string> = {
  permit: "Permit year",
  opened: "Year opened",
};

export interface Options {
  program: Program;
  kind: LihtcKind;
  basis: YearBasis;
  pop: Population;
}

/**
 * Every development carries all the labels that apply to it (tax credit, CHA,
 * TIF, Other City/State, ARO). A program button shows every development with
 * that label, so the programs overlap and do not sum to "All". To count each
 * unit once — in the "All" totals, the stacked chart and the map's colours —
 * units are attributed down a waterfall: CHA units first, then the rest of a
 * tax-credit development's units, then Other City/State, then ARO. TIF is a
 * label only: every TIF-funded unit already falls in one of those buckets.
 * So are CHA project-based vouchers; the "pbv" bucket holds units only in the
 * CHA voucher view, which counts each building's voucher units.
 */
export type Bucket = "cha" | "lihtcNew" | "lihtcPres" | "city" | "aro" | "pbv";
export type Parts = Record<Bucket, number>;
export type Label = "LIHTC" | "CHA" | "PBV" | "TIF" | "City" | "ARO";

const zero = (): Parts => ({ cha: 0, lihtcNew: 0, lihtcPres: 0, city: 0, aro: 0, pbv: 0 });
export const partsTotal = (x: Parts) => x.cha + x.lihtcNew + x.lihtcPres + x.city + x.aro + x.pbv;

/** One development from any source, with its units split into buckets under the selection. */
export interface Dev {
  id: string;
  name: string;
  address: string;
  ward: number | null;
  ll: [number, number] | null;
  year: number | null;
  /** All units in the building, market-rate included, where known. */
  total: number | null;
  parts: Parts;
  units: number;
  labels: Label[];
  /** The development's top bucket in the waterfall, for the map's colour. */
  primary: "CHA" | "LIHTC" | "City" | "ARO";
  kind: "new" | "preserved";
  tags: string[];
  /** CHA project-based voucher units in the building, whatever the view. */
  pbv: number;
  src: LihtcProject | null;
}

/** The year a tax-credit development falls in, or null if it is off the series. */
export function lihtcYear(p: LihtcProject, basis: YearBasis): number | null {
  if (basis === "opened") return p.yo;
  return p.s === "permit" || p.s === "estimate" ? p.y : null;
}

/** ARO units are in market-rate buildings open to anyone, so they count as general. */
const popOk = (p: LihtcProject, o: Options) => o.pop === "all" || p.pop === o.pop;
const aroPopOk = (o: Options) => o.pop === "all" || o.pop === "general";

function labelsOf(p: LihtcProject, src: "lihtc" | "city"): Label[] {
  const l: Label[] = [src === "lihtc" ? "LIHTC" : "City"];
  if ((p.chu ?? 0) > 0 || p.cha) l.push("CHA");
  if (p.pbv) l.push("PBV");
  if (p.tif) l.push("TIF");
  return l;
}

const inSelection = (labels: Label[], program: Program) =>
  program === "all" ||
  (program === "lihtc" && labels.includes("LIHTC")) ||
  (program === "cha" && labels.includes("CHA")) ||
  (program === "pbv" && labels.includes("PBV")) ||
  (program === "tif" && labels.includes("TIF")) ||
  (program === "city" && labels.includes("City")) ||
  (program === "aro" && labels.includes("ARO"));

/** Voucher units split new / preserved in the development's own proportion. */
function pbvSplit(p: LihtcProject): [number, number] {
  const n = p.pbv ?? 0;
  const pbn = p.new + p.pres > 0 ? Math.round((n * p.new) / (p.new + p.pres)) : 0;
  return [pbn, n - pbn];
}

/** A tax-credit or City/State-funded development's units in each bucket under the selection. */
function lihtcParts(p: LihtcProject, src: "lihtc" | "city", o: Options): Parts {
  const x = zero();
  const keepNew = o.kind !== "preserved", keepPres = o.kind !== "new";
  if (o.program === "pbv") {
    const [pbn, pbp] = pbvSplit(p);
    x.pbv = (keepNew ? pbn : 0) + (keepPres ? pbp : 0);
    return x;
  }
  const chn = p.chn ?? 0, chp = p.chp ?? 0;
  x.cha = (keepNew ? chn : 0) + (keepPres ? chp : 0);
  if (o.program === "cha") return x;
  const restNew = Math.max(0, p.new - chn), restPres = Math.max(0, p.pres - chp);
  if (src === "lihtc") {
    x.lihtcNew = keepNew ? restNew : 0;
    x.lihtcPres = keepPres ? restPres : 0;
  } else {
    x.city = (keepNew ? restNew : 0) + (keepPres ? restPres : 0);
  }
  return x;
}

function lihtcTags(p: LihtcProject): string[] {
  const tags: string[] = [];
  if (p.pres > 0 && p.new > 0) tags.push("part preserved");
  if (p.chu != null && p.chu > 0) tags.push(`${p.chu} CHA owned`);
  if (p.pbv) tags.push(`${p.pbv} CHA voucher unit${p.pbv === 1 ? "" : "s"}`);
  if (p.mix) tags.push("mixed-income");
  if (p.sl) tags.push("supportive living");
  if (p.pop !== "general") tags.push(p.pop);
  if (p.s === "estimate") tags.push("year estimated");
  return tags;
}

/**
 * Every development in the selection with its year (null when off the
 * series), units by bucket and labels. The single source for the totals,
 * chart, map, ward card and list.
 */
export function allDevelopments(aro: AroData, lihtc: LihtcData, o: Options): Dev[] {
  const out: Dev[] = [];
  const basis = o.program === "lihtc" ? o.basis : "permit";
  const addLihtc = (p: LihtcProject, src: "lihtc" | "city") => {
    const labels = labelsOf(p, src);
    if (!inSelection(labels, o.program) || !popOk(p, o)) return;
    const parts = lihtcParts(p, src, o);
    const units = partsTotal(parts);
    if (units === 0) return;
    const year = src === "lihtc" ? lihtcYear(p, basis) : p.s === "permit" || p.s === "estimate" ? p.y : null;
    const pres = o.kind === "new" ? 0
      : o.program === "cha" ? p.chp ?? 0
        : o.program === "pbv" ? pbvSplit(p)[1]
          : p.pres;
    // The voucher view holds no CHA bucket, so judge the building as a whole.
    const chaLed = o.program === "pbv"
      ? (p.chu ?? 0) * 2 >= p.li && (p.chu ?? 0) > 0
      : parts.cha > 0 && parts.cha * 2 >= units;
    out.push({
      id: p.id, name: titleCase(p.n), address: p.a, ward: p.w, ll: p.ll, year, total: p.u || null,
      parts, units, labels,
      primary: chaLed ? "CHA" : src === "lihtc" ? "LIHTC" : "City",
      kind: pres * 2 > units ? "preserved" : "new",
      tags: lihtcTags(p), pbv: p.pbv ?? 0, src: p,
    });
  };
  for (const p of lihtc.projects) addLihtc(p, "lihtc");
  for (const p of lihtc.city) addLihtc(p, "city");
  // ARO units are new only, open to anyone, and never CHA.
  const aroIn = o.kind !== "preserved" && aroPopOk(o) &&
    (o.program === "all" || o.program === "aro" || o.program === "tif" || o.program === "pbv");
  if (aroIn) {
    for (const [w, list] of Object.entries(aro.projects)) {
      for (const [i, b] of list.entries()) {
        const labels: Label[] = ["ARO"];
        if (b.pbv) labels.push("PBV");
        if (b.tif) labels.push("TIF");
        if (!inSelection(labels, o.program)) continue;
        const parts = zero();
        if (o.program === "pbv") parts.pbv = b.pbv ?? 0;
        else parts.aro = b.u;
        const tags = b.k === "conversion" ? ["conversion"] : [];
        if (b.pbv) tags.push(`${b.pbv} CHA voucher unit${b.pbv === 1 ? "" : "s"}`);
        out.push({
          id: `ARO-${w}-${i}`, name: b.n !== "—" ? b.n : b.a, address: b.a, ward: Number(w), ll: b.ll ?? null,
          year: b.y && /^\d+$/.test(b.y) ? Number(b.y) : null, total: null, parts, units: partsTotal(parts), labels,
          primary: "ARO", kind: "new", tags, pbv: b.pbv ?? 0, src: null,
        });
      }
    }
  }
  return out;
}

/** Whether the selection is new units only, so a share of new building makes sense. */
export const isNewOnly = (o: Options) => o.program === "aro" || o.kind === "new";

export interface WardRow extends Parts {
  ward: number;
  /** Units in the current selection. */
  units: number;
  /** Developments with units in the selection and span. */
  developments: number;
  /** All new housing units permitted in the ward over the span. */
  allUnits: number;
  /** Selected units as a share of all new units; null where it would mislead. */
  share: number | null;
  rank: number;
}

function allUnitsIn(permits: PermitsData, ward: string, year: string): number {
  return (
    (permits.units[ward]?.[year] ?? 0) +
    (permits.conversions.sfh[ward]?.[year] ?? 0) +
    (permits.conversions.mfh[ward]?.[year] ?? 0)
  );
}

export function summarise(
  devs: Dev[],
  permits: PermitsData,
  from: number,
  to: number,
  o: Options
): { rows: WardRow[]; total: number; totalAll: number; totals: Parts } {
  const rows = new Map<number, WardRow>();
  for (let w = 1; w <= 50; w++) {
    let allUnits = 0;
    for (let y = from; y <= to; y++) allUnits += allUnitsIn(permits, String(w), String(y));
    rows.set(w, { ward: w, ...zero(), units: 0, developments: 0, allUnits, share: null, rank: 0 });
  }
  for (const d of devs) {
    if (d.ward == null || d.year == null || d.year < from || d.year > to) continue;
    const r = rows.get(d.ward)!;
    for (const k of Object.keys(d.parts) as Bucket[]) r[k] += d.parts[k];
    r.developments++;
  }
  let total = 0, totalAll = 0;
  const totals = zero();
  for (const r of rows.values()) {
    r.units = partsTotal(r);
    r.share = isNewOnly(o) && r.allUnits > 0 ? r.units / r.allUnits : null;
    total += r.units;
    totalAll += r.allUnits;
    for (const k of Object.keys(totals) as Bucket[]) totals[k] += r[k];
  }
  const out = [...rows.values()].sort((a, b) => b.units - a.units || a.ward - b.ward);
  out.forEach((r, i) => (r.rank = i + 1));
  return { rows: out, total, totalAll, totals };
}

export interface YearRow extends Parts {
  year: number;
  units: number;
  all: number;
  share: number | null;
}

export function byYear(devs: Dev[], permits: PermitsData, o: Options): YearRow[] {
  const out: YearRow[] = [];
  const idx = new Map<number, YearRow>();
  for (let y = permits.meta.firstYear; y <= permits.meta.lastYear; y++) {
    let all = 0;
    for (let w = 1; w <= 50; w++) all += allUnitsIn(permits, String(w), String(y));
    const row: YearRow = { year: y, ...zero(), units: 0, all, share: null };
    out.push(row);
    idx.set(y, row);
  }
  for (const d of devs) {
    const row = d.year == null ? undefined : idx.get(d.year);
    if (!row) continue;
    for (const k of Object.keys(d.parts) as Bucket[]) row[k] += d.parts[k];
  }
  for (const r of out) {
    r.units = partsTotal(r);
    r.share = isNewOnly(o) && r.all > 0 ? r.units / r.all : null;
  }
  return out;
}

/** Largest developments in a ward under the current selection, all years. */
export function wardDevelopments(devs: Dev[], ward: number, limit = 6): Dev[] {
  return devs
    .filter((d) => d.ward === ward && d.year != null)
    .sort((a, b) => b.units - a.units)
    .slice(0, limit);
}

/** Developments with units in the selection and years, largest first. */
export function inYears(devs: Dev[], from: number, to: number): Dev[] {
  return devs
    .filter((d) => d.year != null && d.year >= from && d.year <= to)
    .sort((a, b) => b.units - a.units || a.name.localeCompare(b.name));
}

export const LABEL_SHORT: Record<Label, string> = {
  LIHTC: "Tax credit",
  CHA: "CHA owned",
  PBV: "CHA voucher",
  TIF: "TIF",
  City: "Other City/State",
  ARO: "ARO",
};
export const labelList = (labels: Label[]) => labels.map((l) => LABEL_SHORT[l]).join(" · ");

export function devsToCsv(devs: Dev[], from: number, to: number, o: Options): string {
  const q = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const head = [
    "name", "address", "ward", "programs", "counted_as", "units_counted", "cha_units", "cha_voucher_units",
    "total_units", "year",
    "new_or_preserved", "notes", "latitude", "longitude", "years", "program_selected", "units_kind", "year_basis",
    "who_it_serves",
  ];
  const lines = [head.join(",")];
  for (const d of devs) {
    lines.push([
      q(d.name), q(d.address), d.ward ?? "", q(labelList(d.labels)), LABEL_SHORT[d.primary], d.units, d.parts.cha,
      d.pbv, d.total ?? "", d.year ?? "", d.kind, q(d.tags.join("; ")), d.ll?.[0] ?? "", d.ll?.[1] ?? "",
      `${from}-${to}`, o.program, o.kind, o.basis, o.pop,
    ].join(","));
  }
  return lines.join("\n");
}

/** Developments with financing awarded but no building permit yet, largest first. */
export function pipeline(lihtc: LihtcData, program: Program): { p: LihtcProject; labels: Label[] }[] {
  const rows: { p: LihtcProject; labels: Label[] }[] = [];
  for (const [list, src] of [[lihtc.projects, "lihtc"], [lihtc.city, "city"]] as const) {
    for (const p of list) {
      if (p.s !== "awarded") continue;
      const labels = labelsOf(p, src);
      if (inSelection(labels, program)) rows.push({ p, labels });
    }
  }
  return rows.sort((a, b) => b.p.li - a.p.li || a.p.n.localeCompare(b.p.n));
}

/** HUD's names arrive in capitals; the City's do not. */
export function titleCase(s: string): string {
  if (s !== s.toUpperCase()) return s;
  return s
    .toLowerCase()
    .replace(/\b([a-z])/g, (m) => m.toUpperCase())
    .replace(/\b(Ii|Iii|Iv|Vi|Vii|Lp|Llc|Slf|Sro|Cha)\b/g, (m) => m.toUpperCase());
}

/** Breaks scale with the program: tax-credit developments are far larger than ARO set-asides. */
export const BREAKS: Record<Program, number[]> = {
  aro: [10, 25, 50, 100, 200, 500],
  lihtc: [50, 100, 250, 500, 1000, 2000],
  cha: [10, 25, 50, 100, 250, 500],
  pbv: [10, 25, 50, 100, 250, 500],
  tif: [25, 50, 100, 250, 500, 1000],
  city: [10, 25, 50, 100, 150, 250],
  all: [50, 100, 250, 500, 1000, 2000],
};

export function breakLabels(breaks: number[]): string[] {
  const f = (n: number) => n.toLocaleString();
  return [
    `under ${f(breaks[0])}`,
    ...breaks.slice(1).map((b, i) => `${f(breaks[i])}–${f(b)}`),
    `${f(breaks[breaks.length - 1])}+`,
  ];
}

export const RAMP = PERMIT_RAMP;
export const NO_DATA = PERMIT_NO_DATA;

export function colorFor(breaks: number[]) {
  return (units: number | null): string => {
    if (units == null || units === 0) return NO_DATA;
    let i = 0;
    while (i < breaks.length && units > breaks[i]) i++;
    return RAMP[Math.min(i, RAMP.length - 1)];
  };
}

export function toCsv(rows: WardRow[], from: number, to: number, o: Options): string {
  const head = [
    "ward", "units_selected", "cha_units", "lihtc_new_units", "lihtc_preserved_units", "other_city_state_units",
    "aro_units", "cha_voucher_units", "developments", "all_new_units", "share_of_new_units", "years", "program", "units_kind",
    "year_basis", "who_it_serves",
  ];
  const lines = [head.join(",")];
  for (const r of [...rows].sort((a, b) => a.ward - b.ward)) {
    lines.push([
      r.ward, r.units, r.cha, r.lihtcNew, r.lihtcPres, r.city, r.aro, r.pbv, r.developments, r.allUnits,
      r.share == null ? "" : r.share.toFixed(5), `${from}-${to}`, o.program, o.kind, o.basis, o.pop,
    ].join(","));
  }
  return lines.join("\n");
}
