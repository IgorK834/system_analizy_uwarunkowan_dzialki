import { afterEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl, getApiResourceUrl } from "@/lib/config";

describe("konfiguracja API", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("zwraca skonfigurowany adres bez końcowych ukośników", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", " https://api.example.test/// ");

    expect(getApiBaseUrl()).toBe("https://api.example.test");
  });

  it("przerywa działanie, gdy konfiguracja jest pusta", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "   ");

    expect(() => getApiBaseUrl()).toThrow("NEXT_PUBLIC_API_BASE_URL");
  });

  it("buduje URL kafla wyłącznie względem własnego API", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test/");

    expect(
      getApiResourceUrl("/api/v1/map/tiles/kiut/{z}/{x}/{y}.png"),
    ).toBe("https://api.example.test/api/v1/map/tiles/kiut/{z}/{x}/{y}.png");
  });

  it("odrzuca ścieżkę bez początkowego ukośnika", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");

    expect(() => getApiResourceUrl("https://upstream.example.test/wms")).toThrow(
      "musi zaczynać się od ukośnika",
    );
  });
});
