import { formatPlDate } from "@/lib/pogStatus";
import { shortSha } from "@/lib/safeLink";
import type { MpzpAssignmentMethod, MpzpParameterEvidence, MpzpZoneResult } from "@/lib/types";

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

/**
 * Strefa MPZP z pochodzeniem przypisania (BK-202) i cytowalnym dowodem
 * każdej wartości parametru (BK-203). Sprzeczne kandydatury są pokazywane
 * obok siebie z ostrzeżeniem; żadna nie jest wybierana automatycznie.
 */
export function MpzpZoneCard({ zone }: { zone: MpzpZoneResult }) {
  const method = zone.assignment_method ?? "legacy";
  const parameters = zone.parameters ?? [];
  const hasConflict = parameters.some((parameter) => parameter.conflict_group_id);
  return (
    <li className="result-list-item" data-zone-id={zone.zone_id ?? undefined}>
      <div className="result-list-item-heading">
        <strong>{zone.zone_symbol}</strong>
        {zone.is_dominant && <span className="tag-dominant">największy udział</span>}
        {zone.touches_boundary && <span className="manual-review">tylko styczność granicy</span>}
      </div>
      {zone.primary_use && <p>Przeznaczenie: {zone.primary_use}</p>}
      <p>
        {zone.touches_boundary
          ? "Brak udziału powierzchniowego — wydzielenie styka się z granicą działki."
          : `Udział w powierzchni działki: ${zone.intersection_pct.toFixed(1)}% (${zone.intersection_area_sqm.toLocaleString("pl-PL", { maximumFractionDigits: 1 })} m²)`}
      </p>
      <p className="field-hint">Sposób przypisania: {ASSIGNMENT_LABELS[method]}</p>
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
              {parameters.map((parameter, index) => (
                <tr
                  key={`${parameter.name}-${index}`}
                  data-conflict={parameter.conflict_group_id ? "true" : undefined}
                >
                  <th scope="row">
                    {parameter.name}
                    {parameter.conflict_group_id && (
                      <span className="manual-review"> · sprzeczna kandydatura</span>
                    )}
                  </th>
                  <td>
                    {formatValue(parameter)}
                    {parameter.raw_value && (
                      <span className="field-hint"> (dosłownie: „{parameter.raw_value}”)</span>
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
              ))}
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
