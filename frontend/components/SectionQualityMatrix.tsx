"use client";

import { useState } from "react";

import {
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
import type { SectionQualityMatrix as Matrix } from "@/lib/types";

type SectionQualityMatrixProps = {
  matrix: Matrix | null | undefined;
  /** Punkt odniesienia ostrzeżenia o wieku „na dziś”; domyślnie chwila otwarcia widoku. */
  now?: Date;
};

/**
 * Macierz kompletności i świeżości sekcji analizy (BK-504).
 *
 * Każda sekcja — także pusta — ma status według kontraktu źródła, źródło albo
 * powód jego braku, czas pobrania, wydanie, flagę ręcznej weryfikacji i osobną
 * świeżość. Zbiorczy status analizy nie jest gwarancją kompletności: użytkownik
 * odróżnia stary pomiar, brak pokrycia i błąd źródła. Ostrzeżenie o wieku na dziś
 * jest osobne i nie zmienia zapisanej, historycznej oceny.
 */
export function SectionQualityMatrix({ matrix, now }: SectionQualityMatrixProps) {
  const [openedAt] = useState(() => new Date());
  const value = matrixOrNull(matrix);

  if (value === null) {
    return (
      <section className="result-section" aria-label="Kompletność i świeżość danych">
        <h3>Kompletność i świeżość danych</h3>
        <p className="manual-review" data-testid="quality-missing">
          Odpowiedź nie zawiera macierzy kompletności i świeżości sekcji (zapis sprzed jej
          wprowadzenia). Brak informacji nie oznacza, że dane są aktualne ani kompletne.
        </p>
      </section>
    );
  }

  const warnings = ageWarnings(value, now ?? openedAt);

  return (
    <section className="result-section" aria-label="Kompletność i świeżość danych">
      <h3>Kompletność i świeżość danych</h3>
      <p className="result-preview" data-testid="quality-scope-note">
        Status i świeżość dotyczą każdej sekcji osobno — żaden zbiorczy status nie gwarantuje
        kompletności całej analizy. Ocena została zapisana z analizą (punkt odniesienia:{" "}
        {formatFetchedAt(value.reference_at)}; polityka{" "}
        <span className="mono">{value.policy_version}</span>).
      </p>
      {value.origin === "reconstructed" && (
        <p className="manual-review" role="note" data-testid="quality-reconstructed">
          Zapis sprzed macierzy jakości: ocenę odtworzono przy odczycie bieżącą polityką — nie
          odzwierciedla oceny z chwili analizy.
        </p>
      )}
      <ul className="quality-list" data-testid="quality-matrix" aria-label="Kompletność i świeżość sekcji analizy">
        {value.sections.map((item) => (
          <li className="quality-card" key={item.section} data-testid={`quality-row-${item.section}`}>
            <div className="quality-card-head">
              <h4>{sectionLabel(item.section)}</h4>
              <span className="quality-badges">
                <span
                  className={`quality-status quality-tone-${QUALITY_STATUS_TONES[item.status]}`}
                  data-status={item.status}
                >
                  <span aria-hidden="true" className="quality-mark">
                    {QUALITY_STATUS_MARKS[item.status]}
                  </span>
                  {statusLabel(value, item.status)}
                </span>
                {item.manual_review_required && (
                  <span className="quality-tag quality-tag-review" data-testid="quality-manual">
                    wymaga weryfikacji
                  </span>
                )}
              </span>
            </div>
            <dl className="quality-facts">
              <div>
                <dt>Źródło</dt>
                <dd data-testid="quality-source">
                  {item.source_name ? (
                    <>
                      {item.source_name}
                      {item.source_id && <span className="mono"> ({item.source_id})</span>}
                    </>
                  ) : (
                    "brak źródła"
                  )}
                </dd>
              </div>
              <div>
                <dt>Pobrano</dt>
                <dd>{formatFetchedAt(item.fetched_at)}</dd>
              </div>
              <div>
                <dt>Wydanie</dt>
                <dd>
                  {item.data_release_id != null ? `#${item.data_release_id}` : "—"}
                  {item.source_version && <span className="mono"> {item.source_version}</span>}
                </dd>
              </div>
              <div>
                <dt>Świeżość</dt>
                <dd>
                  <span
                    className={`quality-freshness quality-freshness-${item.freshness.state}`}
                    data-freshness={item.freshness.state}
                  >
                    {freshnessLabel(value, item.freshness.state)}
                  </span>
                  <small className="quality-detail">{freshnessDetail(item)}</small>
                </dd>
              </div>
              <div>
                <dt>Powód</dt>
                <dd data-testid="quality-reasons">
                  {item.reason_codes.length > 0
                    ? item.reason_codes.map((code) => (
                        <span className="quality-reason" key={code}>
                          {reasonLabel(value, code)}
                        </span>
                      ))
                    : "—"}
                </dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>
      {warnings.length > 0 && (
        <p className="manual-review" role="note" data-testid="quality-age-warning">
          Ostrzeżenie o wieku na dziś: dane sekcji{" "}
          {warnings
            .map((warning) => `${sectionLabel(warning.section)} (${formatAge(warning.ageSeconds)})`)
            .join(", ")}{" "}
          są starsze niż reguła wieku ich źródeł. To osobne ostrzeżenie — nie zmienia oceny
          historycznej z chwili analizy.
        </p>
      )}
      <details className="quality-legend" data-testid="quality-legend">
        <summary>Legenda statusów i świeżości</summary>
        <dl>
          {value.legend.statuses.map((item) => (
            <div key={item.id}>
              <dt>{item.label}</dt>
              <dd>{item.description}</dd>
            </div>
          ))}
          {value.legend.freshness.map((item) => (
            <div key={item.id}>
              <dt>{item.label}</dt>
              <dd>{item.description}</dd>
            </div>
          ))}
        </dl>
      </details>
      <p className="quality-hash">
        Suma kontrolna macierzy (SHA-256):{" "}
        <span className="mono" data-testid="quality-hash">
          {value.matrix_sha256}
        </span>
      </p>
    </section>
  );
}
