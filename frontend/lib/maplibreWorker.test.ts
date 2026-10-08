import { existsSync } from "node:fs";
import path from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

const setWorkerUrl = vi.hoisted(() => vi.fn());
vi.mock("maplibre-gl", () => ({ setWorkerUrl }));

describe("adres workera MapLibre 6 (AU-010)", () => {
  beforeEach(() => {
    setWorkerUrl.mockReset();
    vi.resetModules();
  });

  it("wskazuje plik workera z pakietu maplibre-gl, który istnieje na dysku", async () => {
    const { MAPLIBRE_WORKER_URL } = await import("@/lib/maplibreWorker");

    expect(MAPLIBRE_WORKER_URL).toMatch(/\/node_modules\/maplibre-gl\/dist\/maplibre-gl-worker\.mjs$/);
    // Środowisko testowe (jsdom) nadaje `import.meta.url` schemat http, więc plik sprawdzamy po ścieżce.
    expect(
      existsSync(path.resolve(__dirname, "../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs")),
    ).toBe(true);
  });

  it("ustawia adres workera dokładnie raz, także przy wielu mapach", async () => {
    const { MAPLIBRE_WORKER_URL, configureMapLibreWorker } = await import("@/lib/maplibreWorker");

    configureMapLibreWorker();
    configureMapLibreWorker();
    configureMapLibreWorker();

    expect(setWorkerUrl).toHaveBeenCalledOnce();
    expect(setWorkerUrl).toHaveBeenCalledWith(MAPLIBRE_WORKER_URL);
  });
});
