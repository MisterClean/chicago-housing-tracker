import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
// the dual-handle range slider is defined with the permitting page
import "../styles/permitting.css";
import "../styles/income.css";
import "../styles/mix.css";
import { Scatter, type ScatterPoint } from "../components/Scatter";
import { concentration, mixToCsv, wardMix } from "../lib/affordableVsMarket";
import { wardNeighborhoods } from "../lib/wards";
import type { AroData } from "../lib/aro";
import type { LihtcData } from "../lib/lihtc";
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
/** How many of the biggest market-rate wards the summary groups together. */
const TOP_N = 10;

/** Matches the series colours on the Affordable Units page. */
const ARO_COLOR = "#0d96c9";
const SUB_COLOR = "#00596f";

const count = (v: number) => (v >= 1000 ? `${v / 1000}k` : String(Math.round(v)));
const pct = (v: number) => `${Math.round(v * 100)}%`;

/** The wards drawn as small marks on the zero line, named in words. */
function NoneLine({ wards, what }: { wards: number[]; what: string }) {
  if (wards.length === 0) return null;
  return (
    <p className="mix-none">
      {wards.length === 1 ? "One ward" : `${wards.length} wards`} had no {what} (small marks on the
      zero line): {wards.join(", ")}.
    </p>
  );
}

export function AffordableVsMarket() {
  const [selectedWard, setSelectedWard] = useState<number | null>(null);
  const [hoveredWard, setHoveredWard] = useState<number | null>(null);
  const [from, setFrom] = useState(FIRST);
  const [to, setTo] = useState(LAST);

  const rows = useMemo(() => wardMix(aro, lihtc, permits, from, to), [from, to]);
  const conc = useMemo(() => concentration(rows, TOP_N), [rows]);
  const totals = useMemo(
    () => rows.reduce((t, r) => ({ market: t.market + r.market, aro: t.aro + r.aro, subsidized: t.subsidized + r.subsidized }),
      { market: 0, aro: 0, subsidized: 0 }),
    [rows]
  );
  const xMax = Math.max(...rows.map((r) => r.market));
  const aroPoints: ScatterPoint[] = rows.map((r) => ({ ward: r.ward, x: r.market, y: r.aro }));
  const subPoints: ScatterPoint[] = rows.map((r) => ({ ward: r.ward, x: r.market, y: r.subsidized }));
  const noAro = rows.filter((r) => r.aro === 0).map((r) => r.ward);
  const noSub = rows.filter((r) => r.subsidized === 0).map((r) => r.ward);

  const setFromSafe = (v: number) => setFrom(Math.min(v, to));
  const setToSafe = (v: number) => setTo(Math.max(v, from));
  const span = LAST - FIRST;
  const lo = ((from - FIRST) / span) * 100;
  const hi = ((to - FIRST) / span) * 100;

  const active = selectedWard ?? hoveredWard;
  const activeRow = active != null ? rows.find((r) => r.ward === active) : null;

  const downloadCsv = () => {
    const blob = new Blob([mixToCsv(rows, from, to)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chicago-affordable-vs-market-rate-by-ward-${from}-${to}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const scatterProps = {
    xLabel: "Market-rate homes permitted",
    fmtX: count,
    fmtY: count,
    log: true,
    xMax,
    numbersInDots: true,
    zeroMarks: true,
    selectedWard,
    hoveredWard,
    onSelectWard: setSelectedWard,
    onHoverWard: setHoveredWard,
  };

  return (
    <div className="income mix">
      <header className="in-header">
        <p className="eyebrow">Where affordable goes</p>
        <h1 className="page-title">Affordable and market-rate building</h1>
        <p className="page-lede">
          Do affordable homes get built where the market builds? Two kinds go different ways: ARO
          units are set aside inside market-rate buildings, while subsidized units go where public
          money sends them.
        </p>
      </header>

      <section className="in-controls">
        <div className="in-range-head">
          <span className="eyebrow">Permit years</span>
          <strong className="in-range-val">
            {from}&ndash;{to}
            {to === LAST && <span className="in-partial"> (through {permits.meta.lastDate})</span>}
          </strong>
          {(from !== FIRST || to !== LAST) && (
            <button className="in-link" onClick={() => { setFrom(FIRST); setTo(LAST); }}>
              All years
            </button>
          )}
          {(from !== CYCLE_START || to !== LAST) && (
            <button className="in-link" onClick={() => { setFrom(CYCLE_START); setTo(LAST); }}>
              Current Council only
            </button>
          )}
        </div>
        <div className="pm-slider" style={{ ["--lo" as string]: `${lo}%`, ["--hi" as string]: `${hi}%` }}>
          <div className="pm-slider-track" />
          <div className="pm-slider-fill" />
          <input type="range" min={FIRST} max={LAST} value={from} aria-label="First permit year"
            onChange={(e) => setFromSafe(Number(e.target.value))} />
          <input type="range" min={FIRST} max={LAST} value={to} aria-label="Last permit year"
            onChange={(e) => setToSafe(Number(e.target.value))} />
        </div>
      </section>

      <section className="mix-summary" aria-label="Where each kind of building went">
        <table className="mix-table">
          <thead>
            <tr>
              <th />
              <th className="num">Market-rate homes</th>
              <th className="num">ARO units</th>
              <th className="num">Subsidized units</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">
                The {TOP_N} wards that permitted the most market-rate homes
                <span className="mix-wards">Wards {conc.top.join(", ")}</span>
              </th>
              <td className="num strong" data-label="Market-rate">{pct(conc.market)}</td>
              <td className="num strong" data-label="ARO">{pct(conc.aro)}</td>
              <td className="num strong" data-label="Subsidized">{pct(conc.subsidized)}</td>
            </tr>
            <tr>
              <th scope="row">The other {50 - TOP_N} wards</th>
              <td className="num" data-label="Market-rate">{pct(1 - conc.market)}</td>
              <td className="num" data-label="ARO">{pct(1 - conc.aro)}</td>
              <td className="num" data-label="Subsidized">{pct(1 - conc.subsidized)}</td>
            </tr>
            <tr className="mix-total">
              <th scope="row">Citywide, {from === to ? from : `${from}–${to}`}</th>
              <td className="num" data-label="Market-rate">{totals.market.toLocaleString()}</td>
              <td className="num" data-label="ARO">{totals.aro.toLocaleString()}</td>
              <td className="num" data-label="Subsidized">{totals.subsidized.toLocaleString()}</td>
            </tr>
          </tbody>
        </table>
        <button className="pm-export" onClick={downloadCsv}>Download CSV</button>
      </section>

      <section className="mix-charts">
        <figure className="in-chart">
          <figcaption>
            <h2>ARO units against market-rate homes</h2>
            <p>
              Units the Affordable Requirements Ordinance sets aside inside market-rate buildings,
              by ward, {from}&ndash;{to}. If they follow the market, the dots rise from left to right.
            </p>
          </figcaption>
          <Scatter {...scatterProps} points={aroPoints} yLabel="ARO units" color={ARO_COLOR} />
          <NoneLine wards={noAro} what="ARO units" />
        </figure>
        <figure className="in-chart">
          <figcaption>
            <h2>Subsidized units against market-rate homes</h2>
            <p>
              CHA-owned, tax-credit and Other City/State units, which public money places, by ward,{" "}
              {from}&ndash;{to}. Same horizontal scale as above, so each ward sits in the same
              left-to-right position in both charts.
            </p>
          </figcaption>
          <Scatter {...scatterProps} points={subPoints} yLabel="Subsidized units" color={SUB_COLOR} />
          <NoneLine wards={noSub} what="subsidized units" />
        </figure>
        {/* Inside the charts section and sticky, so it stays on screen whichever chart is hovered. */}
        {activeRow && (
          <div className="in-readout mix-readout" aria-live="polite">
            <span className="in-ward">
              <strong>Ward {activeRow.ward}</strong>{" "}
              <span className="in-hood">{wardNeighborhoods(activeRow.ward)}</span>
            </span>
            <span><strong>{activeRow.market.toLocaleString()}</strong> market-rate homes</span>
            <span><strong>{activeRow.aro.toLocaleString()}</strong> ARO units</span>
            <span><strong>{activeRow.subsidized.toLocaleString()}</strong> subsidized units</span>
            <span>{from}&ndash;{to}</span>
          </div>
        )}
        <p className="mix-scale-note">
          Both axes use a log scale: each gridline is ten times the one before, because a few
          downtown wards permit over a hundred times what most wards do. Hover or tap a dot for
          its numbers.
        </p>
      </section>


      <div className="in-method">
        <p>
          <strong>What is counted.</strong> New homes only, dated by building permit and placed on
          today&rsquo;s ward map. Market-rate homes are every new home permitted (new construction plus
          the net homes created by converting existing buildings, as on{" "}
          <Link to="/permitting">Permitted Units</Link>) less the income-restricted ones counted on{" "}
          <Link to="/affordable">Affordable Units</Link>; the market-rate homes in a mixed-income
          building count here as market-rate. A few wards&rsquo; restricted homes outnumber the
          permits whose unit counts could be read, because some affordable homes come from rehabs and
          conversions whose permits state none, so their market-rate figure is shown as zero.
          Subsidized units are those Affordable Units counts as CHA owned, tax credit or Other
          City/State; preserved units are left out, since the question is where new homes go.
          Wards are drawn to roughly equal populations, so totals compare fairly without dividing by
          households.
        </p>
      </div>
    </div>
  );
}
