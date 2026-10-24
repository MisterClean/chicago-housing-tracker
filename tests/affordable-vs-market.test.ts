import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { concentration, wardMix } from "../src/lib/affordableVsMarket.ts";
import { allDevelopments, summarise, type LihtcData, type Options } from "../src/lib/lihtc.ts";
import type { AroData } from "../src/lib/aro.ts";
import type { PermitsData } from "../src/lib/permits.ts";

const load = <T>(f: string) => JSON.parse(readFileSync(new URL(`../src/data/${f}`, import.meta.url), "utf8")) as T;
const aro = load<AroData>("aro.json");
const lihtc = load<LihtcData>("lihtc.json");
const permits = load<PermitsData>("permits.json");
const NEW_ALL: Options = { program: "all", kind: "new", basis: "permit", pop: "all" };
const { firstYear: FIRST, lastYear: LAST } = permits.meta;

test("every ward, every year range: the three series reconcile with Affordable Units", () => {
  for (let from = FIRST; from <= LAST; from++) {
    for (let to = from; to <= LAST; to++) {
      const rows = wardMix(aro, lihtc, permits, from, to);
      assert.equal(rows.length, 50);
      const page = summarise(allDevelopments(aro, lihtc, NEW_ALL), permits, from, to, NEW_ALL);
      const sum = (k: "aro" | "subsidized") => rows.reduce((s, r) => s + r[k], 0);
      assert.equal(sum("aro"), page.totals.aro, `${from}-${to} ARO`);
      assert.equal(sum("subsidized"), page.totals.cha + page.totals.lihtcNew + page.totals.city, `${from}-${to} subsidized`);
      for (const r of rows) {
        assert.ok(r.market >= 0, `ward ${r.ward} ${from}-${to}: negative market-rate`);
        // Market-rate is the remainder, except where it had to be floored at zero.
        assert.equal(r.affordable, r.aro + r.subsidized);
        if (r.market > 0) assert.equal(r.market + r.affordable, r.all, `ward ${r.ward} ${from}-${to}`);
      }
    }
  }
});

test("the top wards are the biggest market-rate wards, and every share stays within 0-1", () => {
  const rows = wardMix(aro, lihtc, permits, FIRST, LAST);
  const c = concentration(rows, 10);
  const byMarket = [...rows].sort((a, b) => b.market - a.market || a.ward - b.ward).slice(0, 10).map((r) => r.ward);
  assert.deepEqual(c.top, byMarket);
  for (const v of [c.market, c.affordable]) assert.ok(v >= 0 && v <= 1);
});
