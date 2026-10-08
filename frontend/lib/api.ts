import { getApiBaseUrl } from "@/lib/config";
import type {
  AddressSearchResponse,
  AnalyzeRequest,
  AnalyzeResponse,
  AnalyzeResumeRequest,
  GeocodeResponse,
  PogAreaSummary,
  PogAreaSummaryScope,
  PogFeatureDetails,
  PogTileRelease,
  PreviewSource,
} from "@/lib/types";

type AnalyzeOptions = {
  forceRefresh?: boolean;
  signal?: AbortSignal;
};

type RequestOptions = RequestInit & {
  signal?: AbortSignal;
};

/** Metadane błędu HTTP przekazywane z odpowiedzi serwera (kontrakt `ErrorResponse`, AU-003). */
export type ApiErrorDetails = {
  /** Kod błędu API, np. `INTERNAL_ERROR`, `PERSISTENCE_FAILED`, `RATE_LIMITED`. */
  code?: string | null;
  /** Identyfikator żądania (`request_id`/`X-Request-ID`) do zgłoszenia operatorowi. */
  requestId?: string | null;
  /** Czas oczekiwania z nagłówka `Retry-After` (429), w sekundach. */
  retryAfterSeconds?: number | null;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, details: ApiErrorDetails = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = details.code ?? null;
    this.requestId = details.requestId ?? null;
    this.retryAfterSeconds = details.retryAfterSeconds ?? null;
  }
}

function extractDetail(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const detail = extractDetail(item);
      if (detail) return detail;
    }
    return null;
  }

  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return (
      extractDetail(object.detail) ??
      extractDetail(object.message) ??
      extractDetail(object.msg)
    );
  }

  return null;
}

const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$/;

function extractString(body: unknown, key: string): string | null {
  if (body && typeof body === "object" && !Array.isArray(body)) {
    const value = (body as Record<string, unknown>)[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

/** Identyfikator żądania z ciała `ErrorResponse` albo nagłówka `X-Request-ID` (odrzuca śmieci). */
function extractRequestId(body: unknown, response: Response): string | null {
  for (const candidate of [
    extractString(body, "request_id"),
    response.headers.get("x-request-id"),
  ]) {
    if (candidate && REQUEST_ID_PATTERN.test(candidate.trim())) return candidate.trim();
  }
  return null;
}

/** `Retry-After` jako liczba sekund albo data HTTP; `null`, gdy brak lub nieczytelny. */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number.parseInt(trimmed, 10);
  // Data HTTP zawiera nazwy dni i miesięcy; liczby z innymi znakami (np. „-5”) nie są poprawnym czasem.
  if (!/[A-Za-z]/.test(trimmed)) return null;
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

/** Komunikat 429 z odliczaniem; używany przez `api.ts` i hook (aktualizacja co sekundę). */
export function rateLimitMessage(retryAfterSeconds: number | null): string {
  if (retryAfterSeconds === null) {
    return "Zbyt wiele żądań. Spróbuj ponownie za chwilę.";
  }
  return retryAfterSeconds > 0
    ? `Zbyt wiele żądań. Spróbuj ponownie za ${retryAfterSeconds} s.`
    : "Limit żądań został odnowiony — możesz ponowić próbę.";
}

/**
 * Dopisuje kod zgłoszenia do komunikatu błędu serwera. Komunikaty 5xx nie zachęcają do ponawiania:
 * żądanie zwykle padnie ponownie i zużyje limit.
 */
function withRequestId(message: string, requestId: string | null): string {
  return requestId ? `${message} Kod zgłoszenia: ${requestId}.` : message;
}

function messageForStatus(
  status: number,
  detail: string | null,
  meta: ApiErrorDetails = {},
): string {
  const suffix = detail ? ` ${detail}` : "";
  const requestId = meta.requestId ?? null;

  if (status === 0) {
    return "Nie udało się połączyć z usługą. Sprawdź połączenie i spróbuj ponownie.";
  }
  if (status === 422) {
    return `Nieprawidłowe dane wejściowe.${suffix}`;
  }
  if (status === 404) {
    return `Nie znaleziono działki lub adresu.${suffix}`;
  }
  if (status === 429) {
    return rateLimitMessage(meta.retryAfterSeconds ?? null);
  }
  if (meta.code === "PERSISTENCE_FAILED") {
    return withRequestId("Nie udało się zapisać wyniku analizy.", requestId);
  }
  if (status === 502) {
    return withRequestId(
      "Zewnętrzne źródło danych zwróciło odpowiedź, której nie da się odczytać.",
      requestId,
    );
  }
  if (status === 503 || status === 504) {
    return withRequestId(`Usługa analizy jest chwilowo niedostępna.${suffix}`, requestId);
  }
  if (status >= 500) {
    // Treść błędu serwera jest ogólna i dla użytkownika bezużyteczna — liczy się kod zgłoszenia.
    return withRequestId("Błąd po stronie serwera.", requestId);
  }

  return `Nie udało się wykonać żądania.${suffix}`;
}

function buildHttpError(response: Response, body: unknown): ApiError {
  const details: ApiErrorDetails = {
    code: extractString(body, "error"),
    requestId: extractRequestId(body, response),
    retryAfterSeconds:
      response.status === 429 ? parseRetryAfter(response.headers.get("retry-after")) : null,
  };
  return new ApiError(
    response.status,
    messageForStatus(response.status, extractDetail(body), details),
    details,
  );
}

async function requestJson<T>(url: string, options: RequestOptions): Promise<T> {
  let response: Response;

  try {
    response = await fetch(url, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    // Odpowiedzi 5xx mają nagłówki CORS (AU-003), więc odrzucony `fetch` oznacza naprawdę brak sieci.
    throw new ApiError(0, messageForStatus(0, null));
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    if (response.ok) {
      throw new ApiError(
        response.status,
        "Serwer zwrócił odpowiedź w nieprawidłowym formacie.",
      );
    }
  }

  if (!response.ok) {
    throw buildHttpError(response, body);
  }

  return body as T;
}

export async function analyzeParcel(
  payload: AnalyzeRequest,
  options: AnalyzeOptions = {},
): Promise<AnalyzeResponse> {
  const forceRefreshQuery =
    options.forceRefresh === undefined
      ? ""
      : `?force_refresh=${String(options.forceRefresh)}`;

  return requestJson<AnalyzeResponse>(
    `${getApiBaseUrl()}/analyze${forceRefreshQuery}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: options.signal,
    },
  );
}

export async function resumeAnalysis(
  payload: AnalyzeResumeRequest,
  options: { signal?: AbortSignal } = {},
): Promise<AnalyzeResponse> {
  return requestJson<AnalyzeResponse>(`${getApiBaseUrl()}/analyze/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: options.signal,
  });
}

export async function getPreviewSources(
  options: { signal?: AbortSignal } = {},
): Promise<PreviewSource[]> {
  return requestJson<PreviewSource[]>(
    `${getApiBaseUrl()}/api/v1/map/preview-sources`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
}

/**
 * Metadane aktywnego lokalnego wydania POG (BK-401) z URL-em kafli przypiętym
 * do `release_id`. Zwraca `null`, gdy backend nie ma aktywnego wydania (404) —
 * to brak lokalnych danych, a nie brak planu ogólnego w gminie.
 */
export async function getActivePogTileRelease(
  options: { signal?: AbortSignal } = {},
): Promise<PogTileRelease | null> {
  try {
    return await requestJson<PogTileRelease>(
      `${getApiBaseUrl()}/api/v1/map/pog/releases/active`,
      { method: "GET", signal: options.signal },
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * Szczegóły obiektu POG, których kafel MVT nie mieści (BK-404) — przypięte do
 * wydania, z którego pochodzi kliknięta cecha. Identyfikator APP zawiera
 * ukośniki, więc jest kodowany jako jeden segment ścieżki.
 */
export async function getPogFeatureDetails(
  releaseId: number,
  featureId: string,
  options: { signal?: AbortSignal } = {},
): Promise<PogFeatureDetails> {
  return requestJson<PogFeatureDetails>(
    `${getApiBaseUrl()}/api/v1/map/pog/releases/${releaseId}/features/${encodeURIComponent(featureId)}`,
    { method: "GET", signal: options.signal },
  );
}

/**
 * Gotowy agregat powierzchniowy stref aktu albo gminy (BK-405) policzony przy
 * imporcie wydania. Zwraca `null`, gdy agregatu nie ma (404) — np. wydanie
 * sprzed BK-405 — co nie oznacza braku stref ani planu.
 */
export async function getPogAreaSummary(
  releaseId: number,
  scope: PogAreaSummaryScope,
  options: { signal?: AbortSignal } = {},
): Promise<PogAreaSummary | null> {
  const parameters = new URLSearchParams(
    "actId" in scope
      ? { act_id: scope.actId }
      : { teryt: scope.teryt, edition: scope.edition },
  );
  try {
    return await requestJson<PogAreaSummary>(
      `${getApiBaseUrl()}/api/v1/map/pog/releases/${releaseId}/summary?${parameters.toString()}`,
      { method: "GET", signal: options.signal },
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function getAnalysisReport(
  analysisId: number,
  options: { accessToken?: string | null; signal?: AbortSignal } = {},
): Promise<Blob> {
  let response: Response;
  const query = options.accessToken
    ? `?access_token=${encodeURIComponent(options.accessToken)}`
    : "";

  try {
    response = await fetch(`${getApiBaseUrl()}/report/${analysisId}${query}`, {
      method: "GET",
      headers: { Accept: "application/pdf" },
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(
      0,
      "Nie udało się połączyć z usługą raportów. Spróbuj ponownie.",
    );
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Błąd HTTP bez JSON nadal mapujemy na bezpieczny komunikat poniżej.
    }
    const detail = extractDetail(body);
    const failure = buildHttpError(response, body);
    const message =
      response.status === 404
        ? `Nie znaleziono zapisanej analizy.${detail ? ` ${detail}` : ""}`
        : response.status === 403
          ? "Brak dostępu do raportu tej analizy. Uruchom analizę ponownie."
          : response.status === 429
            ? "Zbyt wiele żądań raportu. Spróbuj ponownie za chwilę."
            : response.status >= 500
              ? withRequestId("Nie udało się wygenerować raportu PDF.", failure.requestId)
              : `Nie udało się wygenerować raportu PDF.${detail ? ` ${detail}` : ""}`;
    throw new ApiError(response.status, message, failure);
  }

  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("application/pdf")) {
    throw new ApiError(
      response.status,
      "Serwer zwrócił raport w nieprawidłowym formacie.",
    );
  }

  const blob = await response.blob();
  const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  if (
    blob.size === 0 ||
    signature.length !== 4 ||
    signature[0] !== 0x25 ||
    signature[1] !== 0x50 ||
    signature[2] !== 0x44 ||
    signature[3] !== 0x46
  ) {
    throw new ApiError(
      response.status,
      "Wygenerowany plik nie jest prawidłowym dokumentem PDF.",
    );
  }

  return blob;
}

export type AuditPackageDownload = {
  blob: Blob;
  /** SHA-256 całej paczki podany przez serwer poza archiwum (nagłówek). */
  sha256: string | null;
  exporterVersion: string | null;
};

async function sha256Hex(blob: Blob): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  const digest = await subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Pobiera pakiet audytowy zapisanej analizy (BK-505): ZIP z analysis.json,
 * sources.json, GeoJSON, README i manifestem SHA-256. Suma paczki z nagłówka jest
 * porównywana z sumą pobranych bajtów, gdy przeglądarka udostępnia SubtleCrypto.
 */
export async function getAnalysisAuditPackage(
  analysisId: number,
  options: { accessToken?: string | null; signal?: AbortSignal } = {},
): Promise<AuditPackageDownload> {
  let response: Response;
  const query = options.accessToken
    ? `?access_token=${encodeURIComponent(options.accessToken)}`
    : "";

  try {
    response = await fetch(`${getApiBaseUrl()}/report/${analysisId}/audit.zip${query}`, {
      method: "GET",
      headers: { Accept: "application/zip" },
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(
      0,
      "Nie udało się połączyć z usługą pakietów audytowych. Spróbuj ponownie.",
    );
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Błąd HTTP bez JSON nadal mapujemy na bezpieczny komunikat poniżej.
    }
    const detail = extractDetail(body);
    const failure = buildHttpError(response, body);
    const message =
      response.status === 404
        ? `Nie znaleziono zapisanej analizy.${detail ? ` ${detail}` : ""}`
        : response.status === 403
          ? "Brak dostępu do pakietu audytowego tej analizy. Uruchom analizę ponownie."
          : response.status === 413
            ? "Pakiet audytowy tej analizy przekracza dopuszczalny rozmiar."
            : response.status === 429
              ? "Zbyt wiele żądań pakietu. Spróbuj ponownie za chwilę."
              : response.status >= 500
                ? withRequestId("Nie udało się przygotować pakietu audytowego.", failure.requestId)
                : `Nie udało się przygotować pakietu audytowego.${detail ? ` ${detail}` : ""}`;
    throw new ApiError(response.status, message, failure);
  }

  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("application/zip")) {
    throw new ApiError(
      response.status,
      "Serwer zwrócił pakiet audytowy w nieprawidłowym formacie.",
    );
  }

  const blob = await response.blob();
  const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  if (
    blob.size === 0 ||
    signature.length !== 4 ||
    signature[0] !== 0x50 ||
    signature[1] !== 0x4b ||
    signature[2] !== 0x03 ||
    signature[3] !== 0x04
  ) {
    throw new ApiError(
      response.status,
      "Pobrany plik nie jest prawidłowym archiwum ZIP.",
    );
  }

  const declared = response.headers.get("x-audit-package-sha256")?.toLowerCase() ?? null;
  const actual = await sha256Hex(blob);
  if (declared && actual && declared !== actual) {
    throw new ApiError(
      response.status,
      "Suma SHA-256 pobranego pakietu różni się od sumy podanej przez serwer — pobierz pakiet ponownie.",
    );
  }
  return {
    blob,
    sha256: declared ?? actual,
    exporterVersion: response.headers.get("x-audit-exporter-version"),
  };
}

export async function getAddressSuggestions(
  query: string,
  options: { signal?: AbortSignal } = {},
): Promise<GeocodeResponse> {
  const parameters = new URLSearchParams({ q: query });
  return requestJson<GeocodeResponse>(
    `${getApiBaseUrl()}/geocode/suggest?${parameters.toString()}`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
}

/**
 * Wyszukiwarka adresów (Faza 11.1). Zwraca wyniki z punktem GeoJSON w WGS84 i
 * stabilnym identyfikatorem, dzięki czemu analiza używa dokładnie wybranej
 * sugestii zamiast ponownego geokodowania tekstu.
 */
export async function searchAddresses(
  query: string,
  options: {
    signal?: AbortSignal;
    limit?: number;
    bias?: { lon: number; lat: number };
    bbox?: [number, number, number, number];
  } = {},
): Promise<AddressSearchResponse> {
  const parameters = new URLSearchParams({ q: query });
  if (options.limit !== undefined) {
    parameters.set("limit", String(options.limit));
  }
  if (options.bias) {
    parameters.set("bias_lon", String(options.bias.lon));
    parameters.set("bias_lat", String(options.bias.lat));
  }
  if (options.bbox) {
    parameters.set("bbox", options.bbox.join(","));
  }
  return requestJson<AddressSearchResponse>(
    `${getApiBaseUrl()}/api/v1/search/addresses?${parameters.toString()}`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
}
