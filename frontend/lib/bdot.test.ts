import { describe, expect, it } from "vitest";

import {
  BDOT_STATUS_LABELS,
  ROAD_ACCESS_DISCLAIMER,
  bdotReasonLabel,
  bdotStatusLabel,
  buildingMapFeatures,
  buildingsOrUnknown,
  buildingsStatusNote,
  formatMeters,
  formatPct,
  formatSqm,
  roadContextOrUnknown,
  roadMapLayers,
  roadStatusNote,
} from "@/lib/bdot";
import { BUILDINGS_CASES, ROAD_CASES } from "@/test/bdotFixtures";

describe("lib/bdot", () => {
  it("statusy bez wyniku nigdy nie są opisane jako brak drogi ani brak budynków", () => {
    for (const status of ["no_match", "no_coverage", "unavailable", "unknown"] as const) {
      expect(roadStatusNote(status)).toMatch(/nie (dowodzi|oznacza)|nie sprawdzono|nieznana/i);
      expect(buildingsStatusNote(status)).toMatch(/nie (wiadomo|oznacza)|nie sprawdzono|nieznane/i);
    }
    expect(roadStatusNote("available")).toBeNull();
    expect(buildingsStatusNote("available")).toBeNull();
    expect(BDOT_STATUS_LABELS.no_coverage).toContain("brak pokrycia");
  });

  it("etykiety kodów są polskie, a nieznany kod jest pokazany dosłownie", () => {
    expect(bdotReasonLabel("QUERY_TIMEOUT")).toBe("zapytanie przekroczyło limit czasu");
    expect(bdotReasonLabel("COS_NOWEGO")).toBe("COS_NOWEGO");
    expect(bdotReasonLabel(null)).toBeNull();
    expect(bdotStatusLabel("available")).toBe(BDOT_STATUS_LABELS.available);
  });

  it("zapis bez sekcji jest nieznany i niesie zdanie o braku potwierdzenia prawnego dostępu", () => {
    const road = roadContextOrUnknown(undefined);
    expect(road.status).toBe("unknown");
    expect(road.nearest).toBeNull();
    expect(road.interpretation_note).toBe(ROAD_ACCESS_DISCLAIMER);
    expect(ROAD_ACCESS_DISCLAIMER).toContain("nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej");
    const buildings = buildingsOrUnknown(null);
    expect(buildings.status).toBe("unknown");
    expect(buildings.on_parcel).toBeNull();
    expect(buildings.buffers).toEqual([]);
  });

  it("formatowanie rozróżnia brak wartości od zera", () => {
    expect(formatMeters(null)).toBe("—");
    expect(formatMeters(0)).toBe("0 m");
    expect(formatMeters(12.346)).toBe("12,35 m");
    expect(formatSqm(0)).toBe("0 m²");
    expect(formatSqm(undefined)).toBe("—");
    expect(formatPct(0)).toBe("0%");
    expect(formatPct(null)).toBe("—");
  });

  it("warstwy mapy pochodzą z geometrii w odpowiedzi, a bez drogi są puste", () => {
    const layers = roadMapLayers(ROAD_CASES.available);
    expect(layers.axes).toHaveLength(1);
    expect(layers.links).toHaveLength(1);
    expect(roadMapLayers(ROAD_CASES.intersects).links).toHaveLength(0); // odległość 0 — brak odcinka
    expect(roadMapLayers(ROAD_CASES.no_coverage)).toEqual({ axes: [], links: [] });
    expect(roadMapLayers(undefined)).toEqual({ axes: [], links: [] });
    expect(buildingMapFeatures(BUILDINGS_CASES.available)).toHaveLength(BUILDINGS_CASES.available.buildings.length);
    expect(buildingMapFeatures(BUILDINGS_CASES.no_coverage)).toEqual([]);
  });
});
