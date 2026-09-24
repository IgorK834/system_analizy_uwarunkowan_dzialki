/**
 * Prezentacja statusu prawnego, pokrycia danymi i aktualności źródła POG
 * (BK-106). Lustro `backend/app/shared/planning_status.py`: te same wartości i
 * te same zasady języka — dla statusów niewiążących żaden tekst nie używa
 * słowa „obowiązuje”, a brak geometrii nigdy nie jest opisany jako brak planu.
 */
import type {
  PogCoverageStatus,
  PogDataAvailability,
  PogLegalStatus,
  PogResult,
} from "@/lib/types";

export const POG_LEGAL_STATUS_LABELS: Record<PogLegalStatus, string> = {
  binding: "obowiązuje (potwierdzone urzędowym kodem statusu)",
  project: "projekt aktu — niewiążący",
  in_progress: "w trakcie sporządzania — niewiążący",
  superseded: "nieaktualny (zastąpiony lub uchylony)",
  unknown: "status prawny nieustalony",
};

export const POG_COVERAGE_STATUS_LABELS: Record<PogCoverageStatus, string> = {
  available: "dane przestrzenne dostępne dla działki",
  partial: "dane przestrzenne niepełne",
  act_without_spatial_data: "akt bez danych przestrzennych dla działki",
  no_act_confirmed: "urzędowo potwierdzony brak aktu",
  unknown: "zakres danych nieustalony",
};

export const POG_DATA_AVAILABILITY_LABELS: Record<PogDataAvailability, string> = {
  current: "sprawdzone w źródle przy tej analizie",
  stale: "ostatnia potwierdzona wartość — źródło było niedostępne",
  unavailable: "źródło niedostępne",
};

export const NO_GEOMETRY_IS_NOT_NO_PLAN =
  "Brak geometrii lub pusta odpowiedź usługi nie oznacza braku planu.";

/** Krótka etykieta statusu prawnego do kompaktowych miejsc interfejsu. */
export const POG_LEGAL_STATUS_SHORT: Record<PogLegalStatus, string> = {
  binding: "obowiązuje",
  project: "projekt (niewiążący)",
  in_progress: "w trakcie sporządzania (niewiążący)",
  superseded: "nieaktualny",
  unknown: "nieustalony",
};

export function legalStatusLabel(value: string | null | undefined): string {
  return value && value in POG_LEGAL_STATUS_LABELS
    ? POG_LEGAL_STATUS_LABELS[value as PogLegalStatus]
    : POG_LEGAL_STATUS_LABELS.unknown;
}

export function legalStatusShort(value: string | null | undefined): string {
  return value && value in POG_LEGAL_STATUS_SHORT
    ? POG_LEGAL_STATUS_SHORT[value as PogLegalStatus]
    : POG_LEGAL_STATUS_SHORT.unknown;
}

export function coverageStatusLabel(value: string | null | undefined): string {
  return value && value in POG_COVERAGE_STATUS_LABELS
    ? POG_COVERAGE_STATUS_LABELS[value as PogCoverageStatus]
    : POG_COVERAGE_STATUS_LABELS.unknown;
}

export function dataAvailabilityLabel(value: string | null | undefined): string {
  return value && value in POG_DATA_AVAILABILITY_LABELS
    ? POG_DATA_AVAILABILITY_LABELS[value as PogDataAvailability]
    : POG_DATA_AVAILABILITY_LABELS.unavailable;
}

/** Data w formacie dd.mm.rrrr (UTC) — ten sam co `%d.%m.%Y` w raporcie PDF. */
export function formatPlDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString("pl-PL", {
        timeZone: "UTC",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      });
}

/** Uwagi prezentacyjne; kolejność i treść zgodne z `pog_status_notes_pl`. */
export function pogStatusNotes(
  pog: Pick<
    PogResult,
    "legal_status" | "coverage_status" | "data_availability" | "status_confirmed_at"
  >,
): string[] {
  const notes: string[] = [];
  switch (pog.legal_status) {
    case "project":
      notes.push("Projekt aktu nie jest wiążący i nie może być traktowany jak prawo miejscowe.");
      break;
    case "in_progress":
      notes.push("Procedura sporządzania aktu trwa; ustalenia nie są wiążące.");
      break;
    case "superseded":
      notes.push("Akt jest nieaktualny — sprawdź akt, który go zastąpił.");
      break;
    case "unknown":
      notes.push("Nie potwierdzono statusu prawnego aktu w źródle urzędowym.");
      break;
    default:
      break;
  }
  if (["act_without_spatial_data", "unknown", "partial"].includes(pog.coverage_status)) {
    notes.push(NO_GEOMETRY_IS_NOT_NO_PLAN);
  }
  if (pog.coverage_status === "no_act_confirmed") {
    notes.push("Brak aktu potwierdzono urzędowo — zobacz wskazane potwierdzenie.");
  }
  if (pog.data_availability === "stale") {
    notes.push(
      `Źródło było niedostępne; pokazano ostatnią potwierdzoną wartość z dnia ${
        formatPlDate(pog.status_confirmed_at) ?? "nieznana data"
      }.`,
    );
  } else if (pog.data_availability === "unavailable") {
    notes.push("Źródło było niedostępne; brak wyniku nie oznacza braku ograniczeń.");
  }
  return notes;
}
