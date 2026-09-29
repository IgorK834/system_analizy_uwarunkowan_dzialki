/**
 * Wspólny słownik stanu warstwy mapy (BK-406). Stan warstwy opisuje dostępność
 * i kompletność danych na mapie — jest niezależny od statusu prawnego aktu
 * (`legal_status`, BK-106) i nigdy nie jest wnioskowany z liczby pikseli lub
 * cech w pojedynczym kaflu.
 */
import type { LayerState } from "@/lib/types";

export const LAYER_STATES: readonly LayerState[] = [
  "loading",
  "available",
  "partial",
  "no_coverage",
  "error",
  "stale",
];

export const LAYER_STATE_LABELS: Record<LayerState, string> = {
  loading: "ładowanie",
  available: "dostępna",
  partial: "dane niepełne",
  no_coverage: "brak pokrycia danymi",
  error: "awaria warstwy",
  stale: "dane nieaktualne",
};

/** Opisy do legendy stanów — żaden nie utożsamia braku danych z brakiem planu. */
export const LAYER_STATE_DESCRIPTIONS: Record<LayerState, string> = {
  loading: "Trwa pobieranie metadanych albo kafli; mapa może być chwilowo pusta.",
  available: "Wszystkie potrzebne dane warstwy zostały wczytane.",
  partial:
    "Część danych nie została wczytana albo źródło zgłasza niepełność — puste miejsca mogą wynikać z braku danych.",
  no_coverage:
    "Lokalne wydanie nie obejmuje tego obszaru. To brak danych, a nie urzędowe potwierdzenie braku aktu.",
  error: "Warstwa nie mogła zostać wczytana (awaria usługi lub sieci); mapa podstawowa działa.",
  stale:
    "Pokazano ostatnie poprawnie wczytane dane z podaną datą — nowszych nie udało się potwierdzić.",
};
