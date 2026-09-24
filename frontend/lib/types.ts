export type MapAnalyzeRequest = {
  method: "map";
  lon: number;
  lat: number;
};

export type AddressAnalyzeRequest = {
  method: "address";
  /** Etykieta wybranego adresu (kontekst/audyt) — nie jest ponownie geokodowana. */
  query: string;
  /** Długość geograficzna DOKŁADNIE wybranej sugestii (WGS84). */
  selected_lon: number;
  /** Szerokość geograficzna DOKŁADNIE wybranej sugestii (WGS84). */
  selected_lat: number;
  /** Opcjonalny, stabilny identyfikator wybranej sugestii. */
  selected_result_id?: string | null;
};

export type ParcelIdAnalyzeRequest = {
  method: "parcel_id";
  parcel_identifier: string;
};

export type AnalyzeRequest =
  | MapAnalyzeRequest
  | AddressAnalyzeRequest
  | ParcelIdAnalyzeRequest;

export type AnalyzeResumeRequest = {
  analysis_id: number;
  /** Symbol strefy MPZP odczytany przez użytkownika z mapy rastrowej, max 20 znaków. */
  zone_symbol: string;
};

export type SourceMetadata = {
  source_id?: string | null;
  source_version?: string | null;
  artifact_sha256?: string | null;
  data_release_id?: number | null;
  act_version?: string | null;
  source_name: string;
  source_url: string | null;
  fetched_at: string | null;
  response_status: number | null;
  confidence: number;
  manual_review_required: boolean;
};

export type PreviewSourceKey = "mpzp" | "pog" | "kiut";

/** Publiczny, bezpieczny kontrakt źródła podglądowego zwracany przez backend. */
export type PreviewSource = {
  source_key: PreviewSourceKey;
  label: string;
  attribution: string;
  min_zoom: number;
  max_zoom: number;
  tile_size: 256 | 512;
  tile_url_template: string;
  legal_note: string;
  info_url: string;
  catalog_status: string;
};

export type WarningMessage = {
  code: string;
  message: string;
  severity: "info" | "warning" | "error";
  source_name: string | null;
};

export type GeometryMetrics = {
  area_sqm: number;
  area_ha: number;
  perimeter_m: number;
  is_valid: boolean;
  geometry_repaired: boolean;
};

export type ParcelGeometryResponse = {
  parcel_identifier: string;
  geometry_geojson: Record<string, unknown>;
  metrics: GeometryMetrics;
  source: SourceMetadata;
  /**
   * Obszar zabudowy po technicznym odsunięciu od granicy działki, jako
   * GeoJSON Feature w WGS84. To techniczne przybliżenie
   * (is_technical_approximation=true we properties), nie ostateczna linia
   * zabudowy z MPZP i nie geometria netto po odjęciu stref ochronnych sieci.
   * null, gdy odsunięcie zredukowało obszar do zera.
   */
  buildable_area_geojson: Record<string, unknown> | null;
};

export type MpzpZoneResult = {
  zone_symbol: string;
  primary_use: string | null;
  supplementary_use: string | null;
  max_building_height_m: number | null;
  max_floors: number | null;
  min_biologically_active_pct: number | null;
  max_floor_area_ratio: number | null;
  min_floor_area_ratio: number | null;
  max_building_coverage_pct: number | null;
  intersection_area_sqm: number;
  intersection_pct: number;
  is_dominant: boolean;
  source: SourceMetadata;
};

export type PogResult = {
  schema_version: string;
  legal_status: string;
  coverage_status: string;
  act: {
    id: string;
    version: string | null;
    title: string | null;
    resolution_number: string | null;
    resolution_date: string | null;
  } | null;
  zones: PogZoneResult[];
  dominant_zone_id: string | null;
  ouz: PogAreaResult[];
  downtown_areas: PogAreaResult[];
  social_infrastructure_standard_areas: PogAreaResult[];
  status: string;
  planning_zone: string | null;
  /** Znormalizowany typ dominującej strefy POG. */
  zone_type: string | null;
  /** Czy działka ma istotne powierzchniowe przecięcie z OUZ. */
  in_ouz: boolean;
  /** Udział dominującej strefy POG w powierzchni działki, w skali 0-1. */
  area_ratio: number | null;
  /** Czy działka ma powierzchniowe przecięcie z obszarem śródmiejskim POG. */
  in_downtown_area: boolean;
  uchwala_nr: string | null;
  uchwala_date: string | null;
  /** Czy wynik POG wymaga ręcznej weryfikacji. */
  manual_review_required: boolean;
  /**
   * Jawny wynik tabeli zgodności MPZP-POG; null oznacza brak rozstrzygnięcia
   * (np. brak strefy dominującej MPZP albo brak wyniku POG).
   */
  conflict_with_mpzp: boolean | null;
  /** Surowe atrybuty APP/GML lub WMS zachowane dla audytu parsera. */
  raw_attributes: Record<string, unknown> | null;
  ouz_intersection_area_sqm: number | null;
  ouz_intersection_pct: number | null;
  touches_ouz_boundary: boolean;
  source: SourceMetadata | null;
};

export type PogProfileResult = {
  code: string;
  label: string | null;
  dictionary_source: string;
};

export type PogZoneResult = {
  id: string;
  symbol: string | null;
  type: string;
  label: string | null;
  area_sqm: number;
  area_pct: number;
  max_overground_floor_area_ratio: number | null;
  max_building_height_m: number | null;
  max_building_coverage_pct: number | null;
  min_biologically_active_pct: number | null;
  primary_profile: PogProfileResult[];
  additional_profiles: PogProfileResult[];
  source: SourceMetadata | null;
};

export type PogAreaResult = {
  id: string;
  symbol: string | null;
  label: string | null;
  area_sqm: number;
  area_pct: number;
  touches_boundary: boolean;
  source: SourceMetadata | null;
};

export type InfrastructureResult = {
  network_type: string;
  buffer_m: number;
  /** Powierzchnia technicznego obszaru zabudowy odjęta przez tę strefę ochronną. */
  zone_area_sqm: number;
  /** Źródło lub podstawa konfiguracji reguły bufora. */
  rule_source: string | null;
  /** Pewność technicznej reguły bufora w skali 0-1. */
  rule_confidence: number | null;
  /** Uwagi i ograniczenia zastosowanej reguły bufora. */
  rule_note: string | null;
  /** Czy strefa faktycznie pomniejszyła obszar zabudowy. */
  affects_buildable_area: boolean;
  /** Oryginalny przebieg sieci jako GeoJSON Feature w WGS84. */
  network_geometry_geojson: Record<string, unknown> | null;
  /** Efektywna strefa ochronna jako GeoJSON Feature w WGS84. */
  protection_zone_geojson: Record<string, unknown> | null;
  source: SourceMetadata;
};

export type UtilitiesPreviewResult = {
  coverage_status: "covered" | "not_covered" | "unknown";
  county_name: string | null;
  /** True oznacza potwierdzoną publikację GESUT dla powiatu, nie obecność sieci. */
  layer_available: boolean;
  /** Nota poglądowa; nigdy nie zawiera wyliczonych odległości ani liczby sieci. */
  note: string;
  source: SourceMetadata;
};

export type RiskResult = {
  risk_type: string;
  description: string;
  /** Przecięcie strefy ryzyka z działką jako GeoJSON Feature w WGS84. */
  geometry_geojson: Record<string, unknown> | null;
  source: SourceMetadata;
};

export type AnalyzeResponse = {
  analysis_id: number | null;
  status: string;
  analyzed_at: string;
  parcel: ParcelGeometryResponse | null;
  mpzp_zones: MpzpZoneResult[];
  pog: PogResult | null;
  infrastructure: InfrastructureResult[];
  utilities_preview: UtilitiesPreviewResult | null;
  risks: RiskResult[];
  buildable_area_sqm: number | null;
  manual_zone_required: boolean;
  warnings: WarningMessage[];
  sources: SourceMetadata[];
};

export type GeocodeSuggestion = {
  label: string;
  x: number;
  y: number;
  confidence: number;
  teryt: string;
};

export type GeocodeResponse = {
  query: string;
  suggestions: GeocodeSuggestion[];
  total_returned: number;
};

// --- Wyszukiwarka adresów /api/v1/search/addresses (Faza 11.1) ---

export type PointGeoJSON = {
  type: string;
  /** [lon, lat] w WGS84 (EPSG:4326). */
  coordinates: [number, number];
};

export type MatchRange = { start: number; end: number };

export type AddressPartsResponse = {
  country: string | null;
  voivodeship: string | null;
  county: string | null;
  municipality: string | null;
  city: string | null;
  street: string | null;
  house_number: string | null;
};

export type AddressSourceInfo = { source_id: string; attribution: string };

export type AddressSearchResult = {
  id: string;
  label: string;
  match_ranges: MatchRange[];
  point: PointGeoJSON;
  address_parts: AddressPartsResponse;
  result_type: string;
  confidence: number;
  source: AddressSourceInfo;
};

export type AddressSearchResponse = {
  query: string;
  results: AddressSearchResult[];
  total_returned: number;
};
