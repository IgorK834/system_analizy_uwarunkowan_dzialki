import { afterEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@/lib/config";

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
