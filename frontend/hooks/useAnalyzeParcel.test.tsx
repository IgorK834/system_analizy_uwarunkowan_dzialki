import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, analyzeParcel } from "@/lib/api";
import { useAnalyzeParcel } from "@/hooks/useAnalyzeParcel";
import { buildAnalyzeResponse } from "@/test/fixtures";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, analyzeParcel: vi.fn() };
});

const analyzeParcelMock = vi.mocked(analyzeParcel);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("useAnalyzeParcel", () => {
  beforeEach(() => {
    analyzeParcelMock.mockReset();
  });

  it("udostępnia stan ładowania i zapisuje wynik", async () => {
    const pending = deferred<ReturnType<typeof buildAnalyzeResponse>>();
    analyzeParcelMock.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useAnalyzeParcel());

    let runPromise!: Promise<void>;
    act(() => {
      runPromise = result.current.run({ method: "map", lon: 19, lat: 52 });
    });
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();

    const response = buildAnalyzeResponse();
    pending.resolve(response);
    await act(async () => runPromise);

    expect(result.current).toMatchObject({
      loading: false,
      error: null,
      result: response,
    });
  });

  it("pokazuje komunikat ApiError i bezpieczny komunikat błędu nieznanego", async () => {
    analyzeParcelMock.mockRejectedValueOnce(new ApiError(503, "Serwis niedostępny"));
    const { result } = renderHook(() => useAnalyzeParcel());

    await act(async () => {
      await result.current.run({
        method: "address",
        query: "Warszawa",
        selected_lon: 21.012,
        selected_lat: 52.23,
      });
    });
    expect(result.current.error).toBe("Serwis niedostępny");

    analyzeParcelMock.mockRejectedValueOnce(new Error("sekret techniczny"));
    await act(async () => {
      await result.current.run({ method: "parcel_id", parcel_identifier: "12345" });
    });
    expect(result.current.error).toBe(
      "Wystąpił nieoczekiwany błąd podczas analizy.",
    );
  });

  it("anuluje poprzednie żądanie i zachowuje tylko najnowszy wynik", async () => {
    const first = deferred<ReturnType<typeof buildAnalyzeResponse>>();
    const second = deferred<ReturnType<typeof buildAnalyzeResponse>>();
    analyzeParcelMock
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useAnalyzeParcel());

    let firstRun!: Promise<void>;
    let secondRun!: Promise<void>;
    act(() => {
      firstRun = result.current.run({ method: "map", lon: 19, lat: 52 });
      secondRun = result.current.run({ method: "map", lon: 20, lat: 53 });
    });

    const firstSignal = analyzeParcelMock.mock.calls[0][1]?.signal;
    expect(firstSignal?.aborted).toBe(true);

    const latest = buildAnalyzeResponse({ analysis_id: 99 });
    second.resolve(latest);
    await act(async () => secondRun);

    first.reject(new DOMException("aborted", "AbortError"));
    await act(async () => firstRun);
    expect(result.current.result).toEqual(latest);
    expect(result.current.loading).toBe(false);
  });

  it("reset anuluje request i czyści stan", async () => {
    analyzeParcelMock.mockImplementation(
      (_payload, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const { result } = renderHook(() => useAnalyzeParcel());

    let runPromise!: Promise<void>;
    act(() => {
      runPromise = result.current.run({ method: "map", lon: 19, lat: 52 });
    });
    const signal = analyzeParcelMock.mock.calls[0][1]?.signal;

    act(() => result.current.reset());

    expect(signal?.aborted).toBe(true);
    expect(result.current).toMatchObject({
      loading: false,
      error: null,
      result: null,
    });
    await act(async () => runPromise);
  });

  it("anuluje trwające żądanie podczas odmontowania", async () => {
    analyzeParcelMock.mockImplementation(
      (_payload, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const { result, unmount } = renderHook(() => useAnalyzeParcel());
    let runPromise!: Promise<void>;
    act(() => {
      runPromise = result.current.run({ method: "map", lon: 19, lat: 52 });
    });
    const signal = analyzeParcelMock.mock.calls[0][1]?.signal;

    unmount();

    expect(signal?.aborted).toBe(true);
    await runPromise;
  });

  describe("odliczanie po 429 i błędy 5xx (AU-003)", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    const payload = { method: "parcel_id", parcel_identifier: "12345" } as const;

    it("odlicza czas z Retry-After co sekundę i kończy komunikatem o odnowieniu limitu", async () => {
      analyzeParcelMock.mockRejectedValueOnce(
        new ApiError(429, "Zbyt wiele żądań. Spróbuj ponownie za 3 s.", {
          code: "RATE_LIMITED",
          retryAfterSeconds: 3,
        }),
      );
      const { result } = renderHook(() => useAnalyzeParcel());

      await act(async () => {
        await result.current.run(payload);
      });
      expect(result.current.error).toBe("Zbyt wiele żądań. Spróbuj ponownie za 3 s.");
      expect(result.current.retryAfterSeconds).toBe(3);

      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(result.current.error).toBe("Zbyt wiele żądań. Spróbuj ponownie za 2 s.");

      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(result.current.retryAfterSeconds).toBe(0);
      expect(result.current.error).toBe("Limit żądań został odnowiony — możesz ponowić próbę.");

      // Odliczanie się zatrzymuje: kolejne sekundy niczego nie zmieniają.
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(result.current.retryAfterSeconds).toBe(0);
    });

    it("429 bez Retry-After zostawia statyczny komunikat bez odliczania", async () => {
      analyzeParcelMock.mockRejectedValueOnce(
        new ApiError(429, "Zbyt wiele żądań. Spróbuj ponownie za chwilę."),
      );
      const { result } = renderHook(() => useAnalyzeParcel());

      await act(async () => {
        await result.current.run(payload);
      });
      act(() => {
        vi.advanceTimersByTime(10_000);
      });

      expect(result.current.error).toBe("Zbyt wiele żądań. Spróbuj ponownie za chwilę.");
      expect(result.current.retryAfterSeconds).toBeNull();
    });

    it("kolejne uruchomienie i reset kasują odliczanie", async () => {
      analyzeParcelMock.mockRejectedValueOnce(
        new ApiError(429, "x", { retryAfterSeconds: 30 }),
      );
      const { result } = renderHook(() => useAnalyzeParcel());
      await act(async () => {
        await result.current.run(payload);
      });
      expect(result.current.retryAfterSeconds).toBe(30);

      analyzeParcelMock.mockResolvedValueOnce(buildAnalyzeResponse());
      await act(async () => {
        await result.current.run(payload);
      });
      expect(result.current).toMatchObject({ error: null, retryAfterSeconds: null });

      analyzeParcelMock.mockRejectedValueOnce(
        new ApiError(429, "x", { retryAfterSeconds: 30 }),
      );
      await act(async () => {
        await result.current.run(payload);
      });
      act(() => result.current.reset());
      expect(result.current).toMatchObject({ error: null, retryAfterSeconds: null });
    });

    it("błąd 5xx pokazuje komunikat z kodem zgłoszenia bez odliczania i bez zachęty do ponowienia", async () => {
      const message = "Błąd po stronie serwera. Kod zgłoszenia: 5d0c2f3e-6f0e-4b61-9d57-0c5c1f4a1b3e.";
      analyzeParcelMock.mockRejectedValueOnce(
        new ApiError(500, message, {
          code: "INTERNAL_ERROR",
          requestId: "5d0c2f3e-6f0e-4b61-9d57-0c5c1f4a1b3e",
        }),
      );
      const { result } = renderHook(() => useAnalyzeParcel());

      await act(async () => {
        await result.current.run(payload);
      });
      act(() => {
        vi.advanceTimersByTime(5000);
      });

      expect(result.current.error).toBe(message);
      expect(result.current.retryAfterSeconds).toBeNull();
    });

    it("komunikat błędu sieci (status 0) pozostaje komunikatem sieci", async () => {
      analyzeParcelMock.mockRejectedValueOnce(
        new ApiError(0, "Nie udało się połączyć z usługą. Sprawdź połączenie i spróbuj ponownie."),
      );
      const { result } = renderHook(() => useAnalyzeParcel());

      await act(async () => {
        await result.current.run(payload);
      });

      expect(result.current.error).toContain("Sprawdź połączenie");
    });
  });
});
