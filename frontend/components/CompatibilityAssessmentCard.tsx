import { compatibilityStatusLabel, formatShare } from "@/lib/compatibility";
import { formatPlDate } from "@/lib/pogStatus";
import type { CompatibilityAssessment } from "@/lib/types";

/**
 * Informacyjna ocena relacji MPZP–POG (BK-205) jako osobna sekcja wyniku.
 *
 * Ustalenia MPZP i POG są prezentowane w swoich sekcjach; tu pokazujemy wyłącznie
 * wynik jawnej tabeli reguł dla par stref, z datą stanu prawnego, regułą i
 * uzasadnieniem. Sekcja nigdy nie stwierdza prawnej możliwości zabudowy.
 */
export function CompatibilityAssessmentCard({
  assessment,
}: {
  assessment: CompatibilityAssessment | null | undefined;
}) {
  return (
    <section className="result-section" aria-label="Relacja MPZP–POG">
      <h3>Relacja MPZP–POG — analiza informacyjna</h3>
      <p className="field-hint">
        Ustalenia MPZP i POG są przedstawione osobno powyżej. Ta ocena nie jest
        opinią prawną i nie stwierdza prawnej możliwości zabudowy.
      </p>
      {!assessment && (
        <p className="section-empty">Nie wykonano oceny relacji MPZP–POG dla tej analizy.</p>
      )}
      {assessment && (
        <>
          <dl className="result-summary">
            <div>
              <dt>Wynik oceny</dt>
              <dd data-testid="compatibility-status" data-status={assessment.status}>
                {compatibilityStatusLabel(assessment.status)}
              </dd>
            </div>
            <div>
              <dt>Stan prawny na dzień</dt>
              <dd>{assessment.as_of ? formatPlDate(assessment.as_of) : "nieustalony"}</dd>
            </div>
            <div>
              <dt>Zestaw reguł</dt>
              <dd className="mono">
                {assessment.rule_id
                  ? `${assessment.rule_id} v${assessment.rule_version ?? "?"}`
                  : "brak — zapis historyczny"}
              </dd>
            </div>
          </dl>
          <p>{assessment.rationale}</p>
          {assessment.legacy_evidence && (
            <p className="manual-review" role="note">
              Zapis historyczny sprzed BK-205 ({assessment.legacy_evidence.origin}):{" "}
              {assessment.legacy_evidence.conflict_with_mpzp === true
                ? "stwierdzono konflikt"
                : assessment.legacy_evidence.conflict_with_mpzp === false
                  ? "brak konfliktu"
                  : "brak rozstrzygnięcia"}{" "}
              — bez danych reguły, nie jest pełną oceną.
            </p>
          )}
          {assessment.zone_pairs.length > 0 && (
            <div className="result-table-scroll">
              <table className="result-table">
                <caption>Pary stref MPZP × POG</caption>
                <thead>
                  <tr>
                    <th>Para</th>
                    <th>Wynik</th>
                    <th>Wspólna część działki</th>
                    <th>Reguła</th>
                    <th>Uzasadnienie</th>
                  </tr>
                </thead>
                <tbody>
                  {assessment.zone_pairs.map((pair) => (
                    <tr
                      key={`${pair.mpzp_zone_id ?? pair.mpzp_zone_symbol}-${pair.pog_zone_id}`}
                      data-status={pair.status}
                    >
                      <th scope="row">
                        {pair.mpzp_zone_symbol} × {pair.pog_zone_symbol ?? pair.pog_zone_type}
                      </th>
                      <td>{compatibilityStatusLabel(pair.status)}</td>
                      <td>
                        {formatShare(pair.overlap_area_sqm, pair.overlap_pct)}
                        {!pair.spatially_identified && (
                          <span className="field-hint"> · para niezidentyfikowana przestrzennie</span>
                        )}
                      </td>
                      <td className="mono">
                        {pair.rule_id ? `${pair.rule_id} v${pair.rule_version}` : "brak reguły"}
                        {pair.as_of ? ` · stan na ${formatPlDate(pair.as_of)}` : ""}
                      </td>
                      <td className="field-hint">{pair.rationale}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {assessment.sources.length > 0 && (
            <ul className="field-hint" aria-label="Źródła oceny">
              {assessment.sources.map((source, index) => (
                <li key={`${source.kind}-${index}`}>
                  {source.label}
                  {source.version ? ` · wersja ${source.version}` : ""}
                  {source.as_of ? ` · stan na ${formatPlDate(source.as_of)}` : ""}
                </li>
              ))}
            </ul>
          )}
          {assessment.manual_review_required && (
            <p className="manual-review">Ocena relacji wymaga ręcznej weryfikacji.</p>
          )}
          <p className="legal-disclaimer">{assessment.informational_notice}</p>
        </>
      )}
    </section>
  );
}
