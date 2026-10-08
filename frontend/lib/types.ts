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
  /** Token dostępu z pola `access_token` wyniku analizy (AU-005); bez niego backend zwraca 403. */
  access_token: string;
  /** Symbol strefy MPZP odczytany przez użytkownika z mapy rastrowej (surowy wpis do 200 znaków; serwer sprowadza go do formy kanonicznej ≤ 40 znaków). */
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

/** Rodzaj warunku, od którego zależy wartość parametru uchwały (PV3-08). */
export type MpzpConditionKind = "building_type" | "roof_type" | "subzone" | "location" | "other";

/**
 * unconditional — wartość bez warunku; conditional — wartość alternatywna z warunkami (np. inna
 * wysokość dla dachu płaskiego), NIE sprzeczność; conflict — ta sama przesłanka ma kilka wartości.
 */
export type MpzpValueKind = "unconditional" | "conditional" | "conflict";

/** Warunek wartości: rodzaj, nazwa do wyświetlenia i dosłowny cytat z uchwały. */
export type MpzpValueCondition = {
  kind: MpzpConditionKind;
  label: string;
  quote: string;
};

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
  /** Warunki wartości (PV3-08); brak lub pusta lista = wartość bezwarunkowa (także zapisy sprzed PV3-08). */
  conditions?: MpzpValueCondition[];
  /** Brak w odpowiedziach sprzed PV3-08: wtedy `conflict` przy `conflict_group_id`, inaczej `unconditional`. */
  value_kind?: MpzpValueKind;
  /** Strategia dopasowania silnika ilości (PV3-07), np. `comparative`. */
  extraction_strategy?: string | null;
  /** Przeróbki zapisu przy normalizacji (np. `ratio_to_percent`, `degree_artifact`). */
  normalization_flags?: string[];
  /**
   * PV3-13/14: obecne wyłącznie dla wartości z modelu językowego po bramkach deterministycznych
   * (`extraction_method = "llm_verified"`). Taka wartość jest kandydatem do ręcznej weryfikacji,
   * nigdy „verified”, i nie wypełnia płaskich pól strefy.
   */
  review_status?: "ai_candidate";
  model_id?: string;
  prompt_version?: string;
  response_sha256?: string;
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
  /** Pole przecięcia; null — udział nieustalony (brak wektora, np. symbol ręczny). */
  intersection_area_sqm: number | null;
  /** Udział w działce; null — nieustalony, nigdy nie zakładamy 100%. */
  intersection_pct: number | null;
  /** Pomocniczo: strefa o największym ustalonym udziale; nie zastępuje pełnej listy. */
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
  /** Decyzja użytkownika w trybie ręcznym (BK-204). */
  manual_selection?: ManualZoneSelection | null;
};

/** Zapis ręcznego wskazania symbolu strefy wraz z przypiętą wersją dokumentu. */
export type ManualZoneSelection = {
  /** Symbol w formie kanonicznej (NFKC, jedna spacja wewnętrzna). */
  entered_symbol: string;
  /** Symbol dokładnie tak, jak wpisał go użytkownik; brak w zapisach sprzed PV3-04. */
  entered_symbol_raw?: string | null;
  plan_id: string | null;
  candidate_zone_symbols: string[];
  symbol_in_candidates: boolean;
  document_url: string | null;
  document_sha256: string | null;
  document_version_id: number | null;
  document_fetched_at: string | null;
  /** false — dokument nie był przypięty; parametry pozostają nieustalone. */
  document_pinned: boolean;
  selected_at: string;
};

/** Dokument przypięty przy wstrzymaniu analizy — dokładnie ten, który przeczyta resume. */
export type ManualZoneSourceDocument = {
  requested_url: string | null;
  requested_url_verified: boolean;
  media_type: string;
  filename: string | null;
  sha256: string;
  size_bytes: number;
  fetched_at: string | null;
  document_version_id: number | null;
  /** Względna ścieżka API z przypiętą kopią (nie adres zewnętrzny). */
  preview_path: string;
};

/** Materiał pokazywany przed formularzem symbolu strefy (BK-204). */
export type ManualZoneContext = {
  plan_id: string | null;
  candidate_zone_symbols: string[];
  document_status: "pinned" | "unavailable" | "not_provided";
  document: ManualZoneSourceDocument | null;
  raster_preview_source_key: "mpzp";
  symbol_max_length: number;
  /** Wzorzec formy kanonicznej symbolu — wspólny dla UI i API. */
  symbol_allowed_pattern: string;
  /** Wersja reguł symbolu (forma kanoniczna i wzorzec). */
  symbol_rules_version?: string;
  notice: string;
};

export type CompatibilityStatus =
  | "compatible"
  | "incompatible"
  | "uncertain"
  | "not_applicable"
  | "unknown";

export type CompatibilitySource = {
  kind: "rule_set" | "mpzp" | "pog";
  label: string;
  reference: string | null;
  version: string | null;
  as_of: string | null;
};

/** Para strefa MPZP × strefa POG; rozstrzygnięta para zawsze ma regułę i datę. */
export type CompatibilityZonePair = {
  mpzp_zone_symbol: string;
  mpzp_zone_id: string | null;
  mpzp_assignment_method: MpzpAssignmentMethod;
  mpzp_function: string | null;
  pog_zone_id: string;
  pog_zone_symbol: string | null;
  pog_zone_type: string;
  spatially_identified: boolean;
  overlap_area_sqm: number | null;
  overlap_pct: number | null;
  status: CompatibilityStatus;
  rule_result: "compatible" | "incompatible" | "uncertain" | null;
  rule_id: string | null;
  rule_version: string | null;
  source: string | null;
  as_of: string | null;
  rationale: string;
  manual_review_required: boolean;
};

export type LegacyCompatibilityEvidence = {
  origin: string;
  conflict_with_mpzp: boolean | null;
  result: string | null;
  reasoning: string | null;
  confidence: number | null;
};

/** Informacyjna ocena relacji MPZP–POG (BK-205) — nie opinia prawna. */
export type CompatibilityAssessment = {
  schema_version: string;
  status: CompatibilityStatus;
  reason_code: string;
  /** Data stanu prawnego, do którego odnosi się ocena. */
  as_of: string | null;
  rule_id: string | null;
  rule_version: string | null;
  aggregation: string;
  sources: CompatibilitySource[];
  rationale: string;
  manual_review_required: boolean;
  zone_pairs: CompatibilityZonePair[];
  informational_notice: string;
  legacy_evidence: LegacyCompatibilityEvidence | null;
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
   * Informacyjna ocena relacji MPZP–POG z regułami, parami stref i datą stanu
   * prawnego (BK-205); zastąpiła boolean `conflict_with_mpzp`.
   */
  compatibility_assessment: CompatibilityAssessment | null;
  /** Surowe atrybuty APP/GML lub WMS zachowane dla audytu parsera. */
  raw_attributes: Record<string, unknown> | null;
  ouz_intersection_area_sqm: number | null;
  ouz_intersection_pct: number | null;
  touches_ouz_boundary: boolean;
  /** Wersja stylu POG użyta przy analizie; brak = snapshot sprzed BK-403. */
  presentation_style?: PogPresentationStyleSnapshot | null;
  source: SourceMetadata | null;
};

// --- Wektorowe kafle POG (BK-401) -------------------------------------------

export type PogTileEdition = "all" | "binding" | "project";

export type PogTileLayerName =
  | "zones"
  | "ouz"
  | "downtown"
  | "social_infrastructure_standard"
  | "act_boundary";

/** Metadane wydania POG z URL-em kafli MVT przypiętym do `release_id`. */
export type PogTileRelease = {
  release_id: number;
  source_id: string;
  version_label: string;
  published_at: string | null;
  is_active: boolean;
  artifact_sha256: string | null;
  /** Względny szablon URL kafli, np. /api/v1/map/pog/releases/7/{z}/{x}/{y}.mvt */
  tile_url_template: string;
  tile_schema: string;
  tile_format: string;
  layers: PogTileLayerName[];
  editions: PogTileEdition[];
  default_edition: PogTileEdition;
  min_zoom: number;
  max_zoom: number;
  extent: number;
  buffer: number;
  /** [min_lon, min_lat, max_lon, max_lat] w EPSG:4326. */
  bounds: [number, number, number, number] | null;
  acts_by_legal_status: Partial<Record<PogLegalStatus, number>>;
  /**
   * Zasięg danych każdego aktu (BK-406). Pokrycie widoku jest oceniane z tych
   * metadanych, a nie z pustego kafla; brak pola = wydanie sprzed BK-406.
   */
  coverage_areas?: PogCoverageArea[];
  style_version: string;
  style_sha256: string;
  attribution: string;
  legal_note: string;
};

/**
 * Atrybuty cechy warstwy `zones` kafla MVT. Brak klucza parametru oznacza brak
 * wartości w danych (null), a nie zero — te same nazwy co `PogZoneResult`.
 */
export type PogZoneTileProperties = {
  feature_id: string;
  feature_version?: string;
  symbol?: string;
  label?: string;
  legal_status: PogLegalStatus;
  teryt?: string;
  act_id: string;
  data_release_id: number;
  zone_code: string;
  max_overground_floor_area_ratio?: number;
  max_building_height_m?: number;
  max_building_coverage_pct?: number;
  min_biologically_active_pct?: number;
  parameters_informational?: boolean;
  primary_profiles?: string;
  additional_profiles?: string;
};

/** Zasięg danych aktu w wydaniu POG (EPSG:4326) i kompletność agregatu stref. */
export type PogCoverageArea = {
  act_id: string;
  teryt: string | null;
  legal_status: PogLegalStatus;
  bounds: [number, number, number, number] | null;
  has_boundary: boolean;
  /** `null` — agregat nie został policzony (wydanie sprzed BK-405). */
  is_complete: boolean | null;
  incomplete_reasons: string[];
};

/** Atrybuty cechy OUZ/OZS/OSDIS z kafla MVT. */
export type PogOverlayTileProperties = {
  feature_id: string;
  feature_version?: string;
  symbol?: string;
  label?: string;
  legal_status: PogLegalStatus;
  teryt?: string;
  act_id: string;
  data_release_id: number;
};

/** Warstwy obiektów POG, które inspektor odczytuje z wyrenderowanych kafli. */
export type PogFeatureLayer =
  | "zones"
  | "ouz"
  | "downtown"
  | "social_infrastructure_standard";

/** Jedno trafienie inspektora — po deduplikacji cech z sąsiednich kafli. */
export type PogInspectorHit =
  | { key: string; layer: "zones"; featurePk: number | null; properties: PogZoneTileProperties }
  | {
      key: string;
      layer: Exclude<PogFeatureLayer, "zones">;
      featurePk: number | null;
      properties: PogOverlayTileProperties;
    };

/**
 * Wynik kliknięcia mapy dla inspektora (BK-404). `queried = false`, gdy warstwy
 * POG nie były wyrenderowane — wtedy brak trafień nie jest „brakiem obiektu”.
 */
export type PogPointQuery = {
  lon: number;
  lat: number;
  hits: PogInspectorHit[];
  queried: boolean;
};

/**
 * Stan warstwy mapy (BK-406), niezależny od statusu prawnego aktu. Wyznaczany z
 * metadanych wydania i zdarzeń źródła, nigdy z liczby pikseli lub cech.
 */
export type LayerState =
  | "loading"
  | "available"
  | "partial"
  | "no_coverage"
  | "error"
  | "stale";

/** Stan pobrania metadanych przypiętego wydania POG. */
export type PogReleaseState =
  | { status: "loading"; release: null }
  | { status: "available"; release: PogTileRelease; checkedAt: string }
  | {
      status: "stale";
      release: PogTileRelease;
      /** Chwila ostatniego udanego sprawdzenia wydania. */
      checkedAt: string;
      reason: "refresh_failed" | "release_not_active";
    }
  | { status: "no_release"; release: null }
  | { status: "error"; release: null };

export type PogProfileDetails = {
  code: string;
  label: string | null;
  dictionary_source: string;
};

/** Szczegóły obiektu POG spoza kafla MVT (GET …/features/{feature_id}). */
export type PogFeatureDetails = {
  schema: string;
  tile_schema: string;
  release: {
    release_id: number;
    version_label: string;
    published_at: string | null;
    is_active: boolean;
    artifact_sha256: string | null;
  };
  feature_pk: number;
  feature_id: string;
  feature_version: string | null;
  layer: PogFeatureLayer;
  feature_type: string;
  symbol: string | null;
  label: string | null;
  zone_code: string | null;
  source_zone_type: string | null;
  /** Brak wartości to `null`, nigdy 0. Obiekty OUZ/OZS/OSDIS mają `{}`. */
  parameters: Record<string, number | null>;
  parameters_informational: boolean;
  primary_profiles: PogProfileDetails[];
  additional_profiles: PogProfileDetails[];
  act: {
    act_id: string;
    act_version: string | null;
    name: string | null;
    teryt: string | null;
    legal_status: PogLegalStatus;
    legal_status_code: string | null;
    resolution_number: string | null;
    resolution_date: string | null;
    legal_valid_from: string | null;
    legal_valid_to: string | null;
    publication_id: string | null;
    manual_review_required: boolean;
  };
  source_reference: string | null;
};

export type PogAreaSummaryZone = {
  zone_code: string;
  area_sqm: number;
  area_sqkm: number;
  /** `null`, gdy brak mianownika (granicy aktu) — nigdy domyślne 100%. */
  share_pct: number | null;
  zone_count: number;
};

/** Agregat powierzchniowy stref aktu albo gminy (BK-405). */
export type PogAreaSummary = {
  schema: string;
  release_id: number;
  release_label: string;
  release_is_active: boolean;
  artifact_sha256: string | null;
  scope: "act" | "municipality";
  act_id: string | null;
  act_version: string | null;
  teryt: string | null;
  edition: "binding" | "project" | null;
  legal_status: PogLegalStatus | null;
  act_ids: string[];
  act_count: number;
  denominator_area_sqm: number | null;
  denominator_area_sqkm: number | null;
  denominator_source: string | null;
  zones_area_sqm: number;
  zones_area_sqkm: number;
  missing_area_sqm: number | null;
  missing_area_sqkm: number | null;
  overlap_area_sqm: number;
  outside_area_sqm: number;
  deduplicated_area_sqm: number | null;
  share_sum_pct: number | null;
  share_tolerance_pct: number;
  area_tolerance_sqm: number;
  zone_count: number;
  is_complete: boolean;
  incomplete_reasons: string[];
  zones: PogAreaSummaryZone[];
  method_version: string;
  computed_at: string;
};

export type PogAreaSummaryScope =
  | { actId: string }
  | { teryt: string; edition: "binding" | "project" };

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
  /** Przecięcie strefy z działką jako GeoJSON Feature w WGS84 (prezentacja). */
  geometry_geojson?: Record<string, unknown> | null;
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
  geometry_geojson?: Record<string, unknown> | null;
};

/** Zamrożona wersja stylu POG z chwili analizy (BK-403). */
export type PogPresentationStyleSnapshot = {
  style_version: string;
  style_sha256: string;
  zones: Record<string, { label: string; fill: string; outline: string }>;
  unknown_zone: Record<string, unknown>;
  null_style: Record<string, unknown>;
  overlays: Record<string, Record<string, unknown>>;
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

export type RiskSectionName = "flood" | "nature";
export type RiskSeverity = "low" | "medium" | "high";

/**
 * Obiekt ryzyka (BK-303). Pola strukturalne są jedynym nośnikiem danych;
 * `description` to tekst prezentacyjny. `null`/brak pola = wartość nieznana
 * (zapis sprzed BK-303), nigdy 0.
 */
export type RiskResult = {
  risk_type: string;
  section?: RiskSectionName | null;
  /** Unikalny identyfikator obiektu w źródle (gml:id). */
  feature_id?: string | null;
  severity?: RiskSeverity | null;
  probability_class?: string | null;
  /** Okres powtarzalności wyłącznie z atrybutu returnPeriod źródła. */
  return_period_years?: number | null;
  protection_type?: string | null;
  name?: string | null;
  intersection_area_sqm?: number | null;
  intersection_pct?: number | null;
  /** True: obiekt wyłącznie styka się z granicą działki. */
  touches_boundary?: boolean | null;
  description: string;
  /** Przecięcie strefy ryzyka z działką jako GeoJSON Feature w WGS84. */
  geometry_geojson: Record<string, unknown> | null;
  warnings?: string[];
  source: SourceMetadata;
};

export type RiskSectionStatus = "available" | "unavailable" | "error" | "unknown";
export type RiskRelation = "no_match" | "boundary_only" | "intersection" | "unknown";

/** Status i provenance sekcji ryzyka niezależnie od listy obiektów. */
export type RiskSectionResult = {
  schema_version: string;
  section: RiskSectionName;
  status: RiskSectionStatus;
  reason_code: string | null;
  relation: RiskRelation;
  feature_count: number | null;
  intersecting_feature_count: number | null;
  boundary_feature_count: number | null;
  /** Pole sumy mnogościowej przecięć (bez podwójnego liczenia), m². */
  union_intersection_area_sqm: number | null;
  /** Udział sumy mnogościowej przecięć w działce, % (≤ 100). */
  union_intersection_pct: number | null;
  feature_ids: string[];
  source: SourceMetadata | null;
  warnings: string[];
};

/**
 * Status pomiaru rzeźby terenu (BK-301). Brak pokrycia, niedostępność i
 * zapis bez danych NIE są płaskim terenem — wysokości są wtedy null, nie 0.
 */
export type TerrainStatus = "available" | "no_coverage" | "unavailable" | "unknown";

export type TerrainSlopeStatistics = {
  mean_deg: number;
  median_deg: number;
  /** 90. percentyl (interpolacja liniowa R-7). */
  p90_deg: number;
  max_deg: number;
  mean_pct: number;
  median_pct: number;
  p90_pct: number;
  max_pct: number;
};

export type TerrainSlopeClass = {
  class_id: string;
  label: string;
  /** Dolna granica klasy (włącznie), %. */
  min_pct: number;
  /** Górna granica klasy (rozłącznie), %; null — klasa otwarta. */
  max_pct: number | null;
  pixel_count: number;
  area_sqm: number;
  share_pct: number;
};

export type AspectDirection = "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW";

export type TerrainAspectResult = {
  /** flat — teren płaski (kierunek null, nie 0°); dispersed — brak dominanty. */
  status: "defined" | "dispersed" | "flat";
  /** Azymut kierunku spadku (0° = północ, zgodnie z zegarem). */
  mean_azimuth_deg: number | null;
  resultant_length: number | null;
  dominant_direction: AspectDirection | null;
  sector_shares_pct: Record<string, number>;
  non_flat_share_pct: number;
  flat_threshold_pct: number;
};

export type TerrainProfileSample = {
  distance_m: number;
  /** Easting EPSG:2180. */
  x: number;
  /** Northing EPSG:2180. */
  y: number;
  /** Wysokość z interpolacji dwuliniowej; null dla NoData, nigdy 0. */
  height_m: number | null;
  inside_parcel: boolean;
};

export type TerrainProfileResult = {
  method: string;
  crs: "EPSG:2180";
  start: [number, number];
  end: [number, number];
  length_m: number;
  step_m: number;
  interpolation: "bilinear";
  samples: TerrainProfileSample[];
  /** Linia profilu w WGS84 jako GeoJSON Feature (prezentacja). */
  line_geojson: Record<string, unknown> | null;
};

export type TerrainRasterMetadata = {
  coverage_id: string;
  crs: "EPSG:2180";
  resolution_m: number;
  width_px: number;
  height_px: number;
  bbox: [number, number, number, number];
  buffer_m: number;
  size_bytes: number;
  nodata_value: number | null;
  nodata_policy: string;
  vertical_datum: string | null;
  gdal_version: string | null;
};

/** Pochodne rastra NMT (BK-302): spadek, klasy, ekspozycja i profil. */
export type TerrainReliefResult = {
  schema_version: string;
  algorithm_version: string;
  slope_classes_version: string;
  status: TerrainStatus;
  reason_code: string | null;
  /** Rozdzielczość danych źródłowych, m. */
  resolution_m: number | null;
  parcel_pixel_count: number | null;
  valid_pixel_count: number | null;
  nodata_pixel_count: number | null;
  valid_area_share_pct: number | null;
  min_height_m: number | null;
  max_height_m: number | null;
  mean_height_m: number | null;
  slope: TerrainSlopeStatistics | null;
  slope_classes: TerrainSlopeClass[];
  aspect: TerrainAspectResult | null;
  profile: TerrainProfileResult | null;
  raster: TerrainRasterMetadata | null;
  source: SourceMetadata | null;
  warnings: string[];
};

/** Rzeźba terenu działki (BK-301): Hmin, Hmax, deniwelacja i jakość pomiaru. */
export type TerrainResult = {
  schema_version: string;
  status: TerrainStatus;
  reason_code: string | null;
  /** m n.p.m.; może być ujemna. null, gdy nie zmierzono. */
  min_height_m: number | null;
  max_height_m: number | null;
  /** Hmax − Hmin w metrach; 0 oznacza zmierzony płaski teren. */
  height_difference_m: number | null;
  /** Siatka próbkowania usługi NMT, m. */
  grid_size_m: number | null;
  sampled_points: number | null;
  /** Provenance zapytania — także dla braku pokrycia i niedostępności. */
  source: SourceMetadata | null;
  warnings: string[];
  relief: TerrainReliefResult | null;
};

/** Status sekcji według kontraktu źródła (BK-504) — niezależny od świeżości. */
export type SectionQualityStatus =
  | "available"
  | "partial"
  | "no_coverage"
  | "unavailable"
  | "error"
  | "unknown"
  | "out_of_scope"
  | "awaiting_input";

export type FreshnessState = "fresh" | "stale" | "unknown";
export type FreshnessBasis = "source_declared_interval" | "project_decision";

export type SectionQualityKey =
  | "parcel"
  | "mpzp"
  | "pog"
  | "pog_overlays"
  | "flood"
  | "nature"
  | "terrain"
  | "utilities"
  | "transport"
  | "mpzp_pog_relation";

export type FreshnessAssessment = {
  state: FreshnessState;
  reason_code: string | null;
  /** Punkt odniesienia oceny: chwila analizy (ocena historyczna). */
  reference_at: string;
  age_seconds: number | null;
  max_age_days: number | null;
  basis: FreshnessBasis | null;
};

export type SectionQuality = {
  section: SectionQualityKey;
  report_section: string;
  status: SectionQualityStatus;
  source_id: string | null;
  source_name: string | null;
  /** Czas pobrania danych — nie data wejścia aktu w życie. */
  fetched_at: string | null;
  data_release_id: number | null;
  source_version: string | null;
  manual_review_required: boolean;
  freshness: FreshnessAssessment;
  policy_version: string;
  reason_codes: string[];
};

export type QualityLegendItem = { id: string; label: string; description: string };
export type QualityLegendReason = { code: string; label: string };
export type SectionQualityLegend = {
  statuses: QualityLegendItem[];
  freshness: QualityLegendItem[];
  reasons: QualityLegendReason[];
};

/** Trwała macierz kompletności i świeżości sekcji zapisana z analizą (BK-504). */
export type SectionQualityMatrix = {
  schema_version: string;
  policy_version: string;
  reference_at: string;
  /** stored — ocena z chwili analizy; reconstructed — zapis sprzed BK-504 oceniony przy odczycie. */
  origin: "stored" | "reconstructed";
  sections: SectionQuality[];
  matrix_sha256: string;
  legend: SectionQualityLegend;
};

/** Status źródła discovery MPZP (AU-004); statusy są rozłączne. */
export type MpzpDiscoveryStatus = "available" | "no_match" | "no_coverage" | "unavailable" | "unknown";

/** Zmiana planu wymieniona przy akcie w KIMPZP — nigdy osobny akt. */
export type MpzpDiscoveryAmendment = {
  kind: "text_change" | "change" | "note";
  resolution_number: string | null;
  name: string | null;
  adopted_on: string | null;
  valid_from: string | null;
  document_url: string | null;
  bip_url: string | null;
  raw_text: string | null;
  document_url_verified: boolean;
  bip_url_verified: boolean;
};

export type MpzpDiscoveryLinkField = "text_url" | "legend_url" | "drawing_url" | "bip_url" | "www_url";

/** Akt MPZP obecny w punkcie działki według KIMPZP (discovery, nie przecięcie wektorowe). */
export type MpzpDiscoveryAct = {
  resolution_number: string | null;
  resolution_date: string | null;
  name: string | null;
  valid_from: string | null;
  repealed_on: string | null;
  legal_status: "binding" | "not_binding" | "unknown";
  text_url: string | null;
  legend_url: string | null;
  drawing_url: string | null;
  bip_url: string | null;
  www_url: string | null;
  journal: string | null;
  informatization: "vector" | "raster" | "unknown";
  zone_symbols: string[];
  amendments: MpzpDiscoveryAmendment[];
  source_format: string;
  /** Linki zweryfikowane przez backend jako HTTPS — tylko one są klikalne. */
  verified_links: MpzpDiscoveryLinkField[];
};

export type MpzpDiscoverySection = {
  schema_version: string;
  status: MpzpDiscoveryStatus;
  reason_codes: string[];
  acts: MpzpDiscoveryAct[];
  /** Numer uchwały użytej dalej; null przy kilku aktach albo braku aktu. */
  selected_act: string | null;
  multiple_acts_at_point: boolean;
  multiple_acts_on_parcel: boolean;
  candidate_zone_symbols: string[];
  sampled_points: number;
  failed_points: number;
  is_discovery_only: boolean;
  source: SourceMetadata | null;
};

export type AnalyzeResponse = {
  analysis_id: number | null;
  /** Token dostępu do raportu PDF i dokumentu analizy; null dla wyniku niezapisanego. */
  access_token: string | null;
  status: string;
  analyzed_at: string;
  parcel: ParcelGeometryResponse | null;
  mpzp_zones: MpzpZoneResult[];
  /** Akty wskazane przez KIMPZP i status źródła; brak pola = odpowiedź sprzed AU-004. */
  mpzp_discovery?: MpzpDiscoverySection | null;
  pog: PogResult | null;
  infrastructure: InfrastructureResult[];
  utilities_preview: UtilitiesPreviewResult | null;
  risks: RiskResult[];
  /** Status sekcji flood/nature; brak pola (starsze odpowiedzi) = status unknown. */
  risk_sections?: RiskSectionResult[];
  /** Sekcja NMT; brak pola (starsze odpowiedzi) jest traktowany jak status unknown. */
  terrain?: TerrainResult | null;
  /** Macierz kompletności i świeżości sekcji; brak pola = odpowiedź sprzed BK-504. */
  section_quality?: SectionQualityMatrix | null;
  buildable_area_sqm: number | null;
  manual_zone_required: boolean;
  /** Plan, kandydaci i przypięty dokument pokazywane przed podaniem symbolu. */
  manual_zone_context?: ManualZoneContext | null;
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
