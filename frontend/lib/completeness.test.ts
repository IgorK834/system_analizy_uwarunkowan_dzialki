import { describe, expect, it } from "vitest";

import {
  COMPLETENESS_STATE_LABELS,
  COMPLETENESS_STATE_MARKS,
  COMPLETENESS_STATE_TONES,
  basisLabel,
  completenessOrNull,
  progressText,
  sectionsByState,
  sectionsInScope,
} from "@/lib/completeness";
import { COMPLETENESS_CASES } from "@/test/completenessFixtures";

describe("lib/completeness (AU-104)", () => {
  it("każdy stan ma etykietę, znak i ton — znaczenie nie zależy od koloru", () => {
    for (const state of ["checked", "missing", "out_of_scope"] as const) {
      expect(COMPLETENESS_STATE_LABELS[state]).toBeTruthy();
      expect(COMPLETENESS_STATE_MARKS[state]).toBeTruthy();
      expect(COMPLETENESS_STATE_TONES[state]).toBeTruthy();
    }
    expect(new Set(Object.values(COMPLETENESS_STATE_MARKS)).size).toBe(3);
  });

  it("zakres analizy to sekcje bez `out_of_scope` i zgadza się z mianownikiem z backendu", () => {
    for (const value of Object.values(COMPLETENESS_CASES)) {
      expect(sectionsInScope(value)).toHaveLength(value.total);
      expect(sectionsByState(value, "checked")).toHaveLength(value.checked);
    }
  });

  it("uzbrojenie terenu i transport są wymienione jako poza zakresem, a nie pominięte", () => {
    const value = COMPLETENESS_CASES.complete_vector;
    expect(sectionsByState(value, "out_of_scope").map((item) => item.section).sort()).toEqual(["transport", "utilities"]);
  });

  it("etykieta podstawy MPZP odróżnia wektor od aktu z dokumentem i od `no_match`", () => {
    expect(basisLabel("vector_intersection")).toContain("lokalnym wektorem");
    expect(basisLabel("document_candidate")).toContain("bez lokalnego wektora");
    expect(basisLabel("no_match")).toContain("nie wskazały planu");
    expect(basisLabel("coś_nowego")).toBe("coś_nowego");
    expect(basisLabel(null)).toBeNull();
  });

  it("tekst dla czytnika ekranu zawiera liczby", () => {
    expect(progressText(COMPLETENESS_CASES.complete_vector)).toBe("Sprawdzono wszystkie 8 sekcji w zakresie analizy");
    const partial = COMPLETENESS_CASES.partial_pog;
    expect(progressText(partial)).toBe(`Sprawdzono ${partial.checked} z ${partial.total} sekcji w zakresie analizy`);
  });

  it("odpowiedź bez sekcji albo bez pola nie jest paskiem kompletności", () => {
    expect(completenessOrNull(undefined)).toBeNull();
    expect(completenessOrNull(null)).toBeNull();
    expect(completenessOrNull({ ...COMPLETENESS_CASES.complete_vector, sections: [] })).toBeNull();
    expect(completenessOrNull(COMPLETENESS_CASES.complete_vector)).not.toBeNull();
  });
});
