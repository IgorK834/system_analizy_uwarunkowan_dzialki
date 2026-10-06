"use client";

import { useId, useState } from "react";

import {
  CONDITION_KIND_LABELS,
  conditionsOf,
  summarizeValueKinds,
  valueKindOf,
} from "@/lib/mpzpConditions";
import {
  EXTRACTION_METHOD_LLM_VERIFIED,
  MODEL_READING_DISCLAIMER,
  MODEL_READING_MARK,
  MODEL_READING_SHORT,
  MODEL_READING_SR_TEXT,
  NO_DATA_NOT_NO_RESTRICTION,
  NULL_NOT_ZERO,
  isModelReading,
  modelProvenance,
  needsManualReview,
  selectParameters,
  summarizeProvenance,
  valueStateOf,
} from "@/lib/mpzpProvenance";
import { formatPlDate } from "@/lib/pogStatus";
import { shortSha } from "@/lib/safeLink";
import type {
  ManualZoneSelection,
  MpzpAssignmentMethod,
  MpzpParameterEvidence,
  MpzpZoneResult,
} from "@/lib/types";

const ASSIGNMENT_LABELS: Record<MpzpAssignmentMethod, string> = {
  vector_intersection: "przecięcie z wektorem wydzieleń",
  document_candidate: "kandydat z discovery/dokumentu (bez wektora)",
  manual_user_input: "symbol podany ręcznie z mapy rastrowej",
  legacy: "snapshot sprzed wersjonowania stref",
};

const EXTRACTION_LABELS: Record<string, string> = {
  pdf_text: "tekst PDF",
  html: "HTML",
  ocr: "OCR",
  [EXTRACTION_METHOD_LLM_VERIFIED]: MODEL_READING_SHORT,
};

function formatValue(parameter: MpzpParameterEvidence): string {
  const value =
    typeof parameter.normalized_value === "number"
      ? parameter.normalized_value.toLocaleString("pl-PL")
      : parameter.normalized_value;
  return parameter.unit ? `${value} ${parameter.unit}` : String(value);
}

/**
 * Wartość albo jawny brak danych: `null` to „brak danych” (nie zero i nie brak ograniczenia), a 0 to
 * liczba 0. Stan jest też w atrybucie `data-value-state`.
 */
function ValueText({ parameter }: { parameter: MpzpParameterEvidence }) {
  const state = valueStateOf(parameter);
  if (state === "null") {
    return (
      <span className="value-null" data-value-state="null">
        brak danych
        <span className="visually-hidden">
          {" "}
          — wartości nie ustalono z uchwały; to nie jest zero ani brak ograniczenia
        </span>
      </span>
    );
  }
  return <span data-value-state={state}>{formatValue(parameter)}</span>;
}

function evidenceLocation(parameter: MpzpParameterEvidence): string {
  const parts = [
    parameter.page_number != null ? `str. ${parameter.page_number}` : null,
    parameter.segment_id ? `segment ${parameter.segment_id}` : null,
    parameter.legal_unit_id != null ? `jednostka #${parameter.legal_unit_id}` : null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? parts.join(", ") : "miejsce w dokumencie nieustalone";
}

function shareText(zone: MpzpZoneResult): string {
  if (zone.touches_boundary) {
    return "Brak udziału powierzchniowego — wydzielenie styka się z granicą działki.";
  }
  if (zone.intersection_pct == null || zone.intersection_area_sqm == null) {
    return "Udział w powierzchni działki: nieustalony (brak wektorowej granicy strefy).";
  }
  return `Udział w powierzchni działki: ${zone.intersection_pct.toFixed(1)}% (${zone.intersection_area_sqm.toLocaleString("pl-PL", { maximumFractionDigits: 1 })} m²)`;
}

function ManualSelectionDetails({ selection }: { selection: ManualZoneSelection }) {
  return (
    <dl className="result-summary manual-selection" aria-label="Decyzja użytkownika">
      <div>
        <dt>Wpisany symbol</dt>
        <dd>
          {selection.entered_symbol}
          {selection.entered_symbol_raw != null &&
            selection.entered_symbol_raw !== selection.entered_symbol && (
              <span className="field-hint">
                {" "}(wpisano: „{selection.entered_symbol_raw}”)
              </span>
            )}
          {!selection.symbol_in_candidates && (
            <span className="manual-review"> · spoza kandydatów discovery</span>
          )}
        </dd>
      </div>
      <div>
        <dt>Plan i kandydaci</dt>
        <dd>
          {selection.plan_id ?? "plan nieustalony"} ·{" "}
          {selection.candidate_zone_symbols.length > 0
            ? selection.candidate_zone_symbols.join(", ")
            : "brak kandydatów"}
        </dd>
      </div>
      <div>
        <dt>Dokument</dt>
        <dd className="mono" title={selection.document_sha256 ?? undefined}>
          {selection.document_pinned
            ? `kopia przypięta przy wstrzymaniu · SHA-256 ${shortSha(selection.document_sha256)}`
            : "dokument nie został przypięty — parametry nieustalone"}
        </dd>
      </div>
    </dl>
  );
}

/**
 * Strefa MPZP z pochodzeniem przypisania (BK-202) i cytowalnym dowodem
 * każdej wartości parametru (BK-203). Sprzeczne kandydatury są pokazywane
 * obok siebie z ostrzeżeniem; żadna nie jest wybierana automatycznie. Wartości
 * warunkowe (PV3-08, np. inna wysokość dla dachu płaskiego) są pokazywane z
 * warunkiem i jego cytatem — to nie jest sprzeczność.
 *
 * PV3-18: wartość z modelu językowego jest zawsze oznaczona („odczyt automatyczny (model językowy),
 * zweryfikowany z cytatem — wymaga potwierdzenia”), z cytatem, stroną, warunkami i provenance (model,
 * wersja instrukcji, skrót odpowiedzi); wartość deterministyczna nie jest tak oznaczana. Filtr „do ręcznej
 * weryfikacji” zawęża tabelę. Brak danych (`null`) jest różny od 0 i od braku ograniczenia. Odczyt nie
 * jest przedstawiany jako interpretacja prawna.
 */
export function MpzpZoneCard({ zone }: { zone: MpzpZoneResult }) {
  const method = zone.assignment_method ?? "legacy";
  const parameters = zone.parameters ?? [];
  const kinds = summarizeValueKinds(parameters);
  const provenance = summarizeProvenance(parameters);
  const hasConflict = kinds.conflict > 0;
  const hasConditional = kinds.conditional > 0;
  const [onlyManualReview, setOnlyManualReview] = useState(false);
  const filterStatusId = useId();
  const shown = selectParameters(parameters, onlyManualReview);
  return (
    <li className="result-list-item" data-zone-id={zone.zone_id ?? undefined}>
      <div className="result-list-item-heading">
        <strong>{zone.zone_symbol}</strong>
        {zone.is_dominant && <span className="tag-dominant">największy udział</span>}
        {zone.touches_boundary && <span className="manual-review">tylko styczność granicy</span>}
      </div>
      {method === "manual_user_input" && (
        <p className="manual-review" role="note" data-testid="manual-zone-banner">
          Symbol strefy podano ręcznie — bez wektorowej granicy strefy. Każdy
          parametr tej strefy wymaga weryfikacji, a wynik nie może być pełny.
        </p>
      )}
      {zone.primary_use && <p>Przeznaczenie: {zone.primary_use}</p>}
      <p>{shareText(zone)}</p>
      <p className="field-hint">Sposób przypisania: {ASSIGNMENT_LABELS[method]}</p>
      {zone.manual_selection && <ManualSelectionDetails selection={zone.manual_selection} />}
      {zone.zone_id && <p className="field-hint mono">ID wydzielenia: {zone.zone_id}</p>}
      {zone.act_identifier && (
        <p className="field-hint">
          Plan {zone.act_identifier}
          {zone.act_version ? ` · wersja ${shortSha(zone.act_version)}` : ""}
          {zone.data_release_id != null ? ` · wydanie #${zone.data_release_id}` : ""}
        </p>
      )}
      {hasConflict && (
        <p className="manual-review" role="note">
          Uchwała podaje sprzeczne wartości parametru — żadna nie została wybrana automatycznie.
        </p>
      )}
      {hasConditional && (
        <p className="field-hint" role="note" data-testid="conditional-values-note">
          Część parametrów ma wartości zależne od warunków (rodzaj zabudowy, rodzaj dachu, podstrefa,
          położenie) — to nie jest sprzeczność. Pole zbiorcze strefy jest puste, gdy uchwała nie podaje
          jednej wartości dla całej strefy.
        </p>
      )}
      {provenance.model > 0 && (
        <p className="model-reading-note" role="note" data-testid="model-reading-note">
          <strong>
            {provenance.model === 1
              ? "1 wartość to"
              : `${provenance.model} wartości to`}{" "}
            {MODEL_READING_SHORT}.
          </strong>{" "}
          {MODEL_READING_DISCLAIMER}
        </p>
      )}
      {zone.manual_review_required && !hasConflict && (
        <p className="manual-review">Przypisanie lub parametry wymagają weryfikacji.</p>
      )}
      {parameters.length > 0 && (
        <>
          {provenance.manualReview > 0 && (
            <div className="review-filter-bar">
              <button
                type="button"
                className="review-filter"
                aria-pressed={onlyManualReview}
                aria-describedby={filterStatusId}
                data-testid="manual-review-filter"
                onClick={() => setOnlyManualReview((current) => !current)}
              >
                Tylko do ręcznej weryfikacji ({provenance.manualReview})
              </button>
              <span id={filterStatusId} className="field-hint" role="status" aria-live="polite">
                Widoczne parametry: {shown.length} z {parameters.length}.
              </span>
            </div>
          )}
          <div
            className="result-table-scroll"
            role="region"
            aria-label={`Parametry strefy ${zone.zone_symbol} (przewijana tabela)`}
            tabIndex={0}
          >
            <table className="result-table">
              <caption>Parametry strefy {zone.zone_symbol} i ich źródło w uchwale</caption>
              <thead>
                <tr>
                  <th scope="col">Parametr</th>
                  <th scope="col">Wartość</th>
                  <th scope="col">Źródło</th>
                  <th scope="col">Pewność</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(({ parameter, index }) => {
                  const valueKind = valueKindOf(parameter);
                  const conditions = conditionsOf(parameter);
                  const fromModel = isModelReading(parameter);
                  const origin = modelProvenance(parameter);
                  return (
                    <tr
                      key={`${parameter.name}-${index}`}
                      data-conflict={valueKind === "conflict" ? "true" : undefined}
                      data-value-kind={valueKind}
                      data-model-reading={fromModel ? "true" : undefined}
                      data-needs-review={needsManualReview(parameter) ? "true" : undefined}
                    >
                      <th scope="row">
                        {parameter.name}
                        {valueKind === "conflict" && (
                          <span className="manual-review"> · sprzeczna kandydatura</span>
                        )}
                        {valueKind === "conditional" && (
                          <span className="field-hint" data-testid="conditional-tag"> · warunkowa</span>
                        )}
                        {parameter.manual_review_required && valueKind !== "conflict" && !fromModel && (
                          <span className="manual-review" data-testid="parameter-review">
                            {" "}· wymaga weryfikacji
                          </span>
                        )}
                        {fromModel && (
                          <span className="model-reading-tag" data-testid="model-reading-tag">
                            {MODEL_READING_MARK}
                            <span className="visually-hidden"> {MODEL_READING_SR_TEXT}</span>
                          </span>
                        )}
                      </th>
                      <td>
                        <ValueText parameter={parameter} />
                        {parameter.raw_value && (
                          <span className="field-hint"> (dosłownie: „{parameter.raw_value}”)</span>
                        )}
                        {conditions.length > 0 && (
                          <ul className="field-hint value-conditions" aria-label="Warunki wartości">
                            {conditions.map((condition) => (
                              <li key={`${condition.kind}-${condition.label}`} data-condition-kind={condition.kind}>
                                {CONDITION_KIND_LABELS[condition.kind]}: {condition.label}
                                {condition.quote ? ` — „${condition.quote}”` : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td>
                        {evidenceLocation(parameter)}
                        {parameter.evidence_text && (
                          <blockquote className="field-hint">„{parameter.evidence_text}”</blockquote>
                        )}
                        <span className="field-hint mono" title={parameter.document_sha256 ?? undefined}>
                          {EXTRACTION_LABELS[parameter.extraction_method ?? ""] ?? "metoda nieznana"}
                          {parameter.document_sha256
                            ? ` · SHA-256 ${shortSha(parameter.document_sha256)}`
                            : ""}
                        </span>
                        {fromModel && (
                          <span className="field-hint model-provenance" data-testid="model-provenance">
                            Model: {origin.modelId} · instrukcja: {origin.promptVersion} · SHA-256 odpowiedzi:{" "}
                            <span className="mono" title={origin.responseSha256 ?? undefined}>
                              {shortSha(origin.responseSha256)}
                            </span>
                          </span>
                        )}
                      </td>
                      <td>{(parameter.confidence * 100).toFixed(0)}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="field-hint" data-testid="no-data-note">
            {NO_DATA_NOT_NO_RESTRICTION} {NULL_NOT_ZERO}
          </p>
        </>
      )}
      <p className="field-hint">
        Pewność przypisania: {(zone.source.confidence * 100).toFixed(0)}%
        {zone.source.fetched_at ? ` · dane z ${formatPlDate(zone.source.fetched_at)}` : ""}
      </p>
    </li>
  );
}
