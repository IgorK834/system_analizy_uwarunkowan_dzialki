import { getApiBaseUrl } from "@/lib/config";
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  GeocodeResponse,
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
