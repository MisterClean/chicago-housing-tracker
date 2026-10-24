import type { WardsData } from "../types";
import wardsDataRaw from "../data/wards_data.json";

/**
 * The neighborhoods each ward covers, for the ward cards on every tab. Keyed by
 * current (2023) ward number, so don't show it against a ward number from an
 * older permit.
 */
const NAMES = new Map(
  (wardsDataRaw as unknown as WardsData).wards.map((w) => [w.ward, w.neighborhoodNames])
);

export const wardNeighborhoods = (ward: number): string => NAMES.get(ward) ?? "";
