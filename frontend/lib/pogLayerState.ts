/**
 * Stan warstwy wektorowej POG (BK-406) i komunikaty inspektora (BK-404).
 *
 * `LayerState` (loading | available | partial | no_coverage | error | stale) jest
 * wyznaczany wyłącznie z metadanych przypiętego wydania (zasięgi aktów,
 * kompletność agregatów BK-405, aktywność wydania) oraz ze zdarzeń źródła
 * MapLibre (`sourcedataloading`, `sourcedata`, `error`) — nigdy z liczby
 * pikseli ani z tego, że pojedynczy kafel jest pusty.
 *
 * Status prawny (`legal_status`) jest osobnym wymiarem: plakietka „projekt /
 * dane niewiążące” zależy od aktów w wydaniu, a nie od stanu warstwy, więc
 * pozostaje widoczna także przy awarii kafli i danych nieaktualnych.
 *
 * Żaden komunikat nie mówi „brak planu”: lokalne wydanie nie zawiera
 * urzędowego potwierdzenia braku aktu (to wymaga `no_act_confirmed`, BK-106).
 */
import { formatPlDate } from "@/lib/pogStatus";
import { releaseStatusBadges } from "@/lib/pogZones";
import type {
  LayerState,
  PogCoverageArea,
  PogLegalStatus,
  PogPointQuery,
  PogReleaseState,
  PogTileRelease,
} from "@/lib/types";

/** Aktywność źródła kafli POG zebrana ze zdarzeń MapLibre. */
export type PogTileActivity = {
  /** Źródło nie ma oczekujących żądań (`isSourceLoaded`). */
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
  | "release_loading"
  | "no_release"
  | "release_error"
  | "refresh_failed"
  | "release_not_active"
  | "tiles_failed"
  | "tile_errors"
  | "tile_limit"
  | "outside_coverage"
  | "tiles_loading"
  | "incomplete_data"
  | "ok";

export type PogLayerStatus = {
  state: LayerState;
  reason: PogLayerReason;
  release: PogTileRelease | null;
  /** Chwila ostatniego udanego potwierdzenia wydania (dla `stale` — data danych). */
  checkedAt: string | null;
  tileErrors: number;
  /** Akty widoczne w oknie mapy z niepełnymi danymi (bez granicy, luka, nakładanie). */
  incompleteActs: string[];
  /** Plakietki statusów obecnych w wydaniu — niezależne od stanu warstwy. */
  badges: Array<{ status: PogLegalStatus; badge: string }>;
};

export function boundsIntersect(a: LonLatBounds, b: LonLatBounds): boolean {
  return a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
}

function containsPoint(bounds: LonLatBounds, lon: number, lat: number): boolean {
  return lon >= bounds[0] && lon <= bounds[2] && lat >= bounds[1] && lat <= bounds[3];
}

/** Zasięgi aktów wydania; starsze wydania bez `coverage_areas` mają jeden zasięg. */
function coverageAreas(release: PogTileRelease): Array<Pick<PogCoverageArea, "bounds"> & Partial<PogCoverageArea>> {
  if (release.coverage_areas && release.coverage_areas.length > 0) return release.coverage_areas;
  return release.bounds ? [{ bounds: release.bounds }] : [];
}

/** Czy punkt leży w zasięgu danych któregoś aktu wydania (metadane, nie kafel). */
export function pointInCoverage(release: PogTileRelease, lon: number, lat: number): boolean {
  const areas = coverageAreas(release);
  if (areas.length === 0) return true;
  return areas.some((area) => area.bounds !== null && containsPoint(area.bounds, lon, lat));
}

export function derivePogLayerStatus({
  release: releaseState,
  tiles,
  viewport,
}: {
  release: PogReleaseState;
  tiles: PogTileActivity;
  viewport: LonLatBounds | null;
}): PogLayerStatus {
  const release = releaseState.release;
  const base = {
    release,
    checkedAt:
      releaseState.status === "available" || releaseState.status === "stale"
        ? releaseState.checkedAt
        : null,
    tileErrors: tiles.tileErrors,
    incompleteActs: [] as string[],
    badges: release ? releaseStatusBadges(release.acts_by_legal_status) : [],
  };
  if (releaseState.status === "loading") return { ...base, state: "loading", reason: "release_loading" };
  if (releaseState.status === "no_release") return { ...base, state: "no_coverage", reason: "no_release" };
  if (releaseState.status === "error" || !release) {
    return { ...base, state: "error", reason: "release_error" };
  }
  if (releaseState.status === "stale") return { ...base, state: "stale", reason: releaseState.reason };
  if (!release.is_active) return { ...base, state: "stale", reason: "release_not_active" };

  if (tiles.tileErrors > 0 && !tiles.anyTileLoaded && !tiles.tileLimitHit) {
    return { ...base, state: "error", reason: "tiles_failed" };
  }
  const areas = coverageAreas(release);
  const visible = viewport
    ? areas.filter((area) => area.bounds !== null && boundsIntersect(area.bounds, viewport))
    : areas;
  if (viewport && areas.length > 0 && visible.length === 0) {
    return { ...base, state: "no_coverage", reason: "outside_coverage" };
  }
  if (tiles.tileLimitHit) return { ...base, state: "partial", reason: "tile_limit" };
  if (tiles.tileErrors > 0) return { ...base, state: "partial", reason: "tile_errors" };
  if (!tiles.sourceLoaded) return { ...base, state: "loading", reason: "tiles_loading" };
  const incompleteActs = visible
    .filter((area) => area.is_complete === false || area.has_boundary === false)
    .map((area) => area.act_id ?? "")
    .filter(Boolean);
  if (incompleteActs.length > 0) {
    return { ...base, incompleteActs, state: "partial", reason: "incomplete_data" };
  }
  return { ...base, state: "available", reason: "ok" };
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

/** Opis przypiętego wydania: etykieta, identyfikator i data publikacji danych. */
export function releaseDescription(release: PogTileRelease): string {
  const published = formatPlDate(release.published_at);
  return (
    `Wydanie ${release.version_label} (#${release.release_id})` +
    (published ? ` z dnia ${published}` : "")
  );
}

export function pogLayerStatusMessage(status: PogLayerStatus): { title: string; detail: string } {
  const release = status.release;
  const label = release ? `#${release.release_id}` : "";
  switch (status.reason) {
    case "release_loading":
      return {
        title: "Ładowanie lokalnego wydania planu ogólnego…",
        detail: "Do czasu wczytania pusta mapa nie świadczy o braku danych.",
      };
    case "no_release":
      return {
        title: "Warstwa POG niedostępna: brak lokalnego wydania danych.",
        detail:
          "Baza nie zawiera wydania planu ogólnego. Nie oznacza to braku planu ogólnego w gminie.",
      };
    case "release_error":
      return {
        title: "Warstwa POG jest chwilowo niedostępna; mapa podstawowa nadal działa.",
        detail: "Nie udało się pobrać metadanych wydania. Pusta mapa nie oznacza braku planu.",
      };
    case "refresh_failed":
      return {
        title: "Dane nieaktualne — pokazano ostatnie wczytane wydanie.",
        detail:
          `Odświeżenie wydania nie powiodło się; mapa nadal pokazuje wydanie ${label} ` +
          `potwierdzone ${formatCheckedAt(status.checkedAt) ?? "wcześniej"}.`,
      };
    case "release_not_active":
      return {
        title: `Wydanie ${label} nie jest już aktywne.`,
        detail:
          "Mapa pozostaje przypięta do tego wydania; nowsze dane wymagają jawnego odświeżenia.",
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
        title: `Widok poza zasięgiem danych wydania ${label}.`,
        detail:
          "To brak danych w lokalnym wydaniu, a nie urzędowe potwierdzenie braku planu.",
      };
    case "tiles_loading":
      return {
        title: `Wczytywanie kafli wydania ${label}…`,
        detail: "Do czasu wczytania pusta mapa nie świadczy o braku obiektów.",
      };
    case "incomplete_data":
      return {
        title: "Dane wydania w widoku są niepełne.",
        detail:
          `Akty z brakiem granicy albo luką/nakładaniem stref (kontrola importu): ` +
          `${status.incompleteActs.join(", ")}.`,
      };
    default:
      return {
        title: "Warstwa POG dostępna.",
        detail: "Wszystkie kafle w widoku zostały wczytane z przypiętego wydania.",
      };
  }
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
 * zasięgu danych wydania.
 */
export function inspectorEmptyMessage(
  status: PogLayerStatus,
  query: Pick<PogPointQuery, "lon" | "lat" | "queried">,
  statusFilterLabel: string | null,
): { kind: InspectorEmptyKind; text: string } {
  const release = status.release;
  if (status.reason === "release_loading") {
    return {
      kind: "loading",
      text: "Warstwa POG jeszcze się ładuje — nie można teraz sprawdzić obiektów w tym punkcie.",
    };
  }
  if (!release || status.reason === "tiles_failed" || !query.queried) {
    return {
      kind: "unavailable",
      text:
        "Warstwa POG jest niedostępna, więc nie można stwierdzić, czy w tym punkcie jest obiekt planu. " +
        "Nie oznacza to braku planu.",
    };
  }
  if (!pointInCoverage(release, query.lon, query.lat)) {
    return {
      kind: "no_coverage",
      text:
        `Punkt leży poza zasięgiem danych wydania #${release.release_id}. ` +
        "To brak danych w lokalnym wydaniu, a nie potwierdzenie braku planu.",
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
      `Brak obiektu POG w tym punkcie w wydaniu #${release.release_id} (warstwa wczytana, punkt w zasięgu danych). ` +
      "To nie jest urzędowe potwierdzenie braku planu.",
  };
}
