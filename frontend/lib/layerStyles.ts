/**
 * Konfiguracja kolorów, identyfikatorów źródeł/warstw i opisów legendy dla
 * warstw mapowych dodawanych przez ResultPanel, LayerToggle i PreviewOverlays.
 *
 * Kolory i identyfikatory są scentralizowane tutaj (nie jako inline hex w
 * JSX), żeby uniknąć rozjazdu między komponentem, który dodaje warstwę do
 * MapLibre, i komponentem, który renderuje jej legendę.
 */

// --- Działka (obrys z ULDK) ---
// Kolor zbliżony do palety --accent z globals.css, ale z wyższym kontrastem
// na warstwie rastrowej OSM, żeby obrys był czytelny na różnych podłożach.
export const PARCEL_FILL_COLOR = "#176c4b";
export const PARCEL_LINE_COLOR = "#0d5137";
export const PARCEL_FILL_OPACITY = 0.12;
export const PARCEL_LINE_WIDTH = 2.5;

export const PARCEL_SOURCE_ID = "parcel-source";
export const PARCEL_FILL_LAYER_ID = "parcel-fill-layer";
export const PARCEL_LINE_LAYER_ID = "parcel-line-layer";

// --- Obszar zabudowy (techniczne przybliżenie po odsunięciu od granicy) ---
// Odrębny odcień (pomarańcz), żeby wizualnie odróżnić przybliżenie techniczne
// od pewnego obrysu działki — zgodne z zasadą rozróżniania danych
// pewnych/częściowych z context.md.
export const BUILDABLE_AREA_FILL_COLOR = "#c96a1f";
export const BUILDABLE_AREA_LINE_COLOR = "#8a4813";
export const BUILDABLE_AREA_FILL_OPACITY = 0.18;
export const BUILDABLE_AREA_LINE_WIDTH = 2;
export const BUILDABLE_AREA_LINE_DASH: [number, number] = [2, 1.5];

export const BUILDABLE_AREA_SOURCE_ID = "buildable-area-source";
export const BUILDABLE_AREA_FILL_LAYER_ID = "buildable-area-fill-layer";
export const BUILDABLE_AREA_LINE_LAYER_ID = "buildable-area-line-layer";

// --- Sieci uzbrojenia terenu ---
export const NETWORK_COLOR = "#1769aa";
export const NETWORK_LINE_WIDTH = 3;
export const NETWORK_SOURCE_ID = "network-source";
export const NETWORK_FILL_LAYER_ID = "network-fill-layer";
export const NETWORK_LINE_LAYER_ID = "network-line-layer";

// --- Techniczne strefy ochronne sieci ---
export const PROTECTION_ZONE_FILL_COLOR = "#c43d3d";
export const PROTECTION_ZONE_LINE_COLOR = "#8f2525";
export const PROTECTION_ZONE_FILL_OPACITY = 0.22;
export const PROTECTION_ZONE_LINE_WIDTH = 1.5;
export const PROTECTION_ZONE_SOURCE_ID = "protection-zone-source";
export const PROTECTION_ZONE_FILL_LAYER_ID = "protection-zone-fill-layer";
export const PROTECTION_ZONE_LINE_LAYER_ID = "protection-zone-line-layer";

// --- Ryzyka środowiskowe i przestrzenne ---
export const RISK_FILL_COLOR = "#7b3fb3";
export const RISK_LINE_COLOR = "#54277d";
export const RISK_FILL_OPACITY = 0.2;
export const RISK_LINE_WIDTH = 2;
export const RISK_SOURCE_ID = "risk-source";
export const RISK_FILL_LAYER_ID = "risk-fill-layer";
export const RISK_LINE_LAYER_ID = "risk-line-layer";

// --- Akty MPZP z Rejestru Urbanistycznego (AU-101): część działki objęta aktem i granica aktu ---
// Akt nie jest strefą, więc kolor różni się od stref/ryzyk; granica jest przerywana (kontekst), a część
// działki objęta aktem wypełniona.
export const MPZP_ACT_FILL_COLOR = "#2a5db0";
export const MPZP_ACT_LINE_COLOR = "#1c4180";
export const MPZP_ACT_FILL_OPACITY = 0.18;
export const MPZP_ACT_LINE_WIDTH = 1.5;
export const MPZP_ACT_SOURCE_ID = "mpzp-act-source";
export const MPZP_ACT_FILL_LAYER_ID = "mpzp-act-fill-layer";
export const MPZP_ACT_LINE_LAYER_ID = "mpzp-act-line-layer";
export const MPZP_ACT_BOUNDARY_COLOR = "#1c4180";
export const MPZP_ACT_BOUNDARY_WIDTH = 2;
export const MPZP_ACT_BOUNDARY_DASH: [number, number] = [3, 2];
export const MPZP_ACT_BOUNDARY_SOURCE_ID = "mpzp-act-boundary-source";
export const MPZP_ACT_BOUNDARY_LAYER_ID = "mpzp-act-boundary-layer";

// --- Droga i zabudowa sąsiednia z lokalnego wydania BDOT10k (Task 23.2–23.3) ---
// Oś najbliższej drogi jest linią (brąz), odcinek działka → oś — cienką linią przerywaną, a obrysy budynków —
// szarym wypełnieniem. Kolory różnią się od stref planistycznych i ryzyk.
export const ROAD_LINE_COLOR = "#8a5a00";
export const ROAD_LINE_WIDTH = 3.5;
export const ROAD_SOURCE_ID = "road-context-source";
export const ROAD_FILL_LAYER_ID = "road-context-unused-fill";
export const ROAD_LINE_LAYER_ID = "road-context-line-layer";
export const ROAD_LINK_LINE_COLOR = "#8a5a00";
export const ROAD_LINK_LINE_WIDTH = 1.5;
export const ROAD_LINK_LINE_DASH: [number, number] = [2, 2];
export const ROAD_LINK_SOURCE_ID = "road-link-source";
export const ROAD_LINK_FILL_LAYER_ID = "road-link-unused-fill";
export const ROAD_LINK_LINE_LAYER_ID = "road-link-line-layer";
export const BUILDINGS_FILL_COLOR = "#6b7280";
export const BUILDINGS_LINE_COLOR = "#374151";
export const BUILDINGS_FILL_OPACITY = 0.45;
export const BUILDINGS_LINE_WIDTH = 1;
export const BUILDINGS_SOURCE_ID = "neighbour-buildings-source";
export const BUILDINGS_FILL_LAYER_ID = "neighbour-buildings-fill-layer";
export const BUILDINGS_LINE_LAYER_ID = "neighbour-buildings-line-layer";

// --- Nakładka WMS MPZP (KIMPZP) ---
export const MPZP_WMS_SOURCE_ID = "mpzp-wms-source";
export const MPZP_WMS_LAYER_ID = "mpzp-wms-layer";

// --- Nakładka WMS POG (per gmina, adres z odpowiedzi analizy) ---
export const POG_WMS_SOURCE_ID = "pog-wms-source";
export const POG_WMS_LAYER_ID = "pog-wms-layer";

// --- Nakładka WMS uzbrojenia terenu (KIUT) ---
export const KIUT_WMS_SOURCE_ID = "kiut-wms-source";
export const KIUT_WMS_LAYER_ID = "kiut-wms-layer";
export const KIUT_DISPLAY_MIN_ZOOM = 17;

// Nakładki planistyczne są domyślnie włączone, ale MapLibre nie pobiera ich
// kafli przed osiągnięciem tej skali. Chroni to publiczne WMS-y przed serią
// zapytań dla widoku całej Polski, a użytkownik dostaje szczegóły dopiero przy
// skali, na której plan jest czytelny.
export const PLANNING_WMS_MIN_ZOOM = 11;
// Backend przechowuje natywne kafle do z18. Przy większym zbliżeniu MapLibre
// skaluje już pobrany kafel zamiast generować nowy wariant WMS dla każdego
// kolejnego zoomu, co istotnie poprawia współczynnik trafień cache.
export const MPZP_NATIVE_MAX_ZOOM = 18;
export const PLANNING_WMS_MAX_ZOOM = 22;

// Nakładki rastrowe WMS muszą być półprzezroczyste, żeby nie przykrywały
// całkowicie podkładu OSM i warstw wektorowych działki/obszaru zabudowy.
export const OVERLAY_OPACITY = 0.55;
// Cienkie linie sieci KIUT wymagają wysokiego krycia, aby nie zlewać się z tłem.
export const KIUT_OVERLAY_OPACITY = 0.95;

export type LayerId =
  | "parcel"
  | "buildable_area"
  | "networks"
  | "protection_zones"
  | "risks"
  | "mpzp_acts"
  | "road_context"
  | "neighbour_buildings"
  | "land_cover"
  | "mpzp_wms"
  | "pog_wms"
  | "kiut_wms";

export type LayerLegendEntry = {
  id: LayerId;
  label: string;
  color: string;
};

// Legenda pokazywana obok przełączników w LayerToggle — jedno miejsce prawdy
// dla nazw domenowych i kolorów, zgodnie z wymogiem context.md, żeby każda
// warstwa mapowa miała nazwę domenową i kolor legendy.
export const LAYER_LEGEND: Record<LayerId, LayerLegendEntry> = {
  parcel: { id: "parcel", label: "Obrys działki", color: PARCEL_FILL_COLOR },
  buildable_area: {
    id: "buildable_area",
    label: "Obszar zabudowy (przybliżenie techniczne)",
    color: BUILDABLE_AREA_FILL_COLOR,
  },
  networks: {
    id: "networks",
    label: "Sieci uzbrojenia terenu",
    color: NETWORK_COLOR,
  },
  protection_zones: {
    id: "protection_zones",
    label: "Strefy ochronne sieci",
    color: PROTECTION_ZONE_FILL_COLOR,
  },
  risks: {
    id: "risks",
    label: "Ryzyka i formy ochrony",
    color: RISK_FILL_COLOR,
  },
  mpzp_acts: {
    id: "mpzp_acts",
    label: "Granice aktów MPZP (Rejestr Urbanistyczny)",
    color: MPZP_ACT_FILL_COLOR,
  },
  road_context: {
    id: "road_context",
    label: "Najbliższa droga (oś jezdni, BDOT10k)",
    color: ROAD_LINE_COLOR,
  },
  land_cover: {id: "land_cover", label: "Pokrycie terenu i orientacyjne bufory lasu (BDOT10k)", color: "#739b43"},
  neighbour_buildings: {
    id: "neighbour_buildings",
    label: "Budynki w sąsiedztwie (BDOT10k)",
    color: BUILDINGS_FILL_COLOR,
  },
  mpzp_wms: {
    id: "mpzp_wms",
    label: "MPZP (nakładka WMS, KIMPZP)",
    color: "#2a5db0",
  },
  pog_wms: {
    id: "pog_wms",
    label: "Plan Ogólny Gminy (nakładka WMS)",
    color: "#7a3fae",
  },
  kiut_wms: {
    id: "kiut_wms",
    label: "Uzbrojenie terenu (nakładka WMS, KIUT)",
    color: "#147d86",
  },
};


export const LAND_COVER_COLORS: Record<string, string> = {
  OT_PTWP_A: "#61a5d8", OT_PTZB_A: "#b78274", OT_PTLZ_A: "#387c45", OT_PTRK_A: "#91ad69",
  OT_PTUT_A: "#b7a34c", OT_PTTR_A: "#c5cf81", OT_PTKM_A: "#99999b", OT_PTGN_A: "#d7bf98",
  OT_PTPL_A: "#c9beb3", OT_PTSO_A: "#ad829e", OT_PTWZ_A: "#bc915f", OT_PTNZ_A: "#b5a8cb",
};
