import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  coveredYears,
  periodsToCsv,
  previousDay,
  summarise,
  summarisePeriods,
  type CountMode,
  type PermitsData,
} from "../src/lib/permits.ts";
import periods from "../src/data/wardBoundaryPeriods.json";

const data = JSON.parse(
  readFileSync(new URL("../src/data/permits.json", import.meta.url), "utf8"),
) as PermitsData;

test("single eras and both transition years choose exactly the matching maps", () => {
  for (const [from, to, ids] of [
    [2010, 2014, ["2003"]],
    [2016, 2022, ["2015"]],
    [2024, 2026, ["2023"]],
    [2015, 2015, ["2003", "2015"]],
    [2023, 2023, ["2015", "2023"]],
    [2010, 2026, ["2003", "2015", "2023"]],
  ] as const) {
    assert.deepEqual(
      summarisePeriods(data, periods, from, to).map((p) => p.period.id),
      ids,
    );
  }
  const transition = summarisePeriods(data, periods, 2015, 2015);
  assert.equal(transition[0].end, "2015-05-18");
  assert.equal(transition[1].start, "2015-05-18");
  const recent = summarisePeriods(data, periods, 2023, 2023);
  assert.equal(recent[0].end, "2023-05-15");
  assert.equal(recent[1].start, "2023-05-15");
  const partialSnapshot = {
    ...data,
    meta: { ...data.meta, lastYear: 2026, lastDate: "2026-10-08" },
  };
  assert.equal(
    summarisePeriods(partialSnapshot, periods, 2026, 2026)[0].end,
    "2026-10-09",
  );
});

test("every supported range reconciles to the existing historical totals in both count modes", () => {
  for (const mode of ["new", "withConversions"] satisfies CountMode[]) {
    for (let from = data.meta.firstYear; from <= data.meta.lastYear; from++) {
      for (let to = from; to <= data.meta.lastYear; to++) {
        const panels = summarisePeriods(data, periods, from, to, mode);
        const full = summarise(data, from, to, "atIssue", mode);
        assert.equal(
          panels.reduce((sum, p) => sum + p.total, 0),
          full.total,
          `${from}–${to} ${mode}`,
        );
        for (const row of full.rows) {
          for (const field of [
            "units",
            "newUnits",
            "sfh",
            "mfh",
            "convSfh",
            "convMfh",
            "permits",
          ] as const) {
            assert.equal(
              panels.reduce(
                (sum, p) =>
                  sum + p.rows.find((r) => r.ward === row.ward)![field],
                0,
              ),
              row[field],
            );
          }
        }
        for (const panel of panels) {
          assert.ok(panel.years > 0);
          for (const row of panel.rows)
            assert.equal(row.perYear, row.units / panel.years);
        }
      }
    }
  }
});

test("annualization uses exact coverage, leap years and partial transition years", () => {
  assert.equal(coveredYears("2024-01-01", "2025-01-01"), 1);
  assert.equal(coveredYears("2024-02-28", "2024-03-01"), 2 / 366);
  assert.equal(coveredYears("2026-01-01", "2026-10-09"), 281 / 365);
  const split = summarisePeriods(data, periods, 2015, 2015);
  assert.equal(split[0].years, 137 / 365);
  assert.equal(split[1].years, 228 / 365);
  assert.equal(
    split.reduce((sum, p) => sum + p.years, 0),
    1,
  );
});

test("CSV keeps ward numbers separate by period and includes exact dates and signed conversions", () => {
  const panels = summarisePeriods(data, periods, 2010, 2026, "withConversions");
  const [header, ...lines] = periodsToCsv(panels, "withConversions")
    .split("\n")
    .map((s) => s.split(","));
  assert.equal(lines.length, 150);
  assert.deepEqual(header.slice(0, 3), [
    "boundary_period",
    "period_start",
    "period_end",
  ]);
  const index = Object.fromEntries(header.map((key, i) => [key, i]));
  assert.equal(
    new Set(lines.map((line) => `${line[0]}:${line[index.ward]}`)).size,
    150,
  );
  assert.equal(
    lines.reduce((sum, line) => sum + Number(line[index.units]), 0),
    panels.reduce((sum, p) => sum + p.total, 0),
  );
  for (const p of panels) {
    const line = lines.find((r) => r[0] === p.period.id)!;
    assert.equal(line[1], p.start);
    assert.equal(line[2], previousDay(p.end));
  }
  assert.ok(lines.some((r) => Number(r[index.conversion_mfh_change]) < 0));
});

test("projects and all fifty map wards belong to the correct boundary period", () => {
  const panels = summarisePeriods(data, periods, data.meta.firstYear, data.meta.lastYear);
  const seen = new Set<string>();
  for (const panel of panels) {
    const geo = JSON.parse(
      readFileSync(
        new URL(`../public/${panel.period.geometry}`, import.meta.url),
        "utf8",
      ),
    );
    assert.deepEqual(
      geo.features
        .map((f: { properties: { ward: number } }) => f.properties.ward)
        .sort((a: number, b: number) => a - b),
      Array.from({ length: 50 }, (_, i) => i + 1),
    );
    for (const projects of Object.values(panel.tables.projects)) {
      for (const p of projects) {
        assert.ok(p.d >= panel.start && p.d < panel.end);
        const key = `${p.p}:${p.d}`;
        assert.ok(!seen.has(key), `project counted twice: ${key}`);
        seen.add(key);
      }
    }
  }
});
