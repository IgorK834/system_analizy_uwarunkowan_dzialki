import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getApiBaseUrl,
  getKimpzpTileUrl,
  getPogWmsLayers,
  getPogWmsUrl,
} from "@/lib/config";

describe("getApiBaseUrl", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("zwraca skonfigurowany adres bez końcowych ukośników", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", " https://api.example.test/// ");

    expect(getApiBaseUrl()).toBe("https://api.example.test");
  });

  it("przerywa działanie, gdy konfiguracja jest pusta", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "   ");

    expect(() => getApiBaseUrl()).toThrow("NEXT_PUBLIC_API_BASE_URL");
  });
});

describe("getKimpzpTileUrl", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("domyślnie buduje adres proxy na bazie API", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test/");
    vi.stubEnv("NEXT_PUBLIC_KIMPZP_TILE_URL", "");

    expect(getKimpzpTileUrl()).toBe(
      "https://api.example.test/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png",
    );
  });

  it("pozwala wskazać CDN lub reverse proxy", () => {
    vi.stubEnv("NEXT_PUBLIC_KIMPZP_TILE_URL", " https://tiles.example.test/// ");

    expect(getKimpzpTileUrl()).toBe("https://tiles.example.test");
  });
});

describe("konfiguracja warstw WMS", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("zwraca konfigurowalne nazwy warstw i adres POG", () => {
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_URL", " https://pog.example.test/// ");
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_LAYERS", "strefa_planistyczna");

    expect(getPogWmsUrl()).toBe("https://pog.example.test");
    expect(getPogWmsLayers()).toBe("strefa_planistyczna");
  });

  it("zwraca bezpieczne wartości domyślne nazw warstw i null dla pustego URL POG", () => {
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_URL", "");
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_LAYERS", "");

    expect(getPogWmsUrl()).toBeNull();
    expect(getPogWmsLayers()).toBe(
      "strefaPlanistyczna,obszarUzupelnieniaZabudowy,obszarZabSrodmiejskiej,aktPlanowaniaprzestrzennego",
    );
  });
});
