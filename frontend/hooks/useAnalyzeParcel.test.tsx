import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
      await result.current.run({ method: "address", query: "Warszawa" });
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
});
