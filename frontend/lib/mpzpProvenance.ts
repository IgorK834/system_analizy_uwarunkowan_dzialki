import { valueKindOf } from "@/lib/mpzpConditions";
import type { MpzpParameterEvidence } from "@/lib/types";

/**
 * Pochodzenie wartości parametru MPZP (PV3-18): odczyt automatyczny modelem językowym kontra odczyt
 * deterministyczny. Wartość z modelu trafia do wyniku wyłącznie po deterministycznej weryfikacji względem
 * tekstu uchwały i ma status kandydata do ręcznej weryfikacji (`ai_candidate`) — nigdy „verified”.
 * Interfejs nie przedstawia jej jako interpretacji prawnej.
 *
 * Brzmienie oznaczenia i komunikatów jest to samo co w raporcie PDF (`backend/app/shared/model_reading.py`);
 * test backendu pilnuje, żeby teksty w obu miejscach się nie rozjechały.
 */
export const REVIEW_STATUS_AI_CANDIDATE = "ai_candidate";
export const EXTRACTION_METHOD_LLM_VERIFIED = "llm_verified";

export const MODEL_READING_MARK =
  "odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia";
export const MODEL_READING_SHORT = "odczyt automatyczny (model językowy)";
export const MODEL_READING_DISCLAIMER =
  "Odczyt automatyczny nie jest interpretacją prawną: model językowy zaproponował wartość, a program potwierdził jedynie, że cytat i liczba występują w tekście uchwały na wskazanej stronie. Treść, zakres i warunki obowiązywania ustalenia należy potwierdzić w uchwale.";
export const NO_DATA_NOT_NO_RESTRICTION =
  "Brak danych nie oznacza braku ograniczenia: brak wartości parametru znaczy, że nie ustalono jej z uchwały, a nie że uchwała niczego nie ogranicza.";
export const NULL_NOT_ZERO = "Brak wartości (null) nie jest zerem.";
export const MODEL_READING_SR_TEXT =
  "Wartość z odczytu automatycznego modelem językowym: zweryfikowana programowo z cytatem, ale wymaga potwierdzenia przez człowieka w uchwale.";

/**
 * Wartość z modelu: status `ai_candidate` albo metoda `llm_verified` (weryfikator nadaje oba naraz).
 * Rozpoznanie nie zależy od wyglądu wartości ani pewności — wartość deterministyczna nie jest
 * oznaczana jako odczyt modelu.
 */
export function isModelReading(parameter: MpzpParameterEvidence): boolean {
  return (
    parameter.review_status === REVIEW_STATUS_AI_CANDIDATE ||
    parameter.extraction_method === EXTRACTION_METHOD_LLM_VERIFIED
  );
}

/** Czy wartość trzeba potwierdzić ręcznie: odczyt modelu, sprzeczność albo flaga parsera. */
export function needsManualReview(parameter: MpzpParameterEvidence): boolean {
  return (
    isModelReading(parameter) ||
    parameter.manual_review_required ||
    valueKindOf(parameter) === "conflict"
  );
}

export type ProvenanceSummary = {
  total: number;
  model: number;
  deterministic: number;
  manualReview: number;
};

export function summarizeProvenance(parameters: MpzpParameterEvidence[]): ProvenanceSummary {
  let model = 0;
  let manualReview = 0;
  for (const parameter of parameters) {
    if (isModelReading(parameter)) model += 1;
    if (needsManualReview(parameter)) manualReview += 1;
  }
  return { total: parameters.length, model, deterministic: parameters.length - model, manualReview };
}

/** Parametry razem z ich pozycją w pełnej liście (stabilny klucz wiersza) — opcjonalnie tylko do ręcznej weryfikacji. */
export function selectParameters(
  parameters: MpzpParameterEvidence[],
  onlyManualReview: boolean,
): { parameter: MpzpParameterEvidence; index: number }[] {
  return parameters
    .map((parameter, index) => ({ parameter, index }))
    .filter(({ parameter }) => !onlyManualReview || needsManualReview(parameter));
}

export type ValueState = "value" | "zero" | "null";

/** `null` (brak danych) to coś innego niż 0: stan wartości rozróżnia oba przypadki. */
export function valueStateOf(parameter: MpzpParameterEvidence): ValueState {
  if (parameter.normalized_value === null || parameter.normalized_value === undefined) return "null";
  return parameter.normalized_value === 0 ? "zero" : "value";
}

/** Linia provenance wartości z modelu: model, wersja instrukcji, skrót odpowiedzi (pełny skrót w `title`). */
export function modelProvenance(parameter: MpzpParameterEvidence): {
  modelId: string;
  promptVersion: string;
  responseSha256: string | null;
} {
  return {
    modelId: parameter.model_id ?? "nieznany",
    promptVersion: parameter.prompt_version ?? "nieznana",
    responseSha256: parameter.response_sha256 ?? null,
  };
}
