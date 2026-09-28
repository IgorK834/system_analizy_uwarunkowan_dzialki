import type { CompatibilityStatus } from "@/lib/types";

/**
 * Etykiety oceny relacji MPZP–POG (BK-205), spójne z raportem PDF.
 * Świadomie nie używają słów „zgodne/dopuszczalne”: ocena opisuje wynik jawnej
 * tabeli reguł, a nie prawną możliwość zabudowy działki.
 */
export const COMPATIBILITY_STATUS_LABELS: Record<CompatibilityStatus, string> = {
  compatible: "brak wskazanej rozbieżności w tabeli reguł",
  incompatible: "potencjalna rozbieżność funkcji — wymaga weryfikacji",
  uncertain: "nierozstrzygnięte — wymaga analizy ustaleń obu aktów",
  not_applicable: "nie dotyczy — brak aktu ustanawiającego obowiązek do porównania",
  unknown: "nieustalone — brak danych lub reguły",
};

export function compatibilityStatusLabel(status: CompatibilityStatus): string {
  return COMPATIBILITY_STATUS_LABELS[status] ?? COMPATIBILITY_STATUS_LABELS.unknown;
}

export function formatShare(
  areaSqm: number | null | undefined,
  pct: number | null | undefined,
): string {
  if (areaSqm == null || pct == null) return "nieustalone";
  return `${areaSqm.toLocaleString("pl-PL", { maximumFractionDigits: 1 })} m² (${pct.toFixed(1)}%)`;
}
