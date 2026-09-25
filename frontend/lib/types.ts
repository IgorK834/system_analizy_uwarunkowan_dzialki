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

export type MpzpAssignmentMethod =
  | "vector_intersection"
  | "document_candidate"
  | "manual_user_input"
  | "legacy";

/** Kandydatura parametru uchwały z cytowalnym dowodem (BK-203). */
export type MpzpParameterEvidence = {
  name: string;
  normalized_value: number | string | null;
  raw_value: string | null;
  unit: string | null;
  evidence_text: string | null;
  page_number: number | null;
  segment_id: string | null;
  legal_unit_id: number | null;
  document_sha256: string | null;
  document_version_id: number | null;
  parser_version: string | null;
  extraction_method: string | null;
  confidence: number;
  /** Wspólne ID sprzecznych kandydatur; brak automatycznego wyboru. */
  conflict_group_id: string | null;
  manual_review_required: boolean;
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
  /** Pomocniczo: strefa o największym udziale; nie zastępuje pełnej listy. */
  is_dominant: boolean;
  source: SourceMetadata;
  /** Stabilne ID wydzielenia z wersjonowanego wektora (BK-202). */
  zone_id?: string | null;
  act_identifier?: string | null;
  act_version?: string | null;
  act_version_id?: number | null;
  data_release_id?: number | null;
  document_url?: string | null;
  /** Wydzielenie tylko styka się z działką (pole ≤ 1e-6 m²). */
  touches_boundary?: boolean;
  assignment_method?: MpzpAssignmentMethod;
  intersection_geojson?: Record<string, unknown> | null;
  parameters?: MpzpParameterEvidence[];
  manual_review_required?: boolean;
};

/** Kanoniczny status prawny aktu (BK-106); pochodzi wyłącznie z urzędowego kodu. */
export type PogLegalStatus =
  | "binding"
  | "project"
  | "in_progress"
  | "superseded"
  | "unknown";

/** Pokrycie działki danymi przestrzennymi; brak geometrii ≠ brak aktu. */
export type PogCoverageStatus =
  | "available"
  | "partial"
  | "act_without_spatial_data"
  | "no_act_confirmed"
  | "unknown";

/** Stan operacyjny źródła w chwili analizy; nie zastępuje statusu prawnego. */
export type PogDataAvailability = "current" | "stale" | "unavailable";

export type PogStatusEvidence = {
  source_name: string;
  official: boolean;
  reference: string | null;
  source_id: string | null;
  raw_value: string | null;
  confirmed_at: string | null;
};

export type PogResult = {
  schema_version: string;
  legal_status: PogLegalStatus;
  coverage_status: PogCoverageStatus;
  data_availability: PogDataAvailability;
  /** Chwila potwierdzenia statusu; dla `stale` — data ostatniego potwierdzenia. */
  status_confirmed_at: string | null;
  legal_status_evidence: PogStatusEvidence | null;
  /** Wymagane dla `no_act_confirmed`: wskazanie urzędowego potwierdzenia. */
  coverage_evidence: PogStatusEvidence | null;
  act: PogActResult | null;
  zones: PogZoneResult[];
  dominant_zone_id: string | null;
  ouz: PogAreaResult[];
  downtown_areas: PogAreaResult[];
  social_infrastructure_standard_areas: PogAreaResult[];
  /** Przestarzałe lustro `legal_status` zachowane dla klientów POG v1. */
  status: PogLegalStatus;
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

/** Rekord metadanych CSW RU zamrożony w snapshotcie wyniku (BK-107). */
export type CatalogMetadataSource = {
  record_id: string;
  resource_identifier: string | null;
  title: string | null;
  publication_date: string | null;
  revision_date: string | null;
  creation_date: string | null;
  date_stamp: string | null;
  metadata_url: string | null;
  metadata_url_verified: boolean;
  references: string[];
  record_sha256: string | null;
  response_sha256: string | null;
  fetched_at: string | null;
};

export type FormalDocumentStatus = "current" | "superseded" | "unavailable" | "unresolved";

/** Dokument formalny powiązany z wersją aktu po identyfikatorze i wersji. */
export type FormalDocumentSource = {
  document_identifier: string;
  document_version: string | null;
  publication_id: string | null;
  title: string | null;
  short_name: string | null;
  identification_number: string | null;
  relation: string | null;
  document_date: string | null;
  effective_date: string | null;
  repeal_date: string | null;
  link: string | null;
  /** Tylko zweryfikowany HTTPS może być klikalny. */
  link_verified: boolean;
  record_sha256: string | null;
  status: FormalDocumentStatus;
  /** Widoczne ostrzeżenie dla dokumentu nieaktualnego/niedostępnego. */
  warning: string | null;
};

/** Akt i dokładna wersja z łańcuchem provenance (BK-107). */
export type PogActResult = {
  id: string;
  version: string | null;
  title: string | null;
  resolution_number: string | null;
  resolution_date: string | null;
  act_identifier?: string | null;
  act_version?: string | null;
  publication_id?: string | null;
  version_started_at?: string | null;
  publication_date?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  gml_url?: string | null;
  gml_url_verified?: boolean;
  card_url?: string | null;
  card_url_verified?: boolean;
  data_release_id?: number | null;
  release_label?: string | null;
  artifact_sha256?: string | null;
  fetched_at?: string | null;
  metadata?: CatalogMetadataSource | null;
  formal_documents?: FormalDocumentSource[];
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
  feature_version?: string | null;
  /** Oficjalny URL GML obiektu strefy — źródło parametrów. */
  gml_url?: string | null;
  gml_url_verified?: boolean;
};

export type PogAreaResult = {
  id: string;
  symbol: string | null;
  label: string | null;
  area_sqm: number;
  area_pct: number;
  touches_boundary: boolean;
  source: SourceMetadata | null;
  feature_version?: string | null;
  gml_url?: string | null;
  gml_url_verified?: boolean;
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
