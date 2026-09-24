import { describe, expect, it } from "vitest";

import {
  NO_GEOMETRY_IS_NOT_NO_PLAN,
  POG_COVERAGE_STATUS_LABELS,
  POG_DATA_AVAILABILITY_LABELS,
  POG_LEGAL_STATUS_LABELS,
  POG_LEGAL_STATUS_SHORT,
  coverageStatusLabel,
  dataAvailabilityLabel,
  legalStatusLabel,
  legalStatusShort,
  pogStatusNotes,
} from "@/lib/pogStatus";
import type {
  PogCoverageStatus,
  PogDataAvailability,
  PogLegalStatus,
} from "@/lib/types";

const LEGAL: PogLegalStatus[] = ["binding", "project", "in_progress", "superseded", "unknown"];
const COVERAGE: PogCoverageStatus[] = [
  "available",
  "partial",
  "act_without_spatial_data",
  "no_act_confirmed",
  "unknown",
];
const AVAILABILITY: PogDataAvailability[] = ["current", "stale", "unavailable"];

describe("pogStatus", () => {
  it("ma etykietę dla każdej kanonicznej wartości (ten sam enum co backend)", () => {
    expect(Object.keys(POG_LEGAL_STATUS_LABELS).sort()).toEqual([...LEGAL].sort());
    expect(Object.keys(POG_LEGAL_STATUS_SHORT).sort()).toEqual([...LEGAL].sort());
    expect(Object.keys(POG_COVERAGE_STATUS_LABELS).sort()).toEqual([...COVERAGE].sort());
    expect(Object.keys(POG_DATA_AVAILABILITY_LABELS).sort()).toEqual([...AVAILABILITY].sort());
  });

  it("dla wartości legacy i nieznanych zwraca etykiety bezpieczne (nieustalone)", () => {
    expect(legalStatusLabel("adopted")).toBe(POG_LEGAL_STATUS_LABELS.unknown);
    expect(legalStatusLabel(null)).toBe(POG_LEGAL_STATUS_LABELS.unknown);
    expect(legalStatusShort("not_available")).toBe("nieustalony");
    expect(legalStatusShort(undefined)).toBe("nieustalony");
    expect(coverageStatusLabel("complete")).toBe(POG_COVERAGE_STATUS_LABELS.unknown);
    expect(dataAvailabilityLabel("fresh")).toBe(POG_DATA_AVAILABILITY_LABELS.unavailable);
    expect(dataAvailabilityLabel(null)).toBe(POG_DATA_AVAILABILITY_LABELS.unavailable);
    expect(legalStatusLabel("project")).toBe("projekt aktu — niewiążący");
    expect(coverageStatusLabel("partial")).toBe("dane przestrzenne niepełne");
    expect(dataAvailabilityLabel("stale")).toMatch(/ostatnia potwierdzona/);
  });

  it.each(
    (["project", "in_progress", "unknown"] as PogLegalStatus[]).flatMap((legal) =>
      COVERAGE.flatMap((coverage) =>
        AVAILABILITY.map((availability) => [legal, coverage, availability] as const),
      ),
    ),
  )("%s + %s + %s nigdy nie używa języka obowiązywania", (legal, coverage, availability) => {
    const texts = [
      legalStatusLabel(legal),
      legalStatusShort(legal),
      ...pogStatusNotes({
        legal_status: legal,
        coverage_status: coverage,
        data_availability: availability,
        status_confirmed_at: "2026-08-19T01:00:00Z",
      }),
    ]
      .join(" ")
      .toLowerCase();
    expect(texts).not.toMatch(/obowiązuj/);
    expect(texts.replaceAll("braku planu", "")).not.toMatch(/brak planu/);
  });

  it.each(["act_without_spatial_data", "unknown", "partial"] as PogCoverageStatus[])(
    "brak geometrii (%s) jawnie nie oznacza braku planu",
    (coverage) => {
      const notes = pogStatusNotes({
        legal_status: "binding",
        coverage_status: coverage,
        data_availability: "current",
        status_confirmed_at: null,
      });
      expect(notes).toContain(NO_GEOMETRY_IS_NOT_NO_PLAN);
    },
  );

  it("wartość stale pokazuje datę ostatniego potwierdzenia", () => {
    const notes = pogStatusNotes({
      legal_status: "binding",
      coverage_status: "available",
      data_availability: "stale",
      status_confirmed_at: "2026-08-19T01:00:00Z",
    });
    expect(notes.join(" ")).toMatch(/19\.08\.2026/);
    expect(
      pogStatusNotes({
        legal_status: "binding",
        coverage_status: "available",
        data_availability: "stale",
        status_confirmed_at: null,
      }).join(" "),
    ).toMatch(/nieznana data/);
    expect(
      pogStatusNotes({
        legal_status: "binding",
        coverage_status: "available",
        data_availability: "stale",
        status_confirmed_at: "not-a-date",
      }).join(" "),
    ).toMatch(/not-a-date/);
  });

  it("opisuje superseded, no_act_confirmed i niedostępność źródła", () => {
    expect(
      pogStatusNotes({
        legal_status: "superseded",
        coverage_status: "no_act_confirmed",
        data_availability: "unavailable",
        status_confirmed_at: null,
      }),
    ).toEqual([
      "Akt jest nieaktualny — sprawdź akt, który go zastąpił.",
      "Brak aktu potwierdzono urzędowo — zobacz wskazane potwierdzenie.",
      "Źródło było niedostępne; brak wyniku nie oznacza braku ograniczeń.",
    ]);
    expect(
      pogStatusNotes({
        legal_status: "binding",
        coverage_status: "available",
        data_availability: "current",
        status_confirmed_at: null,
      }),
    ).toEqual([]);
  });
});
