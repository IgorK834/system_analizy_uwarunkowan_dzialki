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
import { QUALITY_STATUS_LABELS } from "@/lib/quality";
import type { AnalyzeResponse } from "@/lib/types";

type CompletenessBarProps = {
  completeness: AnalyzeResponse["completeness"];
  /** Status nagłówkowy zachowany dla zgodności; pokazywany tylko, gdy odpowiedź nie niesie kompletności. */
  status: string;
};

/**
 * Pasek kompletności zamiast sztywnej plakietki `partial` (AU-104).
 *
 * Jeden segment na każdą sekcję W ZAKRESIE analizy: pełny = sprawdzona, pusty = brak wiarygodnego wyniku. Sekcje
 * poza zakresem nie mają segmentu, ale są wymienione w szczegółach (nic nie jest po cichu pomijane). Komunikat
 * nagłówkowy jest tym samym tekstem co na stronie 1 raportu PDF. Status prawny POG, ręczna weryfikacja i
 * świeżość to osobne fakty — nie wpływają na pasek, lecz są pokazane obok.
 */
export function CompletenessBar({ completeness, status }: CompletenessBarProps) {
  const value = completenessOrNull(completeness);

  if (value === null) {
    return (
      <div className="completeness completeness-legacy" data-testid="completeness-missing">
        <span className="status-badge">{status}</span>
        <p className="result-preview">
          Odpowiedź nie niesie paska kompletności (zapis sprzed jego wprowadzenia) — status nagłówkowy
          nie wskazuje, których sekcji brakuje.
        </p>
      </div>
    );
  }

  const inScope = sectionsInScope(value);
  const missing = sectionsByState(value, "missing");
  const outOfScope = sectionsByState(value, "out_of_scope");

  return (
    <div
      className={`completeness completeness-${value.complete ? "complete" : "partial"}`}
      data-testid="completeness"
      data-complete={value.complete ? "true" : "false"}
      data-origin={value.origin}
    >
      <p className="completeness-headline" data-testid="completeness-headline">
        <strong>{value.headline}</strong>
      </p>
      <div
        className="completeness-bar"
        role="progressbar"
        aria-label="Kompletność analizy"
        aria-valuemin={0}
        aria-valuemax={value.total}
        aria-valuenow={value.checked}
        aria-valuetext={progressText(value)}
        data-testid="completeness-progress"
      >
        {inScope.map((item) => (
          <span
            key={item.section}
            className={`completeness-segment completeness-segment-${item.state}`}
            data-section={item.section}
            data-state={item.state}
            title={`${item.label}: ${COMPLETENESS_STATE_LABELS[item.state]}`}
          />
        ))}
      </div>
      <p className="completeness-counter" data-testid="completeness-counter">
        {value.checked} z {value.total} sekcji sprawdzono
        {missing.length > 0 && <> · brak: {missing.map((item) => item.short_label).join(", ")}</>}
        {outOfScope.length > 0 && <> · poza zakresem: {outOfScope.map((item) => item.short_label).join(", ")}</>}
      </p>
      {value.notes.map((note) => (
        <p key={note} className="result-preview" role="note" data-testid="completeness-note">
          {note}
        </p>
      ))}
      <details className="completeness-details">
        <summary>Sekcje analizy i podstawa ustalenia</summary>
        <ul className="completeness-list" data-testid="completeness-list">
          {value.sections.map((item) => (
            <li key={item.section} data-section={item.section} data-state={item.state}>
              <span className={`completeness-state quality-tone-${COMPLETENESS_STATE_TONES[item.state]}`}>
                <span aria-hidden="true">{COMPLETENESS_STATE_MARKS[item.state]} </span>
                {COMPLETENESS_STATE_LABELS[item.state]}
              </span>{" "}
              <strong>{item.label}</strong>
              {basisLabel(item.basis) && <> — {basisLabel(item.basis)}</>}
              <small className="completeness-source">
                {" "}
                (status źródła: {QUALITY_STATUS_LABELS[item.quality_status] ?? item.quality_status}
                {item.manual_review_required && ", wymaga weryfikacji"})
              </small>
            </li>
          ))}
        </ul>
        <p className="result-preview">
          Reguła <span className="mono">{value.rule_version}</span>: sekcja jest sprawdzona, gdy źródło zwróciło wynik zgodny z
          kontraktem; brak pokrycia, błąd i wynik częściowy to brak, a nie brak ograniczenia.
        </p>
      </details>
    </div>
  );
}
