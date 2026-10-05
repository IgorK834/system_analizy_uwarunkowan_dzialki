import { describe, expect, it } from "vitest";

import {
  CONDITION_KIND_LABELS,
  VALUE_KIND_LABELS,
  conditionsOf,
  conditionsText,
  summarizeValueKinds,
  valueKindOf,
} from "@/lib/mpzpConditions";
import type { MpzpParameterEvidence } from "@/lib/types";

function parameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return {
    name: "max_building_height_m",
    normalized_value: 9,
    raw_value: "9 m",
    unit: "m",
    evidence_text: "wysokość 9 m",
    page_number: 1,
    segment_id: null,
    legal_unit_id: null,
    document_sha256: null,
    document_version_id: null,
    parser_version: null,
    extraction_method: null,
    confidence: 0.8,
    conflict_group_id: null,
    manual_review_required: false,
    ...overrides,
  };
}

const FLAT_ROOF = { kind: "roof_type", label: "dach płaski", quote: "dachem płaskim" } as const;

describe("valueKindOf", () => {
  it("używa rodzaju z odpowiedzi API, gdy jest", () => {
    expect(valueKindOf(parameter({ value_kind: "conditional", conditions: [FLAT_ROOF] }))).toBe("conditional");
    expect(valueKindOf(parameter({ value_kind: "conflict", conflict_group_id: "g" }))).toBe("conflict");
    expect(valueKindOf(parameter({ value_kind: "unconditional" }))).toBe("unconditional");
  });

  it("czyta odpowiedź sprzed PV3-08 jako wartość bez warunku, a grupę konfliktu jako sprzeczność", () => {
    expect(valueKindOf(parameter())).toBe("unconditional");
    expect(valueKindOf(parameter({ conflict_group_id: "230_UMW:max_building_height_m" }))).toBe("conflict");
  });

  it("wnioskuje warunkowość z samych warunków, gdy brak value_kind", () => {
    expect(valueKindOf(parameter({ conditions: [FLAT_ROOF] }))).toBe("conditional");
    expect(valueKindOf(parameter({ conditions: [] }))).toBe("unconditional");
  });
});

describe("warunki wartości", () => {
  it("conditionsOf zwraca pustą listę dla zapisu bez warunków", () => {
    expect(conditionsOf(parameter())).toEqual([]);
    expect(conditionsOf(parameter({ conditions: [FLAT_ROOF] }))).toEqual([FLAT_ROOF]);
  });

  it("conditionsText łączy etykiety jednej wartości", () => {
    expect(conditionsText(parameter())).toBe("");
    expect(
      conditionsText(
        parameter({
          conditions: [
            { kind: "building_type", label: "budynki usługowe", quote: "budynków usługowych" },
            FLAT_ROOF,
          ],
        }),
      ),
    ).toBe("budynki usługowe; dach płaski");
  });

  it("każdy rodzaj warunku i wartości ma polską etykietę", () => {
    expect(Object.keys(CONDITION_KIND_LABELS).sort()).toEqual(
      ["building_type", "location", "other", "roof_type", "subzone"],
    );
    expect(Object.keys(VALUE_KIND_LABELS).sort()).toEqual(["conditional", "conflict", "unconditional"]);
    expect(CONDITION_KIND_LABELS.roof_type).toBe("rodzaj dachu");
  });

  it("summarizeValueKinds rozdziela sprzeczność od wartości warunkowych", () => {
    const summary = summarizeValueKinds([
      parameter(),
      parameter({ value_kind: "conditional", conditions: [FLAT_ROOF] }),
      parameter({ conditions: [FLAT_ROOF], normalized_value: 10 }),
      parameter({ conflict_group_id: "g" }),
    ]);
    expect(summary).toEqual({ conflict: 1, conditional: 2, unconditional: 1 });
    expect(summarizeValueKinds([])).toEqual({ conflict: 0, conditional: 0, unconditional: 0 });
  });
});
