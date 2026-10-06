import { describe, expect, it } from "vitest";

import {
  MODEL_READING_DISCLAIMER,
  MODEL_READING_MARK,
  MODEL_READING_SHORT,
  isModelReading,
  modelProvenance,
  needsManualReview,
  selectParameters,
  summarizeProvenance,
  valueStateOf,
} from "@/lib/mpzpProvenance";
import type { MpzpParameterEvidence } from "@/lib/types";

const RESPONSE_SHA = "c".repeat(64);

function parameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return {
    name: "max_storeys",
    normalized_value: 3,
    raw_value: "3 kondygnacje",
    unit: null,
    evidence_text: "zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne",
    page_number: 13,
    segment_id: null,
    legal_unit_id: null,
    document_sha256: "a".repeat(64),
    document_version_id: 3,
    parser_version: "mpzp-parser/3.0-det",
    extraction_method: "pdf_text",
    confidence: 0.9,
    conflict_group_id: null,
    manual_review_required: false,
    ...overrides,
  };
}

function modelParameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return parameter({
    extraction_method: "llm_verified",
    review_status: "ai_candidate",
    manual_review_required: true,
    confidence: 0.62,
    model_id: "gemini-3.8-flash",
    prompt_version: "mpzp-extraction/1",
    response_sha256: RESPONSE_SHA,
    ...overrides,
  });
}

describe("rozpoznanie odczytu automatycznego", () => {
  it("oznacza wyłącznie wartości ze statusem ai_candidate albo metodą llm_verified", () => {
    expect(isModelReading(modelParameter())).toBe(true);
    expect(isModelReading(parameter({ review_status: "ai_candidate" }))).toBe(true);
    expect(isModelReading(parameter({ extraction_method: "llm_verified" }))).toBe(true);
  });

  it("nie oznacza wartości deterministycznych — ani niską pewnością, ani flagą weryfikacji, ani OCR", () => {
    expect(isModelReading(parameter())).toBe(false);
    expect(isModelReading(parameter({ confidence: 0.05 }))).toBe(false);
    expect(isModelReading(parameter({ manual_review_required: true }))).toBe(false);
    expect(isModelReading(parameter({ extraction_method: "ocr" }))).toBe(false);
    expect(isModelReading(parameter({ model_id: "gemini-3.8-flash" }))).toBe(false);
  });

  it("oznaczenie ma uzgodnione brzmienie i nie przedstawia odczytu jako interpretacji prawnej", () => {
    expect(MODEL_READING_MARK).toBe(
      "odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia",
    );
    expect(MODEL_READING_MARK.startsWith(MODEL_READING_SHORT)).toBe(true);
    expect(MODEL_READING_DISCLAIMER).toContain("nie jest interpretacją prawną");
  });
});

describe("wymóg ręcznej weryfikacji i filtr", () => {
  it("wymaga potwierdzenia: odczyt modelu, sprzeczność i flaga parsera — nie wartość pewna", () => {
    expect(needsManualReview(modelParameter({ manual_review_required: false }))).toBe(true);
    expect(needsManualReview(parameter({ conflict_group_id: "g1" }))).toBe(true);
    expect(needsManualReview(parameter({ manual_review_required: true }))).toBe(true);
    expect(needsManualReview(parameter())).toBe(false);
  });

  it("podsumowuje pochodzenie i zachowuje pozycje wierszy po filtrowaniu", () => {
    const list = [parameter(), modelParameter(), parameter({ manual_review_required: true }), parameter()];
    expect(summarizeProvenance(list)).toEqual({ total: 4, model: 1, deterministic: 3, manualReview: 2 });
    expect(selectParameters(list, false).map((item) => item.index)).toEqual([0, 1, 2, 3]);
    expect(selectParameters(list, true).map((item) => item.index)).toEqual([1, 2]);
    expect(summarizeProvenance([])).toEqual({ total: 0, model: 0, deterministic: 0, manualReview: 0 });
  });
});

describe("brak danych a zero", () => {
  it("rozróżnia null, 0 i wartość", () => {
    expect(valueStateOf(parameter({ normalized_value: null }))).toBe("null");
    expect(valueStateOf(parameter({ normalized_value: 0 }))).toBe("zero");
    expect(valueStateOf(parameter({ normalized_value: 0.5 }))).toBe("value");
    expect(valueStateOf(parameter({ normalized_value: "usługi" }))).toBe("value");
    expect(valueStateOf(parameter({ normalized_value: undefined as unknown as null }))).toBe("null");
  });
});

describe("provenance modelu", () => {
  it("zwraca model, wersję instrukcji i skrót odpowiedzi albo jawny brak", () => {
    expect(modelProvenance(modelParameter())).toEqual({
      modelId: "gemini-3.8-flash",
      promptVersion: "mpzp-extraction/1",
      responseSha256: RESPONSE_SHA,
    });
    expect(modelProvenance(parameter())).toEqual({ modelId: "nieznany", promptVersion: "nieznana", responseSha256: null });
  });
});
