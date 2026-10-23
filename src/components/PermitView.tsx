import { useMemo, useState } from "react";
import { PermitMap, type PermitViewport } from "./PermitMap";
import {
  PERMIT_BREAK_LABELS,
  PERMIT_RAMP,
  type Project,
  type WardPermits,
} from "../lib/permits";

type SortKey =
  "rank" | "ward" | "units" | "sfh" | "mfh" | "mfhShare" | "perYear";

interface Props {
  rows: WardPermits[];
  geometry?: string;
  label: string;
  compact?: boolean;
  projectMinUnits: number;
  projectsForWard: (ward: number) => Project[];
  viewport: PermitViewport | null;
  onViewportChange: (view: PermitViewport) => void;
}

export function PermitView({
  rows,
  geometry,
  label,
  compact = false,
  projectMinUnits,
  projectsForWard,
  viewport,
  onViewportChange,
}: Props) {
  const [selectedWard, setSelectedWard] = useState<number | null>(null);
  const [hoveredWard, setHoveredWard] = useState<number | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: "units",
    desc: true,
  });
  const sortedRows = useMemo(() => {
    const dir = sort.desc ? -1 : 1;
    return [...rows].sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      // wards that built nothing have no MFH share; keep them last either way
      if (av == null && bv == null) return a.ward - b.ward;
      if (av == null) return 1;
      if (bv == null) return -1;
      return av === bv ? a.ward - b.ward : (av < bv ? -1 : 1) * dir;
    });
  }, [rows, sort]);

  const toggleSort = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, desc: !s.desc }
        : // numbers read high-to-low first; ward number reads 1-50 first
          { key, desc: key !== "ward" },
    );

  const sortDirection = (key: SortKey) =>
    sort.key === key ? (sort.desc ? "descending" : "ascending") : "none";

  const sortHead = (key: SortKey, label: string) => {
    const on = sort.key === key;
    return (
      <button
        type="button"
        className={on ? "pm-sort on" : "pm-sort"}
        onClick={() => toggleSort(key)}
      >
        {label}
        <span className="pm-sort-arrow">
          {on ? (sort.desc ? "\u2193" : "\u2191") : ""}
        </span>
      </button>
    );
  };

  const active = selectedWard ?? hoveredWard;
  const activeRow = active != null ? rows.find((r) => r.ward === active) : null;

  const wardCard = activeRow ? (
    <div className="pm-card">
      <div className="pm-card-head">
        <strong>Ward {activeRow.ward}</strong>
        <span className="pm-card-rank">#{activeRow.rank} of 50</span>
        <button
          className="pm-link pm-dismiss"
          onClick={() => {
            setSelectedWard(null);
            setHoveredWard(null);
          }}
        >
          Clear
        </button>
      </div>
      <div className="pm-card-grid">
        <div>
          <span className="pm-card-label">Units</span>
          <span className="pm-card-value">
            {activeRow.units.toLocaleString()}
          </span>
        </div>
        <div>
          <span className="pm-card-label">Per year</span>
          <span className="pm-card-value">{activeRow.perYear.toFixed(0)}</span>
        </div>
        <div>
          <span className="pm-card-label">SFH / MFH</span>
          <span className="pm-card-value">
            {activeRow.sfh.toLocaleString()} / {activeRow.mfh.toLocaleString()}
          </span>
        </div>
      </div>
      {(() => {
        const projects = projectsForWard(activeRow.ward);
        return (
          <div className="pm-projects">
            <span className="pm-card-label">
              Largest projects ({projectMinUnits}+ units)
            </span>
            {projects.length === 0 ? (
              <p className="pm-projects-none">
                No projects of {projectMinUnits} or more units in this period.
              </p>
            ) : (
              <ul>
                {projects.map((pr) => (
                  <li key={pr.p + pr.d}>
                    <span className="pm-proj-units">{pr.u}</span>
                    <span className="pm-proj-addr">{pr.a}</span>
                    <span className="pm-proj-date">{pr.d.slice(0, 7)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })()}
    </div>
  ) : null;

  return (
    <section className="pm-body">
      <div className="pm-map">
        <PermitMap
          rows={rows}
          geometry={geometry}
          viewport={viewport}
          onViewportChange={onViewportChange}
          selectedWard={selectedWard}
          hoveredWard={hoveredWard}
          onSelectWard={setSelectedWard}
          onHoverWard={setHoveredWard}
        />
        <div className="pm-overlay">
          <div className="pm-legend">
            <span className="pm-legend-title">Units permitted per year</span>
            <div className="pm-legend-steps">
              {PERMIT_RAMP.map((c, i) => (
                <div key={c} className="pm-legend-step">
                  <span className="pm-chip" style={{ background: c }} />
                  <span className="pm-chip-label">
                    {PERMIT_BREAK_LABELS[i]}
                  </span>
                </div>
              ))}
            </div>
          </div>
          {!compact && wardCard}
        </div>
      </div>

      {compact && (
        <div className="pm-period-detail" aria-live="polite">
          {wardCard ?? (
            <p className="pm-basis-hint">
              Select a ward on the map or in the table to see its projects.
            </p>
          )}
        </div>
      )}
      <div
        className="pm-table-wrap"
        tabIndex={0}
        role="region"
        aria-label={`${label} ward table`}
      >
        <table className="pm-table">
          <caption className="pm-table-caption">
            {label} · ward totals for this period
          </caption>
          <thead>
            <tr>
              <th className="num" aria-sort={sortDirection("rank")}>
                {sortHead("rank", "#")}
              </th>
              <th className="num" aria-sort={sortDirection("ward")}>
                {sortHead("ward", "Ward")}
              </th>
              <th className="num" aria-sort={sortDirection("units")}>
                {sortHead("units", "Units")}
              </th>
              <th
                className="num"
                title="Units in single-family (1-unit) buildings"
                aria-sort={sortDirection("sfh")}
              >
                {sortHead("sfh", "SFH")}
              </th>
              <th
                className="num"
                title="Units in multi-family (2+ unit) buildings"
                aria-sort={sortDirection("mfh")}
              >
                {sortHead("mfh", "MFH")}
              </th>
              <th
                className="num"
                title="Share of units in multi-family buildings"
                aria-sort={sortDirection("mfhShare")}
              >
                {sortHead("mfhShare", "MFH %")}
              </th>

              <th className="num" aria-sort={sortDirection("perYear")}>
                {sortHead("perYear", "Per year")}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((r) => (
              <tr
                key={r.ward}
                className={
                  r.ward === selectedWard
                    ? "pm-row sel"
                    : r.ward === hoveredWard
                      ? "pm-row hov"
                      : "pm-row"
                }
                onMouseEnter={() => setHoveredWard(r.ward)}
                onMouseLeave={() => setHoveredWard(null)}
                onClick={() =>
                  setSelectedWard(selectedWard === r.ward ? null : r.ward)
                }
              >
                <td className="num muted">{r.rank}</td>
                <td className="num">
                  <button
                    type="button"
                    className="pm-ward-button"
                    aria-label={`Ward ${r.ward}`}
                    aria-pressed={r.ward === selectedWard}
                  >
                    {r.ward}
                  </button>
                </td>
                <td className="num strong">{r.units.toLocaleString()}</td>
                <td className="num muted">{r.sfh.toLocaleString()}</td>
                <td className="num">{r.mfh.toLocaleString()}</td>
                <td className="num muted">
                  {r.mfhShare == null
                    ? "—"
                    : `${(r.mfhShare * 100).toFixed(0)}%`}
                </td>

                <td className="num">{r.perYear.toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
