import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
// the dual-handle range slider, toggles and table styling live with the permitting page
import "../styles/permitting.css";
import "../styles/affordable.css";
import { wardNeighborhoods } from "../lib/wards";
import { AroMap, type MapPoint } from "../components/AroMap";
import type { AroData } from "../lib/aro";
import {
  allDevelopments,
  pipeline,
  BASIS_LABELS,
  BREAKS,
  breakLabels,
  byYear,
  colorFor,
  devsToCsv,
  inYears,
  isNewOnly,
  KIND_LABELS,
  labelList,
  LABEL_SHORT,
  POP_LABELS,
  PROGRAM_LABELS,
  RAMP,
  summarise,
  titleCase,
  toCsv,
  wardDevelopments,
  type Dev,
  type LihtcData,
  type LihtcKind,
  type Options,
  type Population,
  type Program,
  type WardRow,
  type YearBasis,
} from "../lib/lihtc";
import type { PermitsData } from "../lib/permits";
import aroRaw from "../data/aro.json";
import lihtcRaw from "../data/lihtc.json";
import permitsRaw from "../data/permits.json";

const aro = aroRaw as unknown as AroData;
const lihtc = lihtcRaw as unknown as LihtcData;
const permits = permitsRaw as unknown as PermitsData;

const FIRST = permits.meta.firstYear;
const LAST = permits.meta.lastYear;
/** The current City Council was seated in May 2023. */
const CYCLE_START = 2023;

type SortKey =
  | "rank" | "ward" | "units" | "cha" | "lihtc" | "city" | "aro" | "developments" | "allUnits" | "share";

/** Dot colours match the stacked chart's segments. */
const DOT_COLOR = { cha: "#6b4c9a", new: "#00596f", pres: "#8fc9de", city: "#d9822b", aro: "#0d96c9" };
const dotKind = (d: Dev): keyof typeof DOT_COLOR =>
  d.primary === "CHA" ? "cha"
    : d.primary === "ARO" ? "aro"
      : d.primary === "City" ? "city"
        : d.kind === "preserved" ? "pres" : "new";
const dotColor = (d: Dev) => DOT_COLOR[dotKind(d)];

const download = (text: string, name: string) => {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
};

const pct = (v: number | null, digits = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

const ESTIMATED = lihtc.projects.filter((p) => p.s === "estimate").length;
const CITY_ESTIMATED = lihtc.city.filter((p) => p.s === "estimate").length;
const NSP = lihtc.city.filter((p) => p.id.startsWith("NSP-"));
const NSP_UNITS = NSP.reduce((t, p) => t + p.li, 0);
const NSP_ALL_UNITS = NSP.reduce((t, p) => t + p.u, 0);
const CITY_SET_ASIDE_UNITS = lihtc.meta.citySetAside.reduce((s, c) => s + c.u, 0);
const TIF_DEVS =
  [...lihtc.projects, ...lihtc.city].filter((p) => p.tif && p.s !== "awarded" && p.s !== "preSeries").length +
  Object.values(aro.projects).flat().filter((b) => b.tif).length;

/** Developments in the series with CHA project-based vouchers, and their voucher units. */
const PBV_SERIES = [
  ...[...lihtc.projects, ...lihtc.city].filter((p) => p.pbv && p.s !== "awarded" && p.s !== "preSeries"),
  ...Object.values(aro.projects).flat().filter((b) => b.pbv),
];
const PBV_DEVS = PBV_SERIES.length;
const PBV_UNITS = PBV_SERIES.reduce((n, p) => n + (p.pbv ?? 0), 0);

/** A round top for a y-axis and its 2-4 ticks, chosen to waste the least headroom. */
function niceScale(max: number): { top: number; ticks: number[] } {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  let best: { top: number; step: number } | null = null;
  for (const n of [2, 3, 4]) {
    const raw = max / n;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const f = [1, 2, 2.5, 3, 4, 5, 10].find((c) => c * mag >= raw)!;
    const step = f * mag;
    const top = step * Math.ceil(max / step - 1e-9);
    if (!best || top < best.top) best = { top, step };
  }
  const ticks: number[] = [];
  for (let t = 0; t <= best!.top + 1e-9; t += best!.step) ticks.push(t);
  return { top: best!.top, ticks };
}

/**
 * A year column chart with a light y-axis and a hover label. `bar` draws the
 * inside of each column (segments for the stacked chart); `tip` is the label.
 */
function YearChart<Y extends { year: number }>(props: {
  rows: Y[];
  value: (y: Y) => number;
  fmtTick: (v: number) => string;
  bar: (y: Y, inRange: boolean) => ReactNode;
  tip: (y: Y) => ReactNode;
  from: number;
  to: number;
  label: string;
}) {
  const { rows, value, fmtTick, bar, tip, from, to, label } = props;
  const [hover, setHover] = useState<number | null>(null);
  const { top, ticks } = niceScale(Math.max(0, ...rows.map(value)));
  const hi = hover == null ? -1 : rows.findIndex((r) => r.year === hover);
  const n = rows.length;
  return (
    <div className="af-plot">
      <div className="af-yaxis" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t} style={{ bottom: `${(t / top) * 100}%` }}>{fmtTick(t)}</span>
        ))}
      </div>
      <div className="af-plot-area" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <div key={t} className="af-grid" style={{ bottom: `${(t / top) * 100}%` }} />
        ))}
        <div className="af-bars" role="img" aria-label={label}>
          {rows.map((y) => {
            const v = value(y);
            const inRange = y.year >= from && y.year <= to;
            return (
              <div key={y.year} className="af-bar-slot" onMouseEnter={() => setHover(y.year)}>
                <div
                  className={`af-bar-wrap${hover === y.year ? " hov" : ""}`}
                  style={{ height: `${Math.max(v > 0 ? 2 : 0, (v / top) * 100)}%` }}
                >
                  {bar(y, inRange)}
                </div>
              </div>
            );
          })}
        </div>
        {hi >= 0 && (
          <div
            className={`af-tip${hi < 3 ? " left" : hi > n - 4 ? " right" : ""}`}
            style={{
              left: `${((hi + 0.5) / n) * 100}%`,
              bottom: `${Math.min(100, (value(rows[hi]) / top) * 100)}%`,
            }}
          >
            {tip(rows[hi])}
          </div>
        )}
      </div>
      <div />
      <div className="af-axis">
        <span>{rows[0]?.year}</span>
        <span>{rows[n - 1]?.year}*</span>
      </div>
    </div>
  );
}

export function AffordableUnits() {
  const [from, setFrom] = useState(FIRST);
  const [to, setTo] = useState(LAST);
  const [program, setProgram] = useState<Program>("all");
  const [kind, setKind] = useState<LihtcKind>("new");
  const [basisChoice, setBasis] = useState<YearBasis>("permit");
  const [pop, setPop] = useState<Population>("all");
  const [selectedWard, setSelectedWard] = useState<number | null>(null);
  const [hoveredWard, setHoveredWard] = useState<number | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "units", desc: true });
  const [showDots, setShowDots] = useState(true);
  const [query, setQuery] = useState("");

  // ARO has no opening year, so only a tax-credit-only view can switch basis.
  const basis: YearBasis = program === "lihtc" ? basisChoice : "permit";
  // ARO units are all general occupancy, so the ARO view ignores the population filter.
  const popEff: Population = program === "aro" ? "all" : pop;
  const opts: Options = useMemo(
    () => ({ program, kind, basis, pop: popEff }),
    [program, kind, basis, popEff]
  );
  const newOnly = isNewOnly(opts);
  const showKind = program !== "aro";
  const pipe = useMemo(() => pipeline(lihtc, program), [program]);
  const pipeUnits = pipe.reduce((s, r) => s + r.p.li, 0);

  const everything = useMemo(() => allDevelopments(aro, lihtc, opts), [opts]);
  const { rows, total, totalAll, totals } = useMemo(
    () => summarise(everything, permits, from, to, opts),
    [everything, from, to, opts]
  );
  const years = useMemo(() => byYear(everything, permits, opts), [everything, opts]);
  const devs = useMemo(() => inYears(everything, from, to), [everything, from, to]);
  // Which buckets the selection can hold, for the chart key, map key and table.
  const has = {
    cha: totals.cha > 0 || program === "cha",
    lihtcNew: totals.lihtcNew > 0,
    lihtcPres: totals.lihtcPres > 0,
    city: totals.city > 0,
    aro: totals.aro > 0,
    pbv: totals.pbv > 0 || program === "pbv",
  };
  // The map key follows the dots drawn, which in the voucher view keep their building's colour.
  const dotKinds = useMemo(() => new Set(devs.map(dotKind)), [devs]);
  const showCity = program === "all" || program === "city";
  const points: MapPoint[] = useMemo(
    () =>
      showDots
        ? devs.filter((d) => d.ll).map((d) => ({
            id: d.id, ll: d.ll!, units: d.units, color: dotColor(d),
            label: `${d.name}: ${d.units.toLocaleString()} units (${labelList(d.labels)}), ${d.year}`,
          }))
        : [],
    [devs, showDots]
  );

  const breaks = BREAKS[program];
  const color = useMemo(() => colorFor(breaks), [breaks]);
  const labels = useMemo(() => breakLabels(breaks), [breaks]);


  const sortedRows = useMemo(() => {
    const dir = sort.desc ? -1 : 1;
    const val = (r: WardRow) => (sort.key === "lihtc" ? r.lihtcNew + r.lihtcPres : r[sort.key]);
    return [...rows].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (av == null && bv == null) return a.ward - b.ward;
      if (av == null) return 1;
      if (bv == null) return -1;
      return av === bv ? a.ward - b.ward : (av < bv ? -1 : 1) * dir;
    });
  }, [rows, sort]);

  const toggleSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key !== "ward" }));

  const sortHead = (key: SortKey, label: string) => {
    const on = sort.key === key;
    return (
      <button
        type="button"
        className={on ? "pm-sort on" : "pm-sort"}
        onClick={() => toggleSort(key)}
        aria-sort={on ? (sort.desc ? "descending" : "ascending") : "none"}
      >
        {label}
        <span className="pm-sort-arrow">{on ? (sort.desc ? "↓" : "↑") : ""}</span>
      </button>
    );
  };

  const downloadCsv = () =>
    download(toCsv(rows, from, to, opts), `chicago-affordable-units-by-ward-${program}-${from}-${to}.csv`);

  const active = selectedWard ?? hoveredWard;
  const activeRow: WardRow | undefined = active != null ? rows.find((r) => r.ward === active) : undefined;
  const wardsWithAny = rows.filter((r) => r.units > 0).length;

  const q = query.trim().toLowerCase();
  const listed = devs.filter(
    (d) =>
      (selectedWard == null || d.ward === selectedWard) &&
      (!q || d.name.toLowerCase().includes(q) || d.address.toLowerCase().includes(q) || String(d.ward) === q)
  );
  const downloadDevs = () =>
    download(devsToCsv(listed, from, to, opts), `chicago-affordable-developments-${program}-${from}-${to}.csv`);

  const setFromSafe = (v: number) => setFrom(Math.min(v, to));
  const setToSafe = (v: number) => setTo(Math.max(v, from));
  const span = LAST - FIRST;
  const lo = ((from - FIRST) / span) * 100;
  const hi = ((to - FIRST) / span) * 100;
  const isFull = from === FIRST && to === LAST;

  const kindWord = kind === "preserved" ? "preserved " : kind === "new" ? "new " : "";
  const unitsLabel0 =
    program === "aro" ? "ARO units"
      : program === "lihtc" ? `${kindWord}tax-credit units`
        : program === "cha" ? `${kindWord}CHA-owned units`
          : program === "pbv" ? `${kindWord}units with CHA project-based vouchers`
          : program === "tif" ? `${kindWord}units in TIF-funded developments`
            : program === "city" ? `${kindWord}Other City/State units`
              : kind === "both" ? "affordable units, new + preserved" : kind === "preserved" ? "preserved units" : "new affordable units";
  const unitsLabel = popEff === "all" ? unitsLabel0 : `${unitsLabel0} (${POP_LABELS[popEff].toLowerCase()})`;

  const toggle = <T extends string>(
    label: string,
    value: T,
    labels: Record<T, string>,
    set: (v: T) => void
  ) => (
    <div className="pm-toggle" role="group" aria-label={label}>
      {(Object.keys(labels) as T[]).map((k) => (
        <button key={k} type="button" className={value === k ? "pm-tog on" : "pm-tog"} onClick={() => set(k)}>
          {labels[k]}
        </button>
      ))}
    </div>
  );

  return (
    <div className="permitting affordable">
      <header className="pm-header">
        <p className="eyebrow">What gets built affordable</p>
        <h1 className="page-title">Affordable Units</h1>
        <p className="page-lede">
          New (and preserved) income-restricted homes permitted in Chicago since {FIRST}. This includes several
          categories of housing, including Low Income Housing Tax Credit (LIHTC) financed projects,
          Affordable Requirements Ordinance (ARO) obligated developments, Chicago Housing Authority
          (CHA) projects, and other City- and State-funded developments.
        </p>
      </header>

      <section className="pm-controls">
        <div className="pm-range">
          <div className="af-switches">
            <div className="pm-basis">
              <span className="eyebrow">Program</span>
              {toggle("Program", program, PROGRAM_LABELS, setProgram)}
            </div>
            {showKind && (
              <div className="pm-basis">
                <span className="eyebrow">New/Preserved</span>
                {toggle("New or preserved units", kind, KIND_LABELS, setKind)}
              </div>
            )}
            {program !== "aro" && (
              <div className="pm-basis">
                <span className="eyebrow">Who it serves</span>
                {toggle("Who it serves", pop, POP_LABELS, setPop)}
              </div>
            )}
            {program === "lihtc" && (
              <div className="pm-basis">
                <span className="eyebrow">Date by</span>
                {toggle("Date developments by", basisChoice, BASIS_LABELS, setBasis)}
              </div>
            )}
            {(popEff !== "all" || (program === "lihtc" && basis === "opened")) && (
              <p className="pm-basis-hint">
                {popEff === "general" && "General: open to any eligible household, not age-restricted or built for a special-needs population; all ARO units are general. "}
                {popEff === "senior" && "Senior: age-restricted buildings (55+ or 62+), including supportive living facilities. "}
                {popEff === "supportive" && "Supportive: buildings for people leaving homelessness or living with a disability, usually with services on site. "}
                {program === "lihtc" && basis === "opened" &&
                  "Dated by the year each development opened; recent projects still under construction are not yet counted."}
              </p>
            )}
          </div>

          <div className="pm-range-head">
            <span className="eyebrow">Years</span>
            <strong className="pm-range-val">
              {from}–{to}
              {to === LAST && <span className="pm-partial"> (through {permits.meta.lastDate})</span>}
            </strong>
            {!isFull && (
              <button className="pm-link" onClick={() => { setFrom(FIRST); setTo(LAST); }}>
                Reset to all years
              </button>
            )}
            {isFull && (
              <button className="pm-link" onClick={() => { setFrom(CYCLE_START); setTo(LAST); }}>
                Current Council only
              </button>
            )}
          </div>

          <div className="pm-slider" style={{ ["--lo" as string]: `${lo}%`, ["--hi" as string]: `${hi}%` }}>
            <div className="pm-slider-track" />
            <div className="pm-slider-fill" />
            <input type="range" min={FIRST} max={LAST} value={from} aria-label="First year"
              onChange={(e) => setFromSafe(Number(e.target.value))} />
            <input type="range" min={FIRST} max={LAST} value={to} aria-label="Last year"
              onChange={(e) => setToSafe(Number(e.target.value))} />
          </div>
        </div>

        <div className="pm-summary">
          <div className="pm-stat">
            <span className="pm-stat-value">{total.toLocaleString()}</span>
            <span className="pm-stat-label">{unitsLabel}</span>
          </div>
          {newOnly && (
            <div className="pm-stat">
              <span className="pm-stat-value">{pct(totalAll > 0 ? total / totalAll : null)}</span>
              <span className="pm-stat-label">of all new units</span>
            </div>
          )}
          <div className="pm-stat">
            <span className="pm-stat-value">{wardsWithAny}</span>
            <span className="pm-stat-label">wards of 50</span>
          </div>
          <button className="pm-export" onClick={downloadCsv}>Download CSV</button>
        </div>
        {(program === "all" || program === "tif") && (
          <p className="af-caveat af-split">
            {[
              has.cha && `${totals.cha.toLocaleString()} CHA owned`,
              has.lihtcNew && `${totals.lihtcNew.toLocaleString()} ${kind === "both" ? "new " : ""}tax-credit`,
              has.lihtcPres && `${totals.lihtcPres.toLocaleString()} ${kind === "both" ? "preserved " : ""}tax-credit`,
              has.city && `${totals.city.toLocaleString()} Other City/State`,
              has.aro && `${totals.aro.toLocaleString()} ARO`,
            ].filter(Boolean).join(" · ")}.
            {program === "all"
              ? " Each unit is counted once, in the first of these that applies: CHA-owned units, then the rest of a tax-credit development, then Other City/State, then ARO. The program buttons above show every development a label applies to, so they overlap and do not add up to this total."
              : " TIF is money added to developments that are also tax-credit, Other City/State or ARO; their units are split the same way."}
          </p>
        )}
        {program === "pbv" && (
          <p className="af-caveat">
            A CHA project-based voucher is a rent subsidy tied to the unit, not the tenant: CHA contracts
            with the building&rsquo;s owner, and when a household moves out the subsidy stays for the next
            eligible one. Only project-based vouchers are shown here; tenant-based (Housing Choice)
            vouchers, which move with the household, are not. Every one of these units is already
            counted under CHA owned, tax credit, Other City/State or ARO, so this view shows where
            CHA&rsquo;s vouchers sit rather than adding units.
          </p>
        )}
        {(pipe.length > 0 || showCity) && (
          <p className="af-caveat">
            Several post-{FIRST} developments are not counted in the above:{" "}
            {[
              pipe.length > 0 && (
                <>{pipeUnits.toLocaleString()} units in {pipe.length} developments with financing awarded but
                  no building permit yet, which will get counted once permitted (listed below)</>
              ),
              showCity && (
                <>{lihtc.meta.citySetAside.length} Other City/State projects or groups
                  ({CITY_SET_ASIDE_UNITS.toLocaleString()} units) left out because they were refinancing only,
                  for-sale, or could not be placed; see the notes below</>
              ),
            ]
              .filter(Boolean)
              .map((part, i, all) => (
                <span key={i}>{all.length > 1 ? `(${i + 1}) ` : ""}{part}{i < all.length - 1 ? "; " : "."} </span>
              ))}
          </p>
        )}
      </section>

      {/* Two measures on different scales get two charts, never two y-axes. */}
      <section className="af-charts">
        <figure className="af-chart">
          <figcaption>
            <h2>Units by year</h2>
            <p>
              {program === "cha" || program === "pbv" || program === "city" || program === "aro"
                ? "Hover a bar for the count."
                : "Stacked by where each unit is counted; no unit is in more than one segment."}
            </p>
            {program !== "cha" && program !== "pbv" && program !== "city" && program !== "aro" && (
              <div className="af-key">
                {has.cha && <span><i className="af-sw cha" /> CHA owned</span>}
                {has.lihtcNew && <span><i className="af-sw new" /> Tax credit{kind === "both" ? ", new" : ""}</span>}
                {has.lihtcPres && <span><i className="af-sw pres" /> Tax credit{kind === "both" ? ", preserved" : ""}</span>}
                {has.city && <span><i className="af-sw city" /> Other City/State</span>}
                {has.aro && <span><i className="af-sw aro" /> ARO</span>}
              </div>
            )}
          </figcaption>
          <YearChart
            rows={years}
            value={(y) => y.units}
            fmtTick={(v) => v.toLocaleString()}
            from={from}
            to={to}
            label="Affordable units per year"
            bar={(y, inRange) => {
              const seg = (v: number, cls: string) =>
                v > 0 ? <div className={`af-seg ${cls}`} style={{ height: `${(v / y.units) * 100}%` }} /> : null;
              return (
                <div className={`af-bar stack${inRange ? " in" : ""}`}>
                  {seg(y.aro, "aro")}
                  {seg(y.city, "city")}
                  {seg(y.lihtcPres, "pres")}
                  {seg(y.lihtcNew, "new")}
                  {seg(y.cha, "cha")}
                  {seg(y.pbv, "pbv")}
                </div>
              );
            }}
            tip={(y) => (
              <>
                <strong>{y.year}: {y.units.toLocaleString()} units</strong>
                {[
                  ["cha", "CHA owned", y.cha],
                  ["pbv", "CHA voucher", y.pbv],
                  ["new", kind === "both" ? "Tax credit, new" : "Tax credit", y.lihtcNew],
                  ["pres", kind === "both" ? "Tax credit, preserved" : "Tax credit", y.lihtcPres],
                  ["city", "Other City/State", y.city],
                  ["aro", "ARO", y.aro],
                ]
                  .filter(([, , v]) => (v as number) > 0)
                  .map(([cls, name, v]) => (
                    <span key={cls as string} className="af-tip-row">
                      <i className={`af-sw ${cls}`} /> {name as string} {(v as number).toLocaleString()}
                    </span>
                  ))}
              </>
            )}
          />
        </figure>

        <figure className="af-chart">
          <figcaption>
            <h2>As a share of all new units</h2>
            <p>
              {newOnly
                ? "Against every new housing unit permitted citywide that year, in new buildings and conversions alike."
                : "Shown for new units only: preserved units are existing homes, so they are not part of new building."}
            </p>
          </figcaption>
          {newOnly ? (
            <>
              <YearChart
                rows={years}
                value={(y) => y.share ?? 0}
                fmtTick={(v) => pct(v, 0)}
                from={from}
                to={to}
                label="Share of all new units per year"
                bar={(_y, inRange) => <div className={`af-bar share${inRange ? " in" : ""}`} />}
                tip={(y) => (
                  <>
                    <strong>{y.year}: {pct(y.share)}</strong>
                    <span className="af-tip-row">{y.units.toLocaleString()} of {y.all.toLocaleString()} new units</span>
                  </>
                )}
              />
            </>
          ) : (
            <p className="af-empty">Switch to new units to see this measure.</p>
          )}
        </figure>
      </section>
      <p className="pm-footnote af-footnote">* Note that {LAST} data is only through {permits.meta.lastDate}.</p>

      <section className="pm-body">
        <div className="pm-map">
          <AroMap
            rows={rows}
            points={points}
            fillOpacity={showDots ? 0.55 : 1}
            colorFor={color}
            selectedWard={selectedWard}
            hoveredWard={hoveredWard}
            onSelectWard={setSelectedWard}
            onHoverWard={setHoveredWard}
          />
          <div className="pm-overlay">
            <div className="pm-legend">
              <span className="pm-legend-title">{unitsLabel}, {from}–{to}</span>
              <div className="pm-legend-steps">
                {RAMP.map((c, i) => (
                  <div key={c} className="pm-legend-step">
                    <span className="pm-chip" style={{ background: c }} />
                    <span className="pm-chip-label">{labels[i]}</span>
                  </div>
                ))}
              </div>
              <label className="af-dots-toggle">
                <input type="checkbox" checked={showDots} onChange={(e) => setShowDots(e.target.checked)} />
                Show each development
              </label>
              {showDots && (
                <div className="af-dot-key">
                  {dotKinds.has("cha") && <span><i style={{ background: DOT_COLOR.cha }} /> CHA owned</span>}
                  {dotKinds.has("new") && <span><i style={{ background: DOT_COLOR.new }} /> Tax credit</span>}
                  {dotKinds.has("pres") && <span><i style={{ background: DOT_COLOR.pres }} /> Tax credit, preserved</span>}
                  {dotKinds.has("city") && <span><i style={{ background: DOT_COLOR.city }} /> Other City/State</span>}
                  {dotKinds.has("aro") && <span><i style={{ background: DOT_COLOR.aro }} /> ARO</span>}
                  <span className="af-dot-size">Size: units</span>
                </div>
              )}
            </div>
            {activeRow && (
              <div className="pm-card">
                <div className="pm-card-head">
                  <strong>Ward {activeRow.ward}</strong>
                  <span className="pm-card-rank">#{activeRow.rank} of 50</span>
                </div>
                <div className="pm-card-hood">{wardNeighborhoods(activeRow.ward)}</div>
                <div className="pm-card-grid">
                  <div>
                    <span className="pm-card-label">Units</span>
                    <span className="pm-card-value">{activeRow.units.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="pm-card-label">All new units</span>
                    <span className="pm-card-value">{activeRow.allUnits.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="pm-card-label">{newOnly ? "Share" : "Developments"}</span>
                    <span className="pm-card-value">
                      {newOnly ? pct(activeRow.share) : activeRow.developments}
                    </span>
                  </div>
                </div>
                {(() => {
                  const devs = wardDevelopments(inYears(everything, from, to), activeRow.ward);
                  return (
                    <div className="pm-projects">
                      <span className="pm-card-label">Largest developments, {from === to ? from : `${from}–${to}`}</span>
                      {devs.length === 0 ? (
                        <p className="pm-projects-none">None in this ward in these years.</p>
                      ) : (
                        <ul>
                          {devs.map((d) => (
                            <li key={d.id}>
                              <span className="pm-proj-units">{d.units}</span>
                              <span className="pm-proj-addr">
                                {d.name}
                                {d.labels.map((l) => <span key={l} className="af-tag">{LABEL_SHORT[l]}</span>)}
                                {d.kind === "preserved" && d.primary !== "ARO" && <span className="af-tag soft">preserved</span>}
                                {d.tags.filter((t) => !/CHA (owned|voucher units?)$/.test(t)).map((t) => <span key={t} className="af-tag soft">{t}</span>)}
                              </span>
                              <span className="pm-proj-date">{d.year ?? "—"}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>

        <div className="pm-table-wrap">
          <table className="pm-table">
            <thead>
              <tr>
                <th className="num">{sortHead("rank", "#")}</th>
                <th className="num">{sortHead("ward", "Ward")}</th>
                <th className="num">{sortHead("units", "Units")}</th>
                {program === "all" && <th className="num" title="CHA-owned units">{sortHead("cha", "CHA")}</th>}
                {program === "all" && <th className="num" title="Tax-credit units other than CHA-owned units">{sortHead("lihtc", "LIHTC")}</th>}
                {program === "all" && <th className="num" title="Other City- and State-funded units, no tax credits">{sortHead("city", "Oth.")}</th>}
                {program === "all" && <th className="num" title="ARO units">{sortHead("aro", "ARO")}</th>}
                {/* The "All" view spends this column on the per-program split; the ward card lists the developments. */}
                {program !== "all" && (
                  <th className="num" title="Developments with units in these years">{sortHead("developments", "Devs")}</th>
                )}
                {/* The "All" view spends this column on the per-bucket split too; the ward card still shows it. */}
                {newOnly && program !== "all" && (
                  <th className="num" title="All new housing units permitted in the ward: new construction plus net units from conversions">
                    {sortHead("allUnits", "All new")}
                  </th>
                )}
                {newOnly && <th className="num" title="Share of all new units">{sortHead("share", "Share")}</th>}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((r) => (
                <tr
                  key={r.ward}
                  className={r.ward === selectedWard ? "pm-row sel" : r.ward === hoveredWard ? "pm-row hov" : "pm-row"}
                  onMouseEnter={() => setHoveredWard(r.ward)}
                  onMouseLeave={() => setHoveredWard(null)}
                  onClick={() => setSelectedWard(selectedWard === r.ward ? null : r.ward)}
                >
                  <td className="num muted">{r.rank}</td>
                  <td className="num">{r.ward}</td>
                  <td className="num strong">{r.units.toLocaleString()}</td>
                  {program === "all" && <td className="num muted">{r.cha.toLocaleString()}</td>}
                  {program === "all" && <td className="num muted">{(r.lihtcNew + r.lihtcPres).toLocaleString()}</td>}
                  {program === "all" && <td className="num muted">{r.city.toLocaleString()}</td>}
                  {program === "all" && <td className="num muted">{r.aro.toLocaleString()}</td>}
                  {program !== "all" && <td className="num muted">{r.developments || "—"}</td>}
                  {newOnly && program !== "all" && <td className="num muted">{r.allUnits.toLocaleString()}</td>}
                  {newOnly && <td className="num">{r.share == null || r.units === 0 ? "—" : pct(r.share)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="af-ami af-devlist">
        <div className="af-devlist-head">
          <div>
            <h2 className="af-ami-title">Every development</h2>
            <p className="af-ami-lede">
              {listed.length.toLocaleString()} developments with {unitsLabel}, {from}–{to}
              {selectedWard != null && <> in Ward {selectedWard}</>}, under the choices above.
              {selectedWard != null && (
                <> <button className="pm-link" onClick={() => setSelectedWard(null)}>Show all wards</button></>
              )}
            </p>
          </div>
          <div className="af-devlist-tools">
            <input
              type="search"
              className="af-search"
              placeholder="Search name, address or ward"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search developments"
            />
            <button className="pm-export" onClick={downloadDevs}>Download CSV</button>
          </div>
        </div>
        <div className="af-pipe-wrap af-devlist-wrap">
          <table className="pm-table af-pipe">
            <thead>
              <tr>
                <th>Development</th>
                <th className="num">Ward</th>
                <th>Programs</th>
                <th className="num">Units</th>
                <th className="num">Year</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {listed.map((d) => (
                <tr key={d.id} className="pm-row">
                  <td>
                    {d.name}
                    {d.address && d.address !== d.name && <span className="af-addr">{d.address}</span>}
                  </td>
                  <td className="num">{d.ward ?? "—"}</td>
                  <td className="muted">{labelList(d.labels)}</td>
                  <td className="num strong">
                    {d.units.toLocaleString()}
                    {d.total != null && d.total > d.units && <span className="af-of"> of {d.total.toLocaleString()}</span>}
                  </td>
                  <td className="num">{d.year ?? "—"}</td>
                  <td className="muted">
                    {[d.primary !== "ARO" && d.kind === "preserved" ? "preserved" : "", ...d.tags].filter(Boolean).join(" · ")}
                  </td>
                </tr>
              ))}
              {listed.length === 0 && (
                <tr><td colSpan={6} className="muted">No developments match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {pipe.length > 0 && (
        <section className="af-ami af-pipeline">
          <h2 className="af-ami-title">In the pipeline</h2>
          <p className="af-ami-lede">
            {pipe.length} developments with {pipeUnits.toLocaleString()} income-restricted units have
            {program === "lihtc" ? " been awarded tax credits" : program === "city" ? " City or State financing approved" : " tax credits or City or State financing awarded"}{" "}
            but no building permit yet. They are not in any figure above. Some will take years; a few may
            never be built.
          </p>
          <div className="af-pipe-wrap">
            <table className="pm-table af-pipe">
              <thead>
                <tr>
                  <th>Development</th>
                  <th className="num">Ward</th>
                  <th className="num">Restricted units</th>
                  <th>Programs</th>
                  <th>Type</th>
                </tr>
              </thead>
              <tbody>
                {pipe.map(({ p, labels }) => (
                  <tr key={p.id} className="pm-row">
                    <td>
                      {titleCase(p.n)}
                      <span className="af-addr">{p.a}</span>
                    </td>
                    <td className="num">{p.w ?? "—"}</td>
                    <td className="num strong">{p.li.toLocaleString()}</td>
                    <td className="muted">{labelList(labels)}</td>
                    <td className="muted">
                      {[p.new > 0 && p.pres > 0 ? "part preserved" : p.pres > 0 ? "preserved" : "new",
                        p.mix ? "mixed-income" : "",
                        p.fu ? "status unconfirmed" : ""]
                        .filter(Boolean).join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}


      <div className="pm-method">
        <p>
          <strong>What is counted.</strong> Income-restricted rental homes, each development tagged with
          every program that applies to it. The federal Low-Income Housing Tax Credit (LIHTC)
          finances most of the city&rsquo;s subsidized affordable housing, allocated by the City and
          by the Illinois Housing Development Authority; those developments land where public money
          sends them. Chicago Housing Authority (CHA) public housing units sit inside many of them,
          and CHA project-based vouchers subsidize the rent on units in many more. Tax-increment
          financing (TIF) is City money added to developments of every kind. Other City- and
          State-funded developments used no tax credits. The Affordable Requirements Ordinance (ARO)
          requires developments of ten or more units that need a zoning change, city land or city
          money to set aside a share of their homes at restricted rents; those units are produced by
          the market and land where it builds. Because one development can carry several labels,
          the program buttons overlap. The &ldquo;All&rdquo; totals, the stacked chart and the map
          count each unit once, in the first bucket that applies: CHA-owned units, then the rest of a
          tax-credit development, then Other City/State, then ARO. None of the City&rsquo;s ARO
          buildings is a tax-credit development. Not covered: public housing built or bought by CHA
          without tax credits, for-sale programs, and housing that is simply cheap without any
          restriction.
        </p>
        <p>
          <strong>Other City/State developments.</strong> {lihtc.meta.cityProjects} developments
          with {lihtc.meta.cityUnits.toLocaleString()} income-restricted units and no federal tax
          credits: City multifamily loans, tax-increment financing and land, from the commitment
          tables in the City&rsquo;s year-end housing reports (2010&ndash;2023) and later City
          announcements and City Council ordinances (2023&ndash;2026), and projects funded by the
          Illinois Housing Development Authority
          (HOME, trust funds, donation credits), from its closing reports for 2018&ndash;2025 and
          its 2025 progress report. A deal that puts new income limits on existing homes counts
          even without a renovation. Only units restricted at or below 80% of area median income
          are counted, not market-rate units or units restricted at higher incomes in the same
          building. This includes {NSP.length} rental buildings the City bought and rehabbed in
          2010&ndash;2013 with federal Neighborhood Stabilization Program money: NSP units are
          restricted either at or below 50% or at or below 120% of area median income, so{" "}
          {NSP_UNITS.toLocaleString()} of their {NSP_ALL_UNITS.toLocaleString()} units are counted.
          For most of these buildings the split is not published and is estimated from the
          City&rsquo;s yearly NSP totals by income. Each is dated by
          its building permit{CITY_ESTIMATED > 0 && <>; {CITY_ESTIMATED} with no rehab permit on record{" "}
          {CITY_ESTIMATED === 1 ? "carries" : "carry"} the year of the City&rsquo;s commitment
          (2012, mid-program, for NSP buildings)</>}. Left out: {lihtc.meta.citySetAside.map((c) => c.n).join("; ")}
          {" "}(refinancing or repair grants with no units built, rehabbed or newly restricted, homes
          for sale, or no site that could be placed). The City&rsquo;s TIF purchase-rehab program
          reports unit totals without addresses (64 units in 2022, 28 in 2023), so those cannot be
          mapped and are not counted. Coverage after 2023 is thinner, because the City&rsquo;s
          year-end report for later years is not yet out.
        </p>
        <p>
          <strong>Tax-credit developments.</strong> HUD&rsquo;s{" "}
          <a href={lihtc.meta.hudUrl} target="_blank" rel="noreferrer">LIHTC database</a> lists
          every project but lags several years and misses some entirely, so it was reconciled
          against the City&rsquo;s funding-round awards and year-end housing reports (2010&ndash;2023)
          and IHDA&rsquo;s award lists, closing reports and bond hearing notices (2019&ndash;2026),
          adding {lihtc.projects.filter((p) => !/^IL[AB]/.test(p.id)).length} developments HUD does not
          have or has wrong. Each development is dated by its building permit, found by address and
          checked by hand where the match was uncertain; {ESTIMATED} with no permit on record carry an
          estimated year. Units counted are low-income units only, not market-rate units in
          mixed-income buildings. A development is <em>new</em> if it built homes, either new
          construction or a building that was not housing before (including the rehab of long-vacant
          buildings), and <em>preserved</em> if it rehabbed existing affordable housing and renewed
          its rent limits, which adds no homes. Where CHA housing is part of a development it is
          tagged, not counted separately. Dates are permit years by default; a tax-credit-only view
          can switch to the year each development opened, where known. A random sample of 25
          dated developments was re-checked by hand: every permit and every new-or-preserved call
          held up, but three low-income counts were too low, because in some mixed-income
          developments HUD counts only the tax-credit units and leaves out the CHA public-housing
          units beside them. Those were corrected from the City&rsquo;s year-end reports, along
          with two more of the same kind found by checking every CHA development.
        </p>
        <p>
          <strong>CHA owned.</strong> Chicago Housing Authority public housing: units in CHA&rsquo;s own
          buildings, and CHA units inside mixed-income developments, often on former CHA sites. Most
          added since {FIRST} sits inside tax-credit developments. The CHA owned button shows just
          those units; in the &ldquo;All&rdquo; view they are counted as CHA rather than as tax-credit
          units. Each development&rsquo;s CHA
          count comes from the City&rsquo;s report of commitments under CHA&rsquo;s Plan for
          Transformation and Plan Forward, HUD&rsquo;s public-housing development file and CHA&rsquo;s
          annual Moving to Work reports; where HUD splits one phase into several records, CHA&rsquo;s
          total is divided among them in proportion to their low-income units. CHA housing added
          without tax credits is not counted: CHA&rsquo;s reports show 218 long-vacant Altgeld Gardens
          units rehabbed in 2016 and 98 units in existing buildings it bought in 2016&ndash;18, but its
          reports for 2010&ndash;2015 are not published online.
        </p>
        <p>
          <strong>CHA voucher.</strong> A CHA project-based voucher is a rent subsidy tied to a unit, not
          to a tenant: CHA contracts with the building&rsquo;s owner, the household pays about 30% of
          its income, and when it moves out the subsidy stays with the unit for the next eligible
          household. Only project-based vouchers are included; tenant-based (Housing Choice) vouchers,
          which move with the household, cannot be tied to a building. The label covers regular
          project-based vouchers, RAD conversions (where CHA&rsquo;s own public housing subsidy moves to
          a voucher contract, so these overlap CHA owned) and Mod Rehab contracts. It is a label, like
          TIF: every voucher unit is already counted under CHA owned, tax credit, Other City/State or
          ARO, and the CHA voucher button shows each building&rsquo;s voucher units. Source: CHA&rsquo;s
          FY2027 Moving to Work plan (draft for public comment, July 2026), which lists every property
          with vouchers under contract or agreement, matched to these developments by address and
          name; {PBV_DEVS} developments in the series carry it, with {PBV_UNITS.toLocaleString()} voucher units. A
          building&rsquo;s voucher count is capped at the units counted here.
        </p>
        <p>
          <strong>TIF.</strong> {TIF_DEVS} developments in the series received City tax-increment
          financing: tax-credit, Other City/State and ARO alike. TIF is never a development&rsquo;s
          only source, so it is a label, not a bucket of its own; every TIF-funded unit is counted
          under one of the other programs. Tagged from the TIF commitment tables in the City&rsquo;s
          year-end housing reports (2010&ndash;2023), the City&rsquo;s list of TIF-funded
          redevelopment agreements, City Council ordinances (2023&ndash;2026) and the developments&rsquo;
          own records. For ARO buildings, only those matched to a TIF agreement by location are
          tagged, so the TIF count for ARO is a floor.
        </p>
        <p>
          <strong>Who it serves.</strong> Each tax-credit and Other City/State development is classed by
          building as senior (age-restricted, 55+ or 62+, including supportive living facilities),
          supportive (for people leaving homelessness or living with a disability, usually with
          services on site, including nonprofit single-room-occupancy buildings) or general (open to
          any eligible household). The classes come from IHDA&rsquo;s elderly and non-elderly
          designations, the City&rsquo;s project types, CHA&rsquo;s voucher listings, HUD&rsquo;s
          assisted-housing client groups and the developers&rsquo; own descriptions. A building that
          mixes populations, such as a senior tower with family townhomes beside it, is general, as
          are all ARO units. Senior buildings with no tax credits and no City money, such as many
          HUD Section 202 buildings, are not covered.
        </p>
        <p>
          <strong>ARO units.</strong> From the Department of Housing&rsquo;s{" "}
          <a href={aro.meta.sourceUrl} target="_blank" rel="noreferrer">ARO rental buildings map</a>,
          a snapshot dated {aro.meta.vintage} covering {aro.meta.buildings} buildings and{" "}
          {aro.meta.units.toLocaleString()} units. The source carries no dates, so each building is
          dated from its building permit, matched by address: exactly for{" "}
          {aro.meta.matchMethods.exact} buildings, within {aro.meta.addrTolerance} house numbers on
          the same street for {aro.meta.matchMethods.address} more, and by hand for{" "}
          {aro.meta.matchMethods.manual}. Conversions count as well as new buildings, so shares are
          measured against new construction plus the net units created by conversions. Covers rental
          ARO units only, not for-sale units, and not units developers chose not to build by paying
          the ordinance&rsquo;s in-lieu fee instead.
        </p>
        <p>
          Wards are on current (2023) boundaries, as on the Permitted Units page. Terms such as AMI,
          LIHTC and new versus preserved are explained in the <Link to="/glossary">glossary</Link>.
        </p>
      </div>
    </div>
  );
}
