import {
  CONDITION_KIND_LABELS,
  conditionsOf,
  summarizeValueKinds,
  valueKindOf,
} from "@/lib/mpzpConditions";
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
};

function formatValue(parameter: MpzpParameterEvidence): string {
  if (parameter.normalized_value === null) return "—";
  const value =
    typeof parameter.normalized_value === "number"
      ? parameter.normalized_value.toLocaleString("pl-PL")
      : parameter.normalized_value;
  return parameter.unit ? `${value} ${parameter.unit}` : value;
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
 */
export function MpzpZoneCard({ zone }: { zone: MpzpZoneResult }) {
  const method = zone.assignment_method ?? "legacy";
  const parameters = zone.parameters ?? [];
  const kinds = summarizeValueKinds(parameters);
  const hasConflict = kinds.conflict > 0;
  const hasConditional = kinds.conditional > 0;
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
      {zone.manual_review_required && !hasConflict && (
        <p className="manual-review">Przypisanie lub parametry wymagają weryfikacji.</p>
      )}
      {parameters.length > 0 && (
        <div className="result-table-scroll">
          <table className="result-table">
            <caption>Parametry strefy {zone.zone_symbol} i ich źródło w uchwale</caption>
            <thead>
              <tr>
                <th>Parametr</th>
                <th>Wartość</th>
                <th>Źródło</th>
                <th>Pewność</th>
              </tr>
            </thead>
            <tbody>
              {parameters.map((parameter, index) => {
                const valueKind = valueKindOf(parameter);
                const conditions = conditionsOf(parameter);
                return (
                <tr
                  key={`${parameter.name}-${index}`}
                  data-conflict={valueKind === "conflict" ? "true" : undefined}
                  data-value-kind={valueKind}
                >
                  <th scope="row">
                    {parameter.name}
                    {valueKind === "conflict" && (
                      <span className="manual-review"> · sprzeczna kandydatura</span>
                    )}
                    {valueKind === "conditional" && (
                      <span className="field-hint" data-testid="conditional-tag"> · warunkowa</span>
                    )}
                    {parameter.manual_review_required && valueKind !== "conflict" && (
                      <span className="manual-review" data-testid="parameter-review">
                        {" "}· wymaga weryfikacji
                      </span>
                    )}
                  </th>
                  <td>
                    {formatValue(parameter)}
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
                  </td>
                  <td>{(parameter.confidence * 100).toFixed(0)}%</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="field-hint">
        Pewność przypisania: {(zone.source.confidence * 100).toFixed(0)}%
        {zone.source.fetched_at ? ` · dane z ${formatPlDate(zone.source.fetched_at)}` : ""}
      </p>
    </li>
  );
}
