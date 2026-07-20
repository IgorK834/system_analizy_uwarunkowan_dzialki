export function getApiBaseUrl(): string {
  const value = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  if (!value) {
    throw new Error(
      "Brak konfiguracji NEXT_PUBLIC_API_BASE_URL. Ustaw adres API przed uruchomieniem aplikacji.",
    );
  }

  return value.replace(/\/+$/, "");
}

/**
 * Szablon cache'owanych kafelków MPZP udostępnianych przez własny backend.
 * Opcjonalna zmienna pozwala skierować ruch do CDN/reverse proxy bez zmiany
 * kodu. Domyślnie kafelki korzystają z tego samego API co analiza działki.
 */
export function getKimpzpTileUrl(): string {
  const configured = process.env.NEXT_PUBLIC_KIMPZP_TILE_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  return `${getApiBaseUrl()}/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png`;
}

/** Krajowa, prezentacyjna usługa WMS uchwalonych Planów Ogólnych Gmin. */
export function getPogWmsUrl(): string | null {
  const value = process.env.NEXT_PUBLIC_POG_WMS_URL?.trim();
  return value ? value.replace(/\/+$/, "") : null;
}

export function getPogWmsLayers(): string {
  return (
    process.env.NEXT_PUBLIC_POG_WMS_LAYERS?.trim() ||
    "strefaPlanistyczna,obszarUzupelnieniaZabudowy,obszarZabSrodmiejskiej,aktPlanowaniaprzestrzennego"
  );
}
