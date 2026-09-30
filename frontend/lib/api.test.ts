import { createHash } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyzeParcel,
  ApiError,
  getAddressSuggestions,
  getActivePogTileRelease,
  getAnalysisAuditPackage,
  getAnalysisReport,
  getPogAreaSummary,
  getPogFeatureDetails,
  getPreviewSources,
  resumeAnalysis,
  searchAddresses,
} from "@/lib/api";
import { buildAnalyzeResponse } from "@/test/fixtures";
import {
  buildPogAreaSummary,
  buildPogFeatureDetails,
  buildPogRelease,
} from "@/test/pogFixtures";

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

const ZIP_BYTES = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2, 3, 4, 5, 6]);
const ZIP_SHA = createHash("sha256").update(ZIP_BYTES).digest("hex");

function zipResponse(headers: Record<string, string> = {}, body: BodyInit = ZIP_BYTES) {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/zip", ...headers },
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

  it("pobiera metadane aktywnego wydania POG, a 404 zamienia na brak wydania", async () => {
    const release = buildPogRelease();
    fetchMock.mockResolvedValueOnce(jsonResponse(release));
    await expect(getActivePogTileRelease()).resolves.toEqual(release);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/map/pog/releases/active",
      expect.objectContaining({ method: "GET" }),
    );

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Brak aktywnego wydania." }, 404));
    await expect(getActivePogTileRelease()).resolves.toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "awaria" }, 500));
    await expect(getActivePogTileRelease()).rejects.toMatchObject({ status: 500 });
  });

  it("BK-404: pobiera szczegóły obiektu z wydania, kodując identyfikator APP jako jeden segment", async () => {
    const details = buildPogFeatureDetails();
    fetchMock.mockResolvedValueOnce(jsonResponse(details));
    await expect(getPogFeatureDetails(42, "PL.ZIPPZP.10011/226401-POG/1POG-100SU")).resolves.toEqual(
      details,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/map/pog/releases/42/features/PL.ZIPPZP.10011%2F226401-POG%2F1POG-100SU",
      expect.objectContaining({ method: "GET" }),
    );
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Obiekt nie należy do wydania." }, 404));
    await expect(getPogFeatureDetails(42, "x")).rejects.toMatchObject({ status: 404 });
  });

  it("BK-405: agregat aktu albo gminy; 404 to brak agregatu, nie błąd", async () => {
    const summary = buildPogAreaSummary();
    fetchMock.mockResolvedValueOnce(jsonResponse(summary));
    await expect(getPogAreaSummary(42, { actId: "PL.ZIPPZP.10011/226401-POG/1POG" })).resolves.toEqual(
      summary,
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://api.example.test/api/v1/map/pog/releases/42/summary?act_id=PL.ZIPPZP.10011%2F226401-POG%2F1POG",
      expect.objectContaining({ method: "GET" }),
    );

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "brak" }, 404));
    await expect(getPogAreaSummary(42, { teryt: "226401", edition: "project" })).resolves.toBeNull();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://api.example.test/api/v1/map/pog/releases/42/summary?teryt=226401&edition=project",
      expect.anything(),
    );

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "awaria" }, 500));
    await expect(getPogAreaSummary(42, { actId: "A" })).rejects.toMatchObject({ status: 500 });
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

  describe("pakiet audytowy (BK-505)", () => {
    it("pobiera ZIP z tokenem, zwraca sumę z nagłówka i wersję eksportera", async () => {
      fetchMock.mockResolvedValue(
        zipResponse({
          "X-Audit-Package-SHA256": ZIP_SHA,
          "X-Audit-Exporter-Version": "audit-exporter/1.0.0",
        }),
      );
      const controller = new AbortController();

      const download = await getAnalysisAuditPackage(42, {
        accessToken: "tok/en+1",
        signal: controller.signal,
      });

      expect(download.blob.size).toBe(ZIP_BYTES.length);
      expect(download.sha256).toBe(ZIP_SHA);
      expect(download.exporterVersion).toBe("audit-exporter/1.0.0");
      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.test/report/42/audit.zip?access_token=tok%2Fen%2B1",
        expect.objectContaining({
          method: "GET",
          headers: { Accept: "application/zip" },
          signal: controller.signal,
        }),
      );
    });

    it("pobiera bez tokenu i liczy sumę lokalnie, gdy serwer jej nie podał", async () => {
      fetchMock.mockResolvedValue(zipResponse());

      const download = await getAnalysisAuditPackage(7);

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.test/report/7/audit.zip",
        expect.anything(),
      );
      expect(download.sha256).toBe(ZIP_SHA);
      expect(download.exporterVersion).toBeNull();
    });

    it("odrzuca pakiet, którego suma różni się od sumy serwera", async () => {
      fetchMock.mockResolvedValue(zipResponse({ "X-Audit-Package-SHA256": "0".repeat(64) }));

      await expect(getAnalysisAuditPackage(42)).rejects.toThrow("różni się od sumy podanej");
    });

    it("bez SubtleCrypto zwraca sumę z nagłówka bez weryfikacji lokalnej", async () => {
      vi.stubGlobal("crypto", undefined);
      fetchMock.mockResolvedValue(zipResponse({ "X-Audit-Package-SHA256": ZIP_SHA.toUpperCase() }));

      const download = await getAnalysisAuditPackage(42);
      expect(download.sha256).toBe(ZIP_SHA);

      fetchMock.mockResolvedValue(zipResponse());
      expect((await getAnalysisAuditPackage(42)).sha256).toBeNull();
    });

    it("mapuje błędy HTTP na czytelne komunikaty", async () => {
      const cases: Array<[number, RegExp]> = [
        [403, /Brak dostępu do pakietu audytowego/],
        [404, /Nie znaleziono zapisanej analizy/],
        [413, /przekracza dopuszczalny rozmiar/],
        [429, /Zbyt wiele żądań pakietu/],
        [500, /Nie udało się przygotować pakietu audytowego/],
      ];
      for (const [status, message] of cases) {
        fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "x" }, status));
        await expect(getAnalysisAuditPackage(42)).rejects.toMatchObject({
          status,
          message: expect.stringMatching(message),
        });
      }
      fetchMock.mockResolvedValueOnce(new Response("nie json", { status: 500 }));
      await expect(getAnalysisAuditPackage(42)).rejects.toThrow("przygotować pakietu");
    });

    it("odrzuca odpowiedź, która nie jest ZIP-em", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ok" }));
      await expect(getAnalysisAuditPackage(42)).rejects.toThrow("nieprawidłowym formacie");

      fetchMock.mockResolvedValueOnce(zipResponse({}, "to nie jest zip"));
      await expect(getAnalysisAuditPackage(42)).rejects.toThrow("nie jest prawidłowym archiwum ZIP");

      fetchMock.mockResolvedValueOnce(zipResponse({}, new Uint8Array()));
      await expect(getAnalysisAuditPackage(42)).rejects.toThrow("nie jest prawidłowym archiwum ZIP");
    });

    it("mapuje awarię sieci i zachowuje AbortError", async () => {
      fetchMock.mockRejectedValueOnce(new TypeError("network down"));
      await expect(getAnalysisAuditPackage(42)).rejects.toMatchObject({ status: 0 });

      const abortError = new DOMException("aborted", "AbortError");
      fetchMock.mockRejectedValueOnce(abortError);
      await expect(getAnalysisAuditPackage(42)).rejects.toBe(abortError);
    });
  });
});
