import type {
  FreshnessState,
  SectionQuality,
  SectionQualityKey,
  SectionQualityMatrix,
  SectionQualityStatus,
} from "@/lib/types";

/**
 * Prezentacja macierzy kompletności i świeżości sekcji (BK-504), spójna z API i
 * raportem PDF. Legenda i etykiety pochodzą z odpowiedzi API (`legend`); stałe
 * poniżej są tylko zapasem dla odpowiedzi bez legendy.
 *
 * Status (kontrakt źródła) i świeżość (reguła wieku źródła) są rozłączne:
 * stary pomiar nie jest błędem, brak pokrycia nie jest błędem źródła, a brak
 * reguły wieku daje „nieustalone”, nigdy domniemany termin ważności.
 */
export const QUALITY_SECTION_LABELS: Record<SectionQualityKey, string> = {
  parcel: "Działka i geometria",
  mpzp: "MPZP",
  pog: "POG — strefy planistyczne",
  pog_overlays: "POG — OUZ, OZS, OSDIS",
  flood: "Zagrożenie powodziowe (ISOK)",
  nature: "Formy ochrony przyrody (GDOŚ)",
  terrain: "Teren (NMT)",
  utilities: "Uzbrojenie terenu (KIUT)",
  transport: "Transport i dostęp do drogi",
  mpzp_pog_relation: "Relacja MPZP–POG",
};

export const QUALITY_STATUS_LABELS: Record<SectionQualityStatus, string> = {
  available: "sprawdzono",
  partial: "częściowo",
  no_coverage: "brak pokrycia źródła",
  unavailable: "źródło niedostępne",
  error: "błąd sprawdzenia",
  unknown: "nieustalone",
  out_of_scope: "poza zakresem",
  awaiting_input: "oczekuje na dane użytkownika",
};

export const FRESHNESS_LABELS: Record<FreshnessState, string> = {
  fresh: "aktualne wg reguły",
  stale: "starsze niż reguła",
  unknown: "świeżość nieustalona",
};

const SECONDS_PER_DAY = 86_400;

/** Ton wizualny statusu; kolor nigdy nie jest jedynym nośnikiem znaczenia (tekst + znak). */
export type QualityTone = "ok" | "warn" | "info" | "bad" | "muted";

export const QUALITY_STATUS_TONES: Record<SectionQualityStatus, QualityTone> = {
  available: "ok",
  partial: "warn",
  no_coverage: "info",
  unavailable: "bad",
  error: "bad",
  unknown: "muted",
  out_of_scope: "muted",
  awaiting_input: "warn",
};

export const QUALITY_STATUS_MARKS: Record<SectionQualityStatus, string> = {
  available: "✓",
  partial: "◐",
  no_coverage: "∅",
  unavailable: "✕",
  error: "!",
  unknown: "?",
  out_of_scope: "–",
  awaiting_input: "…",
};

export function sectionLabel(key: SectionQualityKey): string {
  return QUALITY_SECTION_LABELS[key] ?? key;
}

export function statusLabel(matrix: SectionQualityMatrix, status: SectionQualityStatus): string {
  return (
    matrix.legend?.statuses.find((item) => item.id === status)?.label ??
    QUALITY_STATUS_LABELS[status] ??
    status
  );
}

export function freshnessLabel(matrix: SectionQualityMatrix, state: FreshnessState): string {
  return (
    matrix.legend?.freshness.find((item) => item.id === state)?.label ??
    FRESHNESS_LABELS[state] ??
    state
  );
}

/** Etykieta kodu przyczyny z legendy API; nieznany kod jest pokazany dosłownie. */
export function reasonLabel(matrix: SectionQualityMatrix, code: string): string {
  return matrix.legend?.reasons.find((item) => item.code === code)?.label ?? `kod: ${code}`;
}

export function formatAge(seconds: number | null): string {
  if (seconds == null) return "brak danych";
  const days = Math.floor(seconds / SECONDS_PER_DAY);
  if (days === 0) return "mniej niż 1 dzień";
  if (days === 1) return "1 dzień";
  return `${days} dni`;
}

export function formatFetchedAt(value: string | null): string {
  if (!value) return "brak danych";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "czas nieprawidłowy";
  return date.toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }) + " UTC";
}

export function freshnessDetail(item: SectionQuality): string {
  const parts = [`wiek: ${formatAge(item.freshness.age_seconds)}`];
  parts.push(
    item.freshness.max_age_days != null
      ? `reguła źródła: ${item.freshness.max_age_days} dni`
      : "brak reguły wieku",
  );
  return parts.join("; ");
}

/** Sekcje starsze niż reguła własnego źródła w chwili `now` (ostrzeżenie dodatkowe). */
export type ExportAgeWarning = { section: SectionQualityKey; ageSeconds: number; maxAgeDays: number };

/**
 * Ostrzeżenie o wieku danych „na dziś” — osobne od zapisanej oceny. Korzysta
 * wyłącznie z reguły zapisanej z oceną i nie zmienia ani statusu, ani hasha macierzy.
 */
export function ageWarnings(matrix: SectionQualityMatrix, now: Date): ExportAgeWarning[] {
  const warnings: ExportAgeWarning[] = [];
  for (const item of matrix.sections) {
    const maxDays = item.freshness.max_age_days;
    if (maxDays == null || !item.fetched_at) continue;
    const fetched = new Date(item.fetched_at).getTime();
    if (Number.isNaN(fetched)) continue;
    const ageSeconds = Math.floor((now.getTime() - fetched) / 1000);
    if (ageSeconds > maxDays * SECONDS_PER_DAY) {
      warnings.push({ section: item.section, ageSeconds, maxAgeDays: maxDays });
    }
  }
  return warnings;
}

export function matrixOrNull(matrix: SectionQualityMatrix | null | undefined): SectionQualityMatrix | null {
  return matrix && Array.isArray(matrix.sections) && matrix.sections.length > 0 ? matrix : null;
}
