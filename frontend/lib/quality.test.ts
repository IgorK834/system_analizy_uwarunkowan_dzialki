import { describe, expect, it } from "vitest";

import {
  FRESHNESS_LABELS,
  QUALITY_SECTION_LABELS,
  QUALITY_STATUS_LABELS,
  QUALITY_STATUS_MARKS,
  QUALITY_STATUS_TONES,
  ageWarnings,
  formatAge,
  formatFetchedAt,
  freshnessDetail,
  freshnessLabel,
  matrixOrNull,
  reasonLabel,
  sectionLabel,
  statusLabel,
} from "@/lib/quality";
import { buildQualityMatrix } from "@/test/fixtures";
import type { SectionQualityMatrix } from "@/lib/types";

describe("prezentacja macierzy jakości", () => {
  it("ma etykietę, ton i znak dla każdego statusu (kolor nie jest jedynym nośnikiem)", () => {
    const statuses = Object.keys(QUALITY_STATUS_LABELS);
    expect(statuses).toHaveLength(8);
    for (const status of statuses) {
      expect(QUALITY_STATUS_TONES).toHaveProperty(status);
      expect(QUALITY_STATUS_MARKS).toHaveProperty(status);
    }
    // Brak pokrycia i błąd źródła to osobne etykiety.
    expect(QUALITY_STATUS_LABELS.no_coverage).not.toBe(QUALITY_STATUS_LABELS.unavailable);
    expect(QUALITY_STATUS_LABELS.error).not.toBe(QUALITY_STATUS_LABELS.unavailable);
    expect(new Set(Object.values(QUALITY_STATUS_MARKS)).size).toBe(8);
    expect(Object.keys(QUALITY_SECTION_LABELS)).toHaveLength(10);
    expect(Object.keys(FRESHNESS_LABELS)).toEqual(["fresh", "stale", "unknown"]);
  });

  it("bierze etykiety z legendy API, a bez legendy — ze stałych zapasowych", () => {
    const matrix = buildQualityMatrix();
    expect(statusLabel(matrix, "no_coverage")).toBe("brak pokrycia źródła");
    expect(freshnessLabel(matrix, "stale")).toBe("starsze niż reguła");
    const custom: SectionQualityMatrix = {
      ...matrix,
      legend: {
        ...matrix.legend,
        statuses: [{ id: "available", label: "OK z API", description: "d" }],
        freshness: [{ id: "fresh", label: "świeże z API", description: "d" }],
      },
    };
    expect(statusLabel(custom, "available")).toBe("OK z API");
    expect(freshnessLabel(custom, "fresh")).toBe("świeże z API");
    expect(statusLabel(custom, "partial")).toBe(QUALITY_STATUS_LABELS.partial);
    const noLegend = { ...matrix, legend: undefined } as unknown as SectionQualityMatrix;
    expect(statusLabel(noLegend, "error")).toBe(QUALITY_STATUS_LABELS.error);
    expect(freshnessLabel(noLegend, "unknown")).toBe(FRESHNESS_LABELS.unknown);
    expect(reasonLabel(noLegend, "SERVICE_TIMEOUT")).toBe("kod: SERVICE_TIMEOUT");
  });

  it("tłumaczy kody powodów z legendy i pokazuje nieznany kod dosłownie", () => {
    const matrix = buildQualityMatrix();
    expect(reasonLabel(matrix, "SERVICE_TIMEOUT")).toBe("przekroczono limit czasu usługi");
    expect(reasonLabel(matrix, "NIEZNANY_KOD")).toBe("kod: NIEZNANY_KOD");
    expect(sectionLabel("flood")).toBe("Zagrożenie powodziowe (ISOK)");
    expect(sectionLabel("nieznana" as never)).toBe("nieznana");
  });

  it("formatuje wiek: brak danych, poniżej doby, 1 dzień i N dni", () => {
    expect(formatAge(null)).toBe("brak danych");
    expect(formatAge(0)).toBe("mniej niż 1 dzień");
    expect(formatAge(86_399)).toBe("mniej niż 1 dzień");
    expect(formatAge(86_400)).toBe("1 dzień");
    expect(formatAge(5 * 86_400 + 10)).toBe("5 dni");
  });

  it("formatuje czas pobrania w UTC i odróżnia brak od nieprawidłowego czasu", () => {
    expect(formatFetchedAt(null)).toBe("brak danych");
    expect(formatFetchedAt("to nie data")).toBe("czas nieprawidłowy");
    const formatted = formatFetchedAt("2026-09-20T09:29:00Z");
    expect(formatted).toMatch(/2026/);
    expect(formatted).toMatch(/09:29/);
    expect(formatted.endsWith("UTC")).toBe(true);
  });

  it("opisuje świeżość wiekiem i regułą źródła — albo jej brakiem", () => {
    const matrix = buildQualityMatrix();
    const flood = matrix.sections.find((item) => item.section === "flood")!;
    const parcel = matrix.sections.find((item) => item.section === "parcel")!;
    const transport = matrix.sections.find((item) => item.section === "transport")!;
    expect(freshnessDetail(flood)).toBe("wiek: mniej niż 1 dzień; reguła źródła: 7 dni");
    expect(freshnessDetail(parcel)).toBe("wiek: mniej niż 1 dzień; brak reguły wieku");
    expect(freshnessDetail(transport)).toBe("wiek: brak danych; brak reguły wieku");
  });

  it("ostrzega o wieku na dziś tylko wg reguły własnego źródła i nie zmienia macierzy", () => {
    const matrix = buildQualityMatrix();
    const before = JSON.stringify(matrix);
    const soon = ageWarnings(matrix, new Date("2026-09-21T10:00:00Z"));
    // Teren miał już w chwili analizy stary pomiar (2026-08-01); pozostałe źródła są świeże.
    expect(soon.map((item) => item.section)).toEqual(["terrain"]);
    const later = ageWarnings(matrix, new Date("2027-01-01T00:00:00Z"));
    // flood, nature i terrain mają regułę 7 dni; źródła bez reguły nie są „stare”.
    expect(later.map((item) => item.section)).toEqual(["flood", "nature", "terrain"]);
    expect(later[0]).toMatchObject({ maxAgeDays: 7 });
    expect(later[0].ageSeconds).toBeGreaterThan(7 * 86_400);
    expect(JSON.stringify(matrix)).toBe(before);
  });

  it("pomija sekcje bez czasu pobrania albo z nieprawidłowym czasem", () => {
    const matrix = buildQualityMatrix();
    const broken: SectionQualityMatrix = {
      ...matrix,
      sections: matrix.sections.map((item) =>
        item.section === "flood"
          ? { ...item, fetched_at: "zepsute" }
          : item.section === "nature"
            ? { ...item, fetched_at: null }
            : item,
      ),
    };
    expect(ageWarnings(broken, new Date("2027-01-01T00:00:00Z")).map((item) => item.section)).toEqual([
      "terrain",
    ]);
  });

  it("traktuje brak albo pustą macierz jak jej brak", () => {
    expect(matrixOrNull(null)).toBeNull();
    expect(matrixOrNull(undefined)).toBeNull();
    expect(matrixOrNull({ ...buildQualityMatrix(), sections: [] })).toBeNull();
    const matrix = buildQualityMatrix();
    expect(matrixOrNull(matrix)).toBe(matrix);
  });
});
