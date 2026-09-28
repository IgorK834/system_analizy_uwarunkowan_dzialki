import { describe, expect, it } from "vitest";

import {
  aspectDescription,
  formatDegrees,
  formatMeters,
  formatNumber,
  formatPercent,
  profileChart,
  reasonLabel,
  terrainOrUnknown,
  terrainStatusLabel,
  terrainStatusNote,
} from "@/lib/terrain";
import {
  buildRelief,
  buildTerrain,
  DISPERSED_ASPECT,
  FLAT_TERRAIN,
  NO_COVERAGE_TERRAIN,
  TIMEOUT_TERRAIN,
} from "@/test/terrainFixtures";

describe("terrain presentation", () => {
  it("formatuje jednostki po polsku bez sztucznych zer", () => {
    expect(formatMeters(112.3)).toBe("112,3 m");
    expect(formatMeters(3.4)).toBe("3,4 m");
    expect(formatMeters(0)).toBe("0 m");
    expect(formatMeters(-0)).toBe("0 m");
    expect(formatMeters(-1.8)).toBe("-1,8 m");
    expect(formatMeters(null)).toBe("—");
    expect(formatDegrees(4.2218)).toBe("4,22°");
    expect(formatPercent(16.0119)).toBe("16,01%");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatDegrees(null)).toBe("—");
    expect(formatNumber(3648, 0)).toBe("3648");
  });

  it("rozróżnia cztery statusy i tylko pomiar 0 m nazywa płaskim terenem", () => {
    expect(terrainStatusLabel("available")).toBe("zmierzono");
    expect(terrainStatusLabel("no_coverage")).toBe("brak pokrycia danymi NMT");
    expect(terrainStatusLabel("unavailable")).toBe("pomiar niedostępny");
    expect(terrainStatusLabel("unknown")).toBe("brak informacji w zapisanym wyniku");

    expect(terrainStatusNote(buildTerrain())).toBeNull();
    expect(terrainStatusNote(FLAT_TERRAIN)).toMatch(/wynosi 0 m .* płaski/);
    for (const terrain of [NO_COVERAGE_TERRAIN, TIMEOUT_TERRAIN, terrainOrUnknown(null)]) {
      expect(terrainStatusNote(terrain)).toMatch(/nie oznacza płaskiego terenu/);
    }
  });

  it("zastępuje brak sekcji statusem unknown bez wysokości", () => {
    const legacy = terrainOrUnknown(undefined);
    expect(legacy.status).toBe("unknown");
    expect(legacy.height_difference_m).toBeNull();
    expect(legacy.reason_code).toBe("LEGACY_SNAPSHOT");
    const measured = buildTerrain();
    expect(terrainOrUnknown(measured)).toBe(measured);
  });

  it("tłumaczy kody przyczyn i zachowuje nieznane", () => {
    expect(reasonLabel("SERVICE_TIMEOUT")).toMatch(/nie odpowiedziała/);
    expect(reasonLabel("NEW_CODE")).toBe("NEW_CODE");
    expect(reasonLabel(null)).toBeNull();
  });

  it("opisuje ekspozycję dominującą, rozproszoną i płaską", () => {
    expect(aspectDescription(DISPERSED_ASPECT)).toMatch(/^rozproszona .*90,96°/);
    expect(
      aspectDescription({ ...DISPERSED_ASPECT, status: "defined", dominant_direction: "S", mean_azimuth_deg: 180 }),
    ).toBe("południowa (średni azymut spadku 180°)");
    expect(
      aspectDescription({
        ...DISPERSED_ASPECT,
        status: "flat",
        mean_azimuth_deg: null,
        resultant_length: null,
        non_flat_share_pct: 12.5,
      }),
    ).toMatch(/^nie wyznaczono — teren płaski .*12,5%/);
  });

  it("dzieli profil na odcinki na lukach NoData i skaluje wysokości", () => {
    const profile = buildRelief().profile!;
    const chart = profileChart(profile, 106, 56, 3);

    // 115,069 m → y = 3 + 50·(1 − (115,069 − 114,222) / 1,939) = 31,2; luka przerywa linię.
    expect(chart.segments).toEqual(["3.0,31.2 28.0,3.0", "78.0,43.3 103.0,53.0"]);
    expect(chart.minHeight).toBe(114.222);
    expect(chart.maxHeight).toBe(116.161);
    expect(chart.missingCount).toBe(1);
  });

  it("nie rysuje profilu bez wysokości ani dla linii zerowej długości", () => {
    const profile = buildRelief().profile!;
    const empty = profileChart(
      { ...profile, samples: profile.samples.map((sample) => ({ ...sample, height_m: null })) },
      100,
      50,
    );
    expect(empty).toEqual({ segments: [], minHeight: null, maxHeight: null, missingCount: 5 });
    expect(profileChart({ ...profile, length_m: 0 }, 100, 50).segments).toEqual([]);
    const level = profileChart(
      {
        ...profile,
        samples: profile.samples.map((sample) => ({ ...sample, height_m: 100 })),
      },
      100,
      50,
    );
    expect(level.segments).toHaveLength(1);
  });
});
