/**
 * Stan warstwy wektorowej POG (BK-406) i komunikaty inspektora (BK-404) dla stanu krajowego (ADR-026).
 *
 * `LayerState` (loading | available | partial | no_coverage | error | stale) jest wyznaczany wyłącznie z metadanych
 * przypiętego stanu krajowego (zasięgi aktów, kompletność agregatów BK-405, gotowość tabel mapy) oraz ze zdarzeń
 * źródeł MapLibre (`sourcedataloading`, `sourcedata`, `error`) — nigdy z liczby pikseli ani z tego, że pojedynczy
 * kafel jest pusty.
 *
 * Status prawny (`legal_status`) jest osobnym wymiarem: plakietka „projekt / dane niewiążące” zależy od aktów w
 * stanie, a nie od stanu warstwy, więc pozostaje widoczna także przy awarii kafli i danych nieaktualnych.
 *
 * Żaden komunikat nie mówi „brak planu”: lokalny stan zawiera tylko zaimportowane gminy i nie jest urzędowym
 * potwierdzeniem braku aktu — gmina poza zasięgiem może mieć plan w Rejestrze Urbanistycznym, który nie został
 * jeszcze zaimportowany (pokrycie krajowe: panel „Pokrycie importu”).
 */
import { formatPlDate } from "@/lib/pogStatus";
import { releaseStatusBadges } from "@/lib/pogZones";
import type {
  LayerState,
  PogCoverageArea,
  PogLegalStatus,
  PogPointQuery,
  PogStateStatus,
  PogTileLayerName,
  PogTileState,
} from "@/lib/types";

/** Aktywność źródeł kafli POG zebrana ze zdarzeń MapLibre (obowiązujące i projekty łącznie). */
export type PogTileActivity = {
  /** Żadne ze źródeł nie ma oczekujących żądań (`isSourceLoaded`). */
  sourceLoaded: boolean;
  /** Co najmniej jeden kafel wczytał się poprawnie od ostatniego ponowienia. */
  anyTileLoaded: boolean;
  /** Liczba kafli zakończonych błędem od ostatniego ponowienia. */
  tileErrors: number;
  /** Serwer odrzucił kafel limitem obiektów/bajtów (HTTP 413). */
  tileLimitHit: boolean;
};

export const INITIAL_TILE_ACTIVITY: PogTileActivity = {
  sourceLoaded: false,
  anyTileLoaded: false,
  tileErrors: 0,
  tileLimitHit: false,
};

/** [min_lon, min_lat, max_lon, max_lat] w EPSG:4326. */
export type LonLatBounds = [number, number, number, number];

export type PogLayerReason =
  | "state_loading"
  | "no_data"
  | "state_error"
  | "refresh_failed"
  | "state_not_current"
  | "tiles_failed"
  | "tile_errors"
  | "tile_limit"
  | "outside_coverage"
  | "tiles_loading"
  | "map_incomplete"
  | "incomplete_data"
  | "ok";

export type PogLayerStatus = {
  state: LayerState;
  reason: PogLayerReason;
  /** Przypięty stan krajowy (źródło URL-i kafli); `null`, gdy go nie ma. */
  registry: PogTileState | null;
  /** Chwila ostatniego udanego potwierdzenia stanu (dla `stale` — data danych). */
  checkedAt: string | null;
  tileErrors: number;
  /** Akty widoczne w oknie mapy z niepełnymi danymi (bez granicy, luka, nakładanie). */
  incompleteActs: string[];
  /** Liczba aktów, których zasięg przecina okno mapy (mianownik dla `incompleteActs`). */
  visibleActs: number;
  /** Plakietki statusów obecnych w stanie — niezależne od stanu warstwy. */
  badges: Array<{ status: PogLegalStatus; badge: string }>;
};

export function boundsIntersect(a: LonLatBounds, b: LonLatBounds): boolean {
  return a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
}

function containsPoint(bounds: LonLatBounds, lon: number, lat: number): boolean {
  return lon >= bounds[0] && lon <= bounds[2] && lat >= bounds[1] && lat <= bounds[3];
}

/** Zasięgi aktów stanu; stan bez `coverage_areas` ma jeden zasięg (obwiednię całości). */
function coverageAreas(state: PogTileState): Array<Pick<PogCoverageArea, "bounds"> & Partial<PogCoverageArea>> {
  if (state.coverage_areas && state.coverage_areas.length > 0) return state.coverage_areas;
  return state.bounds ? [{ bounds: state.bounds }] : [];
}

/** Czy punkt leży w zasięgu danych któregoś aktu stanu (metadane, nie kafel). */
export function pointInCoverage(state: PogTileState, lon: number, lat: number): boolean {
  const areas = coverageAreas(state);
  if (areas.length === 0) return true;
  return areas.some((area) => area.bounds !== null && containsPoint(area.bounds, lon, lat));
}

export function derivePogLayerStatus({
  state: stateStatus,
  tiles,
  viewport,
}: {
  state: PogStateStatus;
  tiles: PogTileActivity;
  viewport: LonLatBounds | null;
}): PogLayerStatus {
  const registry = stateStatus.state;
  const base = {
    registry,
    checkedAt:
      stateStatus.status === "available" || stateStatus.status === "stale"
        ? stateStatus.checkedAt
        : null,
    tileErrors: tiles.tileErrors,
    incompleteActs: [] as string[],
    visibleActs: 0,
    badges: registry ? releaseStatusBadges(registry.acts_by_legal_status) : [],
  };
  if (stateStatus.status === "loading") return { ...base, state: "loading", reason: "state_loading" };
  if (stateStatus.status === "no_data") return { ...base, state: "no_coverage", reason: "no_data" };
  if (stateStatus.status === "error" || !registry) {
    return { ...base, state: "error", reason: "state_error" };
  }
  if (stateStatus.status === "stale") return { ...base, state: "stale", reason: stateStatus.reason };
  if (!registry.is_current) return { ...base, state: "stale", reason: "state_not_current" };

  if (tiles.tileErrors > 0 && !tiles.anyTileLoaded && !tiles.tileLimitHit) {
    return { ...base, state: "error", reason: "tiles_failed" };
  }
  const areas = coverageAreas(registry);
  const visible = viewport
    ? areas.filter((area) => area.bounds !== null && boundsIntersect(area.bounds, viewport))
    : areas;
  if (viewport && areas.length > 0 && visible.length === 0) {
    return { ...base, state: "no_coverage", reason: "outside_coverage" };
  }
  if (tiles.tileLimitHit) return { ...base, state: "partial", reason: "tile_limit" };
  if (tiles.tileErrors > 0) return { ...base, state: "partial", reason: "tile_errors" };
  if (!tiles.sourceLoaded) return { ...base, state: "loading", reason: "tiles_loading" };
  if (!registry.map_ready) return { ...base, state: "partial", reason: "map_incomplete" };
  const visibleActs = visible.length;
  const incompleteActs = visible
    .filter((area) => area.is_complete === false || area.has_boundary === false)
    .map((area) => area.act_id ?? "")
    .filter(Boolean);
  if (incompleteActs.length > 0) {
    return { ...base, incompleteActs, visibleActs, state: "partial", reason: "incomplete_data" };
  }
  return { ...base, visibleActs, state: "available", reason: "ok" };
}

const DATE_TIME = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatCheckedAt(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : DATE_TIME.format(parsed);
}

const KIND_LABELS: Record<string, string> = {
  import: "import",
  withdrawal: "wycofanie aktu",
  rollback: "cofnięcie wersji",
  migration: "migracja",
};

/** Opis przypiętego stanu krajowego: numer, rodzaj zmiany, data i liczba aktów. */
export function stateDescription(state: PogTileState): string {
  const created = formatPlDate(state.created_at);
  const kind = KIND_LABELS[state.kind] ?? state.kind;
  return (
    `Stan krajowy #${state.state_id} (${kind}${created ? `, ${created}` : ""}), ` +
    `${state.act_count} ${state.act_count === 1 ? "akt" : "aktów"}`
  );
}

const MAX_LISTED_ACTS = 5;

function listActs(acts: string[]): string {
  const shown = acts.slice(0, MAX_LISTED_ACTS).join(", ");
  return acts.length > MAX_LISTED_ACTS ? `${shown} i ${acts.length - MAX_LISTED_ACTS} kolejnych` : shown;
}

export function pogLayerStatusMessage(status: PogLayerStatus): { title: string; detail: string } {
  const registry = status.registry;
  const label = registry ? `#${registry.state_id}` : "";
  switch (status.reason) {
    case "state_loading":
      return {
        title: "Ładowanie lokalnego stanu planów ogólnych…",
        detail: "Do czasu wczytania pusta mapa nie świadczy o braku danych.",
      };
    case "no_data":
      return {
        title: "Warstwa POG niedostępna: baza nie zawiera jeszcze zaimportowanych planów ogólnych.",
        detail:
          "Żadna gmina nie została zaimportowana z Rejestru Urbanistycznego. Nie oznacza to braku planu ogólnego w gminie.",
      };
    case "state_error":
      return {
        title: "Warstwa POG jest chwilowo niedostępna; mapa podstawowa nadal działa.",
        detail: "Nie udało się pobrać metadanych stanu. Pusta mapa nie oznacza braku planu.",
      };
    case "refresh_failed":
      return {
        title: "Dane nieaktualne — pokazano ostatnio wczytany stan.",
        detail:
          `Odświeżenie stanu nie powiodło się; mapa nadal pokazuje stan ${label} ` +
          `potwierdzony ${formatCheckedAt(status.checkedAt) ?? "wcześniej"}.`,
      };
    case "state_not_current":
      return {
        title: `Stan ${label} nie jest już bieżący.`,
        detail:
          "Mapa pozostaje przypięta do tego stanu; nowsze dane (import kolejnych gmin) wymagają jawnego odświeżenia.",
      };
    case "tiles_failed":
      return {
        title: "Awaria kafli POG — żaden kafel nie został wczytany.",
        detail: "Usługa kafli lub sieć są niedostępne. Pusta mapa nie oznacza braku planu.",
      };
    case "tile_errors":
      return {
        title: `Część kafli POG nie została wczytana (${status.tileErrors}).`,
        detail:
          "Puste fragmenty mapy mogą wynikać z awarii kafli, a nie z braku planu. Ponów wczytanie.",
      };
    case "tile_limit":
      return {
        title: "Kafel przekroczył limit obiektów — dane w widoku są niepełne.",
        detail: "Przybliż mapę, aby wczytać wszystkie obiekty POG.",
      };
    case "outside_coverage":
      return {
        title: `Widok poza zasięgiem zaimportowanych gmin (stan ${label}).`,
        detail:
          "To brak danych w lokalnym stanie, a nie urzędowe potwierdzenie braku planu — gmina może mieć plan ogólny " +
          "w Rejestrze Urbanistycznym, który nie został jeszcze zaimportowany (zob. „Pokrycie importu”).",
      };
    case "tiles_loading":
      return {
        title: `Wczytywanie kafli stanu ${label}…`,
        detail: "Do czasu wczytania pusta mapa nie świadczy o braku obiektów.",
      };
    case "map_incomplete":
      return {
        title: "Mapa niepełna: część aktów nie ma jeszcze przygotowanych danych do wyświetlania.",
        detail:
          `Dla ${registry?.versions_without_map_rows ?? "części"} wersji aktów trwa uzupełnianie tabel mapy ` +
          "(po migracji). Brakujące obiekty nie oznaczają braku planu.",
      };
    case "incomplete_data":
      return {
        title: "Dane stanu w widoku są niepełne.",
        detail:
          `${status.incompleteActs.length} z ${status.visibleActs} aktów w widoku ma brak granicy albo lukę/nakładanie ` +
          `stref (kontrola importu): ${listActs(status.incompleteActs)}.`,
      };
    default:
      return {
        title: "Warstwa POG dostępna.",
        detail: "Wszystkie kafle w widoku zostały wczytane z przypiętego stanu krajowego.",
      };
  }
}

/**
 * Najmniejszy zoom, od którego warstwa jest w kaflach (jedno źródło prawdy: serwer). Poniżej progu pusty wynik inspektora
 * nie mówi o braku obiektu, tylko o tym, że warstwa nie została jeszcze wczytana.
 */
export function layerMinZoom(
  registry: PogTileState | null,
  layers: readonly PogTileLayerName[],
): number | null {
  if (!registry?.layer_min_zoom) return null;
  const zooms = layers.map((layer) => registry.layer_min_zoom[layer]).filter((zoom): zoom is number => typeof zoom === "number");
  return zooms.length > 0 ? Math.min(...zooms) : null;
}

/** Czy przy tym zoomie kafle zawierają dane warstw (nieznany zoom lub próg — zakładamy, że tak). */
export function layersLoadedAtZoom(
  registry: PogTileState | null,
  layers: readonly PogTileLayerName[],
  zoom: number | null | undefined,
): boolean {
  const minimum = layerMinZoom(registry, layers);
  return minimum === null || zoom === null || zoom === undefined || zoom >= minimum;
}

export type InspectorEmptyKind =
  | "loading"
  | "unavailable"
  | "no_coverage"
  | "partial"
  | "filtered"
  | "no_object";

/**
 * Komunikat inspektora, gdy w punkcie nie wyrenderowano żadnej cechy POG.
 * Brak cech jest interpretowany w kontekście stanu warstwy i pokrycia —
 * „brak obiektu” pada tylko wtedy, gdy warstwa jest wczytana, a punkt leży w
 * zasięgu zaimportowanych danych.
 */
export function inspectorEmptyMessage(
  status: PogLayerStatus,
  query: Pick<PogPointQuery, "lon" | "lat" | "queried">,
  statusFilterLabel: string | null,
): { kind: InspectorEmptyKind; text: string } {
  const registry = status.registry;
  if (status.reason === "state_loading") {
    return {
      kind: "loading",
      text: "Warstwa POG jeszcze się ładuje — nie można teraz sprawdzić obiektów w tym punkcie.",
    };
  }
  if (!registry || status.reason === "tiles_failed" || !query.queried) {
    return {
      kind: "unavailable",
      text:
        "Warstwa POG jest niedostępna, więc nie można stwierdzić, czy w tym punkcie jest obiekt planu. " +
        "Nie oznacza to braku planu.",
    };
  }
  if (!pointInCoverage(registry, query.lon, query.lat)) {
    return {
      kind: "no_coverage",
      text:
        `Punkt leży poza zasięgiem zaimportowanych gmin (stan #${registry.state_id}). ` +
        "To brak danych w lokalnym stanie, a nie potwierdzenie braku planu — gmina może mieć plan ogólny w " +
        "Rejestrze Urbanistycznym, który nie został jeszcze zaimportowany.",
    };
  }
  if (status.reason === "tiles_loading") {
    return {
      kind: "loading",
      text: "Kafle w tym miejscu jeszcze się wczytują — kliknij ponownie za chwilę.",
    };
  }
  if (status.reason === "tile_errors" || status.reason === "tile_limit") {
    return {
      kind: "partial",
      text:
        "Nie znaleziono obiektu we wczytanych kaflach, ale część kafli ma błąd — wynik jest niepewny. " +
        "Ponów wczytanie warstwy.",
    };
  }
  if (statusFilterLabel) {
    return {
      kind: "filtered",
      text:
        `Brak obiektu przy aktywnym filtrze „${statusFilterLabel}” — obiekty o innym statusie ` +
        "prawnym są ukryte. Przełącz edycję na „Wszystkie akty”.",
    };
  }
  return {
    kind: "no_object",
    text:
      `Brak obiektu POG w tym punkcie w stanie #${registry.state_id} (warstwa wczytana, punkt w zasięgu danych). ` +
      "To nie jest urzędowe potwierdzenie braku planu.",
  };
}
