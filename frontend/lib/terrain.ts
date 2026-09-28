import type {
  AspectDirection,
  TerrainAspectResult,
  TerrainProfileResult,
  TerrainResult,
  TerrainStatus,
} from "@/lib/types";

/**
 * Prezentacja sekcji rzeźby terenu (BK-301/BK-302), spójna z raportem PDF.
 *
 * Cztery statusy są rozłączne: tylko `available` pokazuje wysokości, a
 * zmierzone 0 m jest jedynym przypadkiem opisywanym jako teren płaski.
 */
export const TERRAIN_STATUS_LABELS: Record<TerrainStatus, string> = {
  available: "zmierzono",
  no_coverage: "brak pokrycia danymi NMT",
  unavailable: "pomiar niedostępny",
  unknown: "brak informacji w zapisanym wyniku",
};

const TERRAIN_STATUS_NOTES: Record<Exclude<TerrainStatus, "available">, string> = {
  no_coverage:
    "Źródło potwierdziło brak danych wysokościowych dla działki. Deniwelacja jest nieznana — brak pokrycia nie oznacza płaskiego terenu.",
  unavailable:
    "Nie udało się uzyskać danych wysokościowych. Deniwelacja jest nieznana — wynik nie oznacza płaskiego terenu.",
  unknown:
    "Zapisany wynik pochodzi sprzed sekcji rzeźby terenu i nie zawiera pomiaru NMT. Brak informacji nie oznacza płaskiego terenu.",
};

const REASON_LABELS: Record<string, string> = {
  SERVICE_TIMEOUT: "usługa nie odpowiedziała w wymaganym czasie",
  SERVICE_HTTP_ERROR: "błąd HTTP usługi",
  SERVICE_REPORTED_ERROR: "usługa zgłosiła błąd w odpowiedzi",
  SERVICE_ERROR: "usługa odrzuciła zapytanie",
  INVALID_RESPONSE: "odpowiedź bez wysokości",
  NO_COVERAGE_SENTINEL: "usługa zwróciła znacznik braku danych",
  LEGACY_SNAPSHOT: "zapis sprzed sekcji NMT",
  UNEXPECTED_ERROR: "nieoczekiwany błąd",
  MISSING_MEASUREMENT: "brak pomiaru w wyniku sekcji",
  SOURCE_NOT_RUNNABLE: "kontrakt źródła niepotwierdzony w katalogu",
  CONTRACT_MISMATCH: "odpowiedź niezgodna z kontraktem WCS",
  RASTER_TOO_LARGE: "raster przekracza limit rozmiaru",
  RASTER_CRS_MISMATCH: "raster w nieoczekiwanym układzie współrzędnych",
  RASTER_GRID_INVALID: "siatka rastra niezgodna z żądaniem",
  RASTER_DECODE_ERROR: "nie udało się odczytać GeoTIFF",
  OUTSIDE_COVERAGE: "działka poza zasięgiem pokrycia",
  NO_DATA_IN_PARCEL: "brak danych rastra w obrysie działki",
  PARCEL_BELOW_RESOLUTION: "działka mniejsza niż piksel rastra",
  INSUFFICIENT_VALID_WINDOW: "brak pełnego okna 3×3 z danymi",
};

const ASPECT_LABELS: Record<AspectDirection, string> = {
  N: "północna",
  NE: "północno-wschodnia",
  E: "wschodnia",
  SE: "południowo-wschodnia",
  S: "południowa",
  SW: "południowo-zachodnia",
  W: "zachodnia",
  NW: "północno-zachodnia",
};

export function terrainStatusLabel(status: TerrainStatus): string {
  return TERRAIN_STATUS_LABELS[status] ?? TERRAIN_STATUS_LABELS.unknown;
}

/** Nota statusu; dla pomiaru 0 m jawnie mówi o płaskim terenie. */
export function terrainStatusNote(terrain: TerrainResult): string | null {
  if (terrain.status === "available") {
    return terrain.height_difference_m === 0
      ? "Zmierzona deniwelacja wynosi 0 m — w siatce próbkowania teren w obrysie działki jest płaski."
      : null;
  }
  return TERRAIN_STATUS_NOTES[terrain.status] ?? TERRAIN_STATUS_NOTES.unknown;
}

export function reasonLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return REASON_LABELS[code] ?? code;
}

/** Wynik zastępczy dla odpowiedzi bez sekcji terrain (zapis sprzed BK-301). */
export function terrainOrUnknown(terrain: TerrainResult | null | undefined): TerrainResult {
  return (
    terrain ?? {
      schema_version: "1.0",
      status: "unknown",
      reason_code: "LEGACY_SNAPSHOT",
      min_height_m: null,
      max_height_m: null,
      height_difference_m: null,
      grid_size_m: null,
      sampled_points: null,
      source: null,
      warnings: [],
      relief: null,
    }
  );
}

export function formatNumber(value: number | null | undefined, maximumFractionDigits = 3): string {
  if (value == null) return "—";
  const normalized = Object.is(value, -0) ? 0 : value;
  return normalized.toLocaleString("pl-PL", { maximumFractionDigits });
}

export function formatMeters(value: number | null | undefined): string {
  return value == null ? "—" : `${formatNumber(value)} m`;
}

export function formatDegrees(value: number | null | undefined): string {
  return value == null ? "—" : `${formatNumber(value, 2)}°`;
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${formatNumber(value, 2)}%`;
}

export function aspectDescription(aspect: TerrainAspectResult): string {
  if (aspect.status === "flat") {
    return `nie wyznaczono — teren płaski (nachylone ≥ ${formatNumber(aspect.flat_threshold_pct, 1)}% jest ${formatPercent(aspect.non_flat_share_pct)} powierzchni)`;
  }
  const azimuth = formatDegrees(aspect.mean_azimuth_deg);
  if (aspect.status === "dispersed" || !aspect.dominant_direction) {
    return `rozproszona — brak dominującego kierunku (średni azymut ${azimuth})`;
  }
  return `${ASPECT_LABELS[aspect.dominant_direction]} (średni azymut spadku ${azimuth})`;
}

export type ProfileChart = {
  segments: string[];
  minHeight: number | null;
  maxHeight: number | null;
  missingCount: number;
};

/**
 * Współrzędne polilinii profilu w układzie SVG. Próbka bez danych przerywa
 * linię — luka nie jest rysowana jako wysokość 0.
 */
export function profileChart(
  profile: TerrainProfileResult,
  width: number,
  height: number,
  padding = 6,
): ProfileChart {
  const heights = profile.samples
    .map((sample) => sample.height_m)
    .filter((value): value is number => value != null);
  const missingCount = profile.samples.length - heights.length;
  if (heights.length === 0 || profile.length_m <= 0) {
    return { segments: [], minHeight: null, maxHeight: null, missingCount };
  }
  const low = Math.min(...heights);
  const high = Math.max(...heights);
  const span = high - low || 1;
  const usableWidth = width - 2 * padding;
  const usableHeight = height - 2 * padding;
  const segments: string[] = [];
  let current: string[] = [];
  for (const sample of profile.samples) {
    if (sample.height_m == null) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      continue;
    }
    const x = padding + (usableWidth * sample.distance_m) / profile.length_m;
    const y = padding + usableHeight * (1 - (sample.height_m - low) / span);
    current.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  if (current.length > 1) segments.push(current.join(" "));
  return { segments, minHeight: low, maxHeight: high, missingCount };
}
