/**
 * Affordable against market-rate building, ward by ward, for the Affordable vs.
 * Market page: does affordable housing get built where the market builds?
 *
 * Built from the same counts as the Affordable Units page, new units only:
 * building is the question, and market-rate permits are new building too.
 */
import { allDevelopments, summarise, type Options } from "./lihtc";
import type { LihtcData } from "./lihtc";
import type { AroData } from "./aro";
import type { PermitsData } from "./permits";

export interface MixRow {
  ward: number;
  /** All new homes permitted: new construction plus net units from conversions. */
  all: number;
  /** All new homes less the income-restricted ones below, floored at zero. */
  market: number;
  aro: number;
  /** CHA owned, tax credit and Other City/State units. */
  subsidized: number;
  /** ARO plus subsidized: every new income-restricted unit. */
  affordable: number;
}

const NEW_ALL: Options = { program: "all", kind: "new", basis: "permit", pop: "all" };

export function wardMix(
  aro: AroData,
  lihtc: LihtcData,
  permits: PermitsData,
  from: number,
  to: number
): MixRow[] {
  const devs = allDevelopments(aro, lihtc, NEW_ALL);
  const { rows } = summarise(devs, permits, from, to, NEW_ALL);
  return rows
    .map((r) => {
      const subsidized = r.cha + r.lihtcNew + r.lihtcPres + r.city;
      return {
        ward: r.ward,
        all: r.allUnits,
        // A few wards' restricted units outnumber the permits whose unit count
        // could be read (rehabs and conversions that state none), so floor at 0.
        market: Math.max(0, r.allUnits - subsidized - r.aro),
        aro: r.aro,
        subsidized,
        affordable: r.aro + subsidized,
      };
    })
    .sort((a, b) => a.ward - b.ward);
}

export interface Concentration {
  /** The wards with the most market-rate building, most first. */
  top: number[];
  /** Share of each series that the top wards hold. */
  market: number;
  affordable: number;
}

/** How much of each kind of building the `n` biggest market-rate wards hold. */
export function concentration(rows: MixRow[], n = 10): Concentration {
  const top = [...rows].sort((a, b) => b.market - a.market || a.ward - b.ward).slice(0, n);
  const sum = (rs: MixRow[], k: keyof MixRow) => rs.reduce((s, r) => s + r[k], 0);
  const share = (k: "market" | "affordable") => {
    const all = sum(rows, k);
    return all > 0 ? sum(top, k) / all : 0;
  };
  return { top: top.map((r) => r.ward), market: share("market"), affordable: share("affordable") };
}

export function mixToCsv(rows: MixRow[], from: number, to: number): string {
  const head = ["ward", "market_rate_units", "affordable_units", "aro_units", "other_affordable_units", "all_new_units", "years"];
  return [
    head.join(","),
    ...rows.map((r) => [r.ward, r.market, r.affordable, r.aro, r.subsidized, r.all, `${from}-${to}`].join(",")),
  ].join("\n");
}
