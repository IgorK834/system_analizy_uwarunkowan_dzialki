import { getApiBaseUrl } from "@/lib/config";
import type {
  AddressSearchResponse,
  AnalyzeRequest,
  AnalyzeResponse,
  AnalyzeResumeRequest,
  GeocodeResponse,
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

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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

function messageForStatus(status: number, detail: string | null): string {
  const suffix = detail ? ` ${detail}` : "";

  if (status === 422) {
    return `Nieprawidłowe dane wejściowe.${suffix}`;
  }
  if (status === 503) {
    return `Usługa analizy jest chwilowo niedostępna.${suffix}`;
  }
  if (status === 404) {
    return `Nie znaleziono działki lub adresu.${suffix}`;
  }

  return `Nie udało się wykonać żądania.${suffix}`;
}

async function requestJson<T>(url: string, options: RequestOptions): Promise<T> {
  let response: Response;

  try {
    response = await fetch(url, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(
      0,
      "Nie udało się połączyć z usługą. Sprawdź połączenie i spróbuj ponownie.",
    );
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
    throw new ApiError(
      response.status,
      messageForStatus(response.status, extractDetail(body)),
    );
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
    const message =
      response.status === 404
        ? `Nie znaleziono zapisanej analizy.${detail ? ` ${detail}` : ""}`
        : response.status === 403
          ? "Brak dostępu do raportu tej analizy. Uruchom analizę ponownie."
          : response.status === 429
            ? "Zbyt wiele żądań raportu. Spróbuj ponownie za chwilę."
            : `Nie udało się wygenerować raportu PDF.${detail ? ` ${detail}` : ""}`;
    throw new ApiError(response.status, message);
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
