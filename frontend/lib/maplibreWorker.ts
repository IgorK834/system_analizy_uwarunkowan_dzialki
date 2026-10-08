/**
 * Adres workera MapLibre GL JS 6 (AU-010).
 *
 * MapLibre 5 miał worker w głównym pliku; wersja 6 ładuje osobny moduł `maplibre-gl-worker.mjs` pod
 * adresem liczonym wewnątrz biblioteki z `new URL("./maplibre-gl-worker.mjs", import.meta.url)` (nazwa
 * pliku w zmiennej). Turbopack (Next 16) nie potrafi tego zbundlować: po `next build` adres wskazuje
 * stronę główną, przeglądarka dostaje HTML zamiast JavaScriptu i mapa nie ładuje kafli
 * („Worker failed to load”). Statyczne `new URL("…", import.meta.url)` bundler rozpoznaje jako zasób
 * i emituje do `_next/static/media`, więc adres podajemy jawnie przez `setWorkerUrl`.
 */
import { setWorkerUrl } from "maplibre-gl";

let configured = false;

export const MAPLIBRE_WORKER_URL = new URL(
  "../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs",
  import.meta.url,
).href;

/** Idempotentne: wołać przed utworzeniem pierwszej mapy. */
export function configureMapLibreWorker(): void {
  if (configured) return;
  configured = true;
  setWorkerUrl(MAPLIBRE_WORKER_URL);
}
