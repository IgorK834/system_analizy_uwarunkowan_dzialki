import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyzeParcel,
  ApiError,
  getAddressSuggestions,
  getAnalysisReport,
  getPreviewSources,
  resumeAnalysis,
  searchAddresses,
} from "@/lib/api";
import { buildAnalyzeResponse } from "@/test/fixtures";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pdfResponse(content = "%PDF-1.7 test") {
  return new Response(content, {
    status: 200,
    headers: { "Content-Type": "application/pdf" },
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

  it("wysyła wznowienie analizy jako JSON do /analyze/resume", async () => {
    const response = buildAnalyzeResponse({ status: "complete" });
    fetchMock.mockResolvedValue(jsonResponse(response));

    await expect(
      resumeAnalysis({ analysis_id: 42, zone_symbol: "230_U" }),
    ).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/analyze/resume",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis_id: 42, zone_symbol: "230_U" }),
      }),
    );
  });

  it("pobiera rejestr źródeł podglądowych z własnego backendu", async () => {
    const sources = [
      {
        source_key: "kiut",
        label: "Uzbrojenie terenu",
        attribution: "KIUT, GUGiK",
        min_zoom: 17,
        max_zoom: 20,
        tile_size: 512,
        tile_url_template: "/api/v1/map/tiles/kiut/{z}/{x}/{y}.png",
        legal_note: "Podgląd poglądowy.",
        info_url: "https://example.test/kiut",
        catalog_status: "production",
      },
    ];
    fetchMock.mockResolvedValue(jsonResponse(sources));
    const controller = new AbortController();

    await expect(
      getPreviewSources({ signal: controller.signal }),
    ).resolves.toEqual(sources);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/map/preview-sources",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
  });

  it("pobiera i weryfikuje raport PDF zapisanej analizy", async () => {
    fetchMock.mockResolvedValue(pdfResponse());
    const controller = new AbortController();

    const report = await getAnalysisReport(42, {
      accessToken: "tok/en+1",
      signal: controller.signal,
    });

    expect(report.type).toBe("application/pdf");
    expect(report.size).toBeGreaterThan(4);
    expect(new TextDecoder().decode(await report.slice(0, 4).arrayBuffer())).toBe(
      "%PDF",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/report/42?access_token=tok%2Fen%2B1",
      expect.objectContaining({
        method: "GET",
        headers: { Accept: "application/pdf" },
        signal: controller.signal,
      }),
    );
  });

  it("pobiera raport bez tokenu, gdy analiza go nie zwróciła", async () => {
    fetchMock.mockResolvedValue(pdfResponse());

    await getAnalysisReport(42);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/report/42",
      expect.anything(),
    );
  });

  it("mapuje 403 i 429 raportu na czytelne komunikaty", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "x" }, 403));
    await expect(getAnalysisReport(42)).rejects.toThrow("Brak dostępu");

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "x" }, 429));
    await expect(getAnalysisReport(42)).rejects.toThrow("Zbyt wiele żądań");
  });

  it("odrzuca udaną odpowiedź raportu, która nie jest PDF", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok" }));

    await expect(getAnalysisReport(42)).rejects.toThrow(
      "nieprawidłowym formacie",
    );
  });

  it("odrzuca raport bez sygnatury %PDF", async () => {
    fetchMock.mockResolvedValue(
      new Response("not-a-pdf", {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    );

    await expect(getAnalysisReport(42)).rejects.toThrow(
      "nie jest prawidłowym dokumentem PDF",
    );
  });

  it("mapuje brak zapisanej analizy na błąd raportu 404", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "Analiza nie istnieje." }, 404),
    );

    await expect(getAnalysisReport(404)).rejects.toMatchObject({
      status: 404,
      message: expect.stringContaining("Nie znaleziono zapisanej analizy"),
    });
  });

  it("mapuje awarię sieci raportu i zachowuje jego AbortError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    await expect(getAnalysisReport(42)).rejects.toMatchObject({ status: 0 });

    const abortError = new DOMException("aborted", "AbortError");
    fetchMock.mockRejectedValueOnce(abortError);
    await expect(getAnalysisReport(42)).rejects.toBe(abortError);
  });

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
        selected_lon: 21.012,
        selected_lat: 52.23,
      });

      await expect(promise).rejects.toMatchObject({
        name: "ApiError",
        status,
      });
      await expect(promise).rejects.toThrow(expectedMessage as string);
    },
  );

  it("wyszukuje adresy przez /api/v1/search/addresses i zwraca wyniki", async () => {
    const payload = {
      query: "Marki",
      results: [
        {
          id: "hash:abc",
          label: "Marki, Andersa 1",
          match_ranges: [],
          point: { type: "Point", coordinates: [21.1, 52.32] },
          address_parts: {
            country: "Polska",
            voivodeship: null,
            county: null,
            municipality: null,
            city: "Marki",
            street: "Andersa",
            house_number: "1",
          },
          result_type: "house_number",
          confidence: 0.9,
          source: { source_id: "emuia_uug", attribution: "GUGiK / EMUiA" },
        },
      ],
      total_returned: 1,
    };
    fetchMock.mockResolvedValue(jsonResponse(payload));

    const result = await searchAddresses("Marki", {
      limit: 5,
      bias: { lon: 21.1, lat: 52.3 },
      bbox: [21, 52, 21.2, 52.4],
    });

    expect(result.total_returned).toBe(1);
    expect(result.results[0].id).toBe("hash:abc");
    const requestedUrl = String(fetchMock.mock.calls[0][0]);
    expect(requestedUrl).toContain("/api/v1/search/addresses");
    expect(requestedUrl).toContain("q=Marki");
    expect(requestedUrl).toContain("limit=5");
    expect(requestedUrl).toContain("bias_lon=21.1");
    expect(requestedUrl).toContain("bias_lat=52.3");
    expect(requestedUrl).toContain("bbox=21%2C52%2C21.2%2C52.4");
  });

  it("mapuje błąd wyszukiwarki adresów na ApiError", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: "SOURCE_UNAVAILABLE", detail: "x" }, 503));
    await expect(searchAddresses("Marki")).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
    });
  });

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
