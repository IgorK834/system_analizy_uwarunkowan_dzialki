import type {
  BdotContextStatus,
  BuildingRelation,
  NeighbourBuildingsResult,
  RoadContextResult,
  RoadRelation,
} from "@/lib/types";

/**
 * Prezentacja sekcji z lokalnego wydania BDOT10k: najbliższa droga (Task 23.2) i zabudowa sąsiednia (Task 23.3),
 * spójna z raportem PDF. Statusy są rozłączne — brak pokrycia, awaria zapytania i pusty ograniczony zasięg nigdy nie
 * są opisywane jako brak drogi ani brak budynków. Zdanie o braku potwierdzenia prawnego dostępu do drogi pochodzi z API
 * (`interpretation_note`) i nie może zostać pominięte.
 */
export const ROAD_ACCESS_DISCLAIMER =
  "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.";

export const BDOT_STATUS_LABELS: Record<BdotContextStatus, string> = {
  available: "obliczono z lokalnego wydania BDOT10k",
  no_match: "nic nie znaleziono w ograniczonym zasięgu",
  no_coverage: "brak pokrycia danymi BDOT10k",
  unavailable: "źródło niedostępne",
  unknown: "zapis sprzed tej sekcji — brak informacji",
};

const ROAD_STATUS_NOTES: Record<Exclude<BdotContextStatus, "available">, string> = {
  no_match:
    "W największym sprawdzonym promieniu nie znaleziono osi jezdni. Ograniczony zasięg wyszukiwania nie dowodzi braku drogi.",
  no_coverage:
    "Dane BDOT10k nie obejmują powiatu tej działki, więc drogi nie sprawdzono. Brak danych nie oznacza braku drogi.",
  unavailable:
    "Nie udało się wykonać zapytania o drogę. Odległość jest nieznana — wynik nie oznacza braku drogi.",
  unknown:
    "Zapisany wynik pochodzi sprzed sekcji drogowej. Brak informacji nie oznacza braku drogi.",
};

const BUILDINGS_STATUS_NOTES: Record<Exclude<BdotContextStatus, "available">, string> = {
  no_match:
    "Bufor wykracza poza zasięg zaimportowanego powiatu, a w pokrytej części nie znaleziono budynków. Nie wiadomo, czy budynków nie ma.",
  no_coverage:
    "Dane BDOT10k nie obejmują powiatu tej działki, więc zabudowy nie sprawdzono. Brak danych nie oznacza braku budynków.",
  unavailable:
    "Nie udało się policzyć zabudowy sąsiedniej. Statystyki są nieznane — wynik nie oznacza braku budynków.",
  unknown:
    "Zapisany wynik pochodzi sprzed sekcji zabudowy sąsiedniej. Brak informacji nie oznacza braku budynków.",
};

const REASON_LABELS: Record<string, string> = {
  SEARCH_RADIUS_EXHAUSTED: "największy sprawdzony promień bez drogi",
  SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE: "obszar wyszukiwania wykracza poza zaimportowany powiat",
  NO_ACTIVE_RELEASE: "nie zaimportowano żadnego wydania BDOT10k",
  OUTSIDE_COVERAGE: "działka poza zaimportowanymi powiatami",
  QUERY_TIMEOUT: "zapytanie przekroczyło limit czasu",
  DATABASE_ERROR: "błąd zapytania do lokalnych danych",
  SOURCE_NOT_RUNNABLE: "kontrakt źródła niepotwierdzony w katalogu",
  TOO_MANY_CANDIDATES: "zbyt wiele budynków w otoczeniu, by policzyć statystyki",
  FEATURE_DISABLED: "sekcja wyłączona w konfiguracji",
  UNEXPECTED_ERROR: "nieoczekiwany błąd",
  SECTION_NOT_RECORDED: "zapis sprzed tej sekcji",
};

export const ROAD_RELATION_LABELS: Record<RoadRelation, string> = {
  intersects: "oś jezdni przecina działkę",
  touches: "oś jezdni styka się z granicą działki",
  disjoint: "oś jezdni leży poza działką",
};

export const BUILDING_RELATION_LABELS: Record<BuildingRelation, string> = {
  on_parcel: "na działce",
  straddling: "zachodzi na działkę",
  adjacent: "przylega do działki",
  neighbour: "sąsiedni",
};

export function bdotStatusLabel(status: BdotContextStatus): string {
  return BDOT_STATUS_LABELS[status] ?? BDOT_STATUS_LABELS.unknown;
}

export function bdotReasonLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return REASON_LABELS[code] ?? code;
}

export function roadStatusNote(status: BdotContextStatus): string | null {
  return status === "available" ? null : (ROAD_STATUS_NOTES[status] ?? ROAD_STATUS_NOTES.unknown);
}

export function buildingsStatusNote(status: BdotContextStatus): string | null {
  return status === "available"
    ? null
    : (BUILDINGS_STATUS_NOTES[status] ?? BUILDINGS_STATUS_NOTES.unknown);
}

/** Wynik zastępczy dla odpowiedzi bez sekcji drogowej (zapis sprzed Task 23.2). */
export function roadContextOrUnknown(value: RoadContextResult | null | undefined): RoadContextResult {
  return (
    value ?? {
      schema_version: "1.0",
      status: "unknown",
      reason_code: "SECTION_NOT_RECORDED",
      distance_reference: "parcel_polygon_to_carriageway_axis",
      nearest: null,
      search_radius_m: null,
      searched_radii_m: [],
      max_search_radius_m: null,
      candidate_limit: null,
      search_area_within_coverage: null,
      intersecting_road_count: null,
      touching_road_count: null,
      release: null,
      interpretation_note: ROAD_ACCESS_DISCLAIMER,
      source: null,
      warnings: [],
    }
  );
}

/** Wynik zastępczy dla odpowiedzi bez sekcji zabudowy (zapis sprzed Task 23.3). */
export function buildingsOrUnknown(
  value: NeighbourBuildingsResult | null | undefined,
): NeighbourBuildingsResult {
  return (
    value ?? {
      schema_version: "1.0",
      status: "unknown",
      reason_code: "SECTION_NOT_RECORDED",
      ring_definition: "buffer(parcel, r) - parcel",
      distance_reference: "parcel_polygon_to_building_footprint",
      buffers_m: [],
      parcel_area_sqm: null,
      on_parcel: null,
      buffers: [],
      buildings: [],
      buildings_truncated: false,
      listed_radius_m: null,
      candidates_considered: null,
      search_area_within_coverage: null,
      release: null,
      interpretation_note:
        "Kontekst istniejącej zabudowy, a nie ocena możliwości zabudowy.",
      source: null,
      warnings: [],
    }
  );
}

export function formatNumber(value: number | null | undefined, maximumFractionDigits = 2): string {
  if (value == null) return "—";
  const normalized = Object.is(value, -0) ? 0 : value;
  return normalized.toLocaleString("pl-PL", { maximumFractionDigits });
}

export function formatMeters(value: number | null | undefined, digits = 2): string {
  return value == null ? "—" : `${formatNumber(value, digits)} m`;
}

export function formatSqm(value: number | null | undefined): string {
  return value == null ? "—" : `${formatNumber(value, 2)} m²`;
}

export function formatPct(value: number | null | undefined): string {
  return value == null ? "—" : `${formatNumber(value, 1)}%`;
}

/** Warstwy mapy: oś najbliższej drogi, odcinek działka → oś oraz obrysy budynków (GeoJSON w WGS84). */
export function roadMapLayers(road: RoadContextResult | null | undefined): {
  axes: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
} {
  const nearest = road?.nearest;
  return {
    axes: nearest?.road_geojson ? [nearest.road_geojson] : [],
    links: nearest?.link_geojson ? [nearest.link_geojson] : [],
  };
}

export function buildingMapFeatures(
  buildings: NeighbourBuildingsResult | null | undefined,
): Array<Record<string, unknown>> {
  return (buildings?.buildings ?? [])
    .map((item) => item.geometry_geojson)
    .filter((feature): feature is Record<string, unknown> => Boolean(feature));
}
