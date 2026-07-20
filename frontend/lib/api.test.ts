import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyzeParcel,
  ApiError,
  getAddressSuggestions,
} from "@/lib/api";
import { buildAnalyzeResponse } from "@/test/fixtures";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("klient API", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test/");
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("wysyła analizę jako JSON bez parametru cache, gdy opcji nie podano", async () => {
    const response = buildAnalyzeResponse();
    fetchMock.mockResolvedValue(jsonResponse(response));

    await expect(
      analyzeParcel({ method: "map", lon: 19.5, lat: 52.1 }),
    ).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/analyze",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method: "map", lon: 19.5, lat: 52.1 }),
      }),
    );
  });

  it.each([true, false])(
    "przekazuje force_refresh=%s i sygnał anulowania",
    async (forceRefresh) => {
      fetchMock.mockResolvedValue(jsonResponse(buildAnalyzeResponse()));
      const controller = new AbortController();

      await analyzeParcel(
        { method: "parcel_id", parcel_identifier: "12345" },
        { forceRefresh, signal: controller.signal },
      );

      expect(fetchMock).toHaveBeenCalledWith(
        `https://api.example.test/analyze?force_refresh=${String(forceRefresh)}`,
        expect.objectContaining({ signal: controller.signal }),
      );
    },
  );

  it("koduje zapytanie geokodowania i zwraca kontrakt sugestii", async () => {
    const geocode = {
      query: "Łódź, Piotrkowska 1",
      suggestions: [],
      total_returned: 0,
    };
    fetchMock.mockResolvedValue(jsonResponse(geocode));

    await expect(
      getAddressSuggestions("Łódź, Piotrkowska 1"),
    ).resolves.toEqual(geocode);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/geocode/suggest?q=%C5%81%C3%B3d%C5%BA%2C+Piotrkowska+1",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it.each([
    [422, { detail: [{ msg: "Pole jest wymagane" }] }, "Nieprawidłowe dane"],
    [503, { detail: { message: "Serwis ULDK nie odpowiada" } }, "chwilowo niedostępna"],
    [404, { detail: "Brak działki" }, "Nie znaleziono"],
    [500, { error: "INTERNAL" }, "Nie udało się wykonać"],
  ])(
    "mapuje odpowiedź HTTP %s na bezpieczny ApiError",
    async (status, body, expectedMessage) => {
      fetchMock.mockResolvedValue(jsonResponse(body, status as number));

      const promise = analyzeParcel({
        method: "address",
        query: "Warszawa",
      });

      await expect(promise).rejects.toMatchObject({
        name: "ApiError",
        status,
      });
      await expect(promise).rejects.toThrow(expectedMessage as string);
    },
  );

  it("zgłasza nieprawidłowy format udanej odpowiedzi", async () => {
    fetchMock.mockResolvedValue(new Response("not-json", { status: 200 }));

    await expect(
      analyzeParcel({ method: "map", lon: 19, lat: 52 }),
    ).rejects.toThrow("nieprawidłowym formacie");
  });

  it("mapuje błąd sieci, ale zachowuje AbortError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    await expect(getAddressSuggestions("Warszawa")).rejects.toEqual(
      expect.objectContaining({ status: 0 }),
    );

    const abortError = new DOMException("aborted", "AbortError");
    fetchMock.mockRejectedValueOnce(abortError);
    await expect(getAddressSuggestions("Warszawa")).rejects.toBe(abortError);
  });

  it("ApiError zachowuje status i nazwę błędu", () => {
    const error = new ApiError(418, "Test");

    expect(error).toMatchObject({ name: "ApiError", status: 418, message: "Test" });
  });
});
