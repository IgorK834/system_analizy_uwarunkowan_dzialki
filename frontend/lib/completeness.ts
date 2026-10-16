import type { Completeness, CompletenessSection, CompletenessState } from "@/lib/types";

/**
 * Prezentacja kompletności analizy (AU-104, ADR-022). Stany, liczby i komunikat pochodzą z backendu — ten sam
 * tekst jest na stronie 1 raportu PDF. Kolor nigdy nie jest jedynym nośnikiem znaczenia (znak + tekst).
 */
export const COMPLETENESS_STATE_LABELS: Record<CompletenessState, string> = {
  checked: "sprawdzono",
  missing: "brak",
  out_of_scope: "poza zakresem",
};

export const COMPLETENESS_STATE_MARKS: Record<CompletenessState, string> = {
  checked: "✓",
  missing: "✕",
  out_of_scope: "–",
};

export type CompletenessTone = "ok" | "warn" | "muted";

export const COMPLETENESS_STATE_TONES: Record<CompletenessState, CompletenessTone> = {
  checked: "ok",
  missing: "warn",
  out_of_scope: "muted",
};

export const MPZP_BASIS_LABELS: Record<string, string> = {
  vector_intersection: "przecięcie z lokalnym wektorem stref",
  document_candidate: "akt i dokument, bez lokalnego wektora",
  no_match: "kanały odpowiedziały i nie wskazały planu",
};

/** Odpowiedź z kompletnością albo `null` dla zapisu sprzed AU-104 (wtedy UI pokazuje sam status). */
export function completenessOrNull(value: Completeness | null | undefined): Completeness | null {
  return value && Array.isArray(value.sections) && value.sections.length > 0 ? value : null;
}

/** Sekcje w zakresie analizy (bez `out_of_scope`) — to one tworzą segmenty paska i mianownik. */
export function sectionsInScope(value: Completeness): CompletenessSection[] {
  return value.sections.filter((item) => item.state !== "out_of_scope");
}

export function sectionsByState(value: Completeness, state: CompletenessState): CompletenessSection[] {
  return value.sections.filter((item) => item.state === state);
}

export function basisLabel(basis: string | null): string | null {
  if (!basis) return null;
  return MPZP_BASIS_LABELS[basis] ?? basis;
}

/** Tekst dla czytnika ekranu: liczby i ewentualny brak, bez polegania na kolorze segmentów. */
export function progressText(value: Completeness): string {
  return value.complete
    ? `Sprawdzono wszystkie ${value.total} sekcji w zakresie analizy`
    : `Sprawdzono ${value.checked} z ${value.total} sekcji w zakresie analizy`;
}
