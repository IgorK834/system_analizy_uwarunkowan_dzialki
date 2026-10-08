import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, resumeAnalysis } from "@/lib/api";
import { useResumeAnalysis } from "@/hooks/useResumeAnalysis";
import { buildAnalyzeResponse } from "@/test/fixtures";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, resumeAnalysis: vi.fn() };
});

const resumeAnalysisMock = vi.mocked(resumeAnalysis);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("useResumeAnalysis", () => {
  beforeEach(() => {
    resumeAnalysisMock.mockReset();
  });

  it("udostępnia stan ładowania i zwraca zaktualizowany wynik", async () => {
    const pending = deferred<ReturnType<typeof buildAnalyzeResponse>>();
    resumeAnalysisMock.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useResumeAnalysis());

    let runPromise!: Promise<ReturnType<typeof buildAnalyzeResponse> | null>;
    act(() => {
      runPromise = result.current.run({ analysis_id: 42, access_token: "token-42", zone_symbol: "230_U" });
    });
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();

    const response = buildAnalyzeResponse({ status: "complete" });
    pending.resolve(response);
    const resolved = await act(async () => runPromise);

    expect(resolved).toEqual(response);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("pokazuje komunikat ApiError i bezpieczny komunikat błędu nieznanego", async () => {
    resumeAnalysisMock.mockRejectedValueOnce(new ApiError(422, "Nieprawidłowy symbol"));
    const { result } = renderHook(() => useResumeAnalysis());

    await act(async () => {
      await result.current.run({ analysis_id: 1, access_token: "token-1", zone_symbol: "MN" });
    });
    expect(result.current.error).toBe("Nieprawidłowy symbol");

    resumeAnalysisMock.mockRejectedValueOnce(new Error("sekret techniczny"));
    await act(async () => {
      await result.current.run({ analysis_id: 1, access_token: "token-1", zone_symbol: "MN" });
    });
    expect(result.current.error).toBe(
      "Wystąpił nieoczekiwany błąd podczas wznawiania analizy.",
    );
  });

  it("reset anuluje request i czyści stan błędu/ładowania", async () => {
    resumeAnalysisMock.mockImplementation(
      (_payload, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const { result } = renderHook(() => useResumeAnalysis());

    let runPromise!: Promise<unknown>;
    act(() => {
      runPromise = result.current.run({ analysis_id: 1, access_token: "token-1", zone_symbol: "MN" });
    });
    const signal = resumeAnalysisMock.mock.calls[0][1]?.signal;

    act(() => result.current.reset());

    expect(signal?.aborted).toBe(true);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    await act(async () => runPromise);
  });

  it("anuluje trwające żądanie podczas odmontowania", async () => {
    resumeAnalysisMock.mockImplementation(
      (_payload, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const { result, unmount } = renderHook(() => useResumeAnalysis());
    let runPromise!: Promise<unknown>;
    act(() => {
      runPromise = result.current.run({ analysis_id: 1, access_token: "token-1", zone_symbol: "MN" });
    });
    const signal = resumeAnalysisMock.mock.calls[0][1]?.signal;

    unmount();

    expect(signal?.aborted).toBe(true);
    await runPromise;
  });

  it.each([
    [403, "Brak dostępu do tej analizy"],
    [404, "nie istnieje"],
    [409, "nie czeka już na symbol strefy"],
    [503, "Nic nie zapisano — analiza nadal czeka na symbol strefy."],
  ])("mapuje status %s wznowienia na jednoznaczny komunikat", async (status, text) => {
    resumeAnalysisMock.mockRejectedValueOnce(new ApiError(status, "Szczegół API."));
    const { result } = renderHook(() => useResumeAnalysis());

    await act(async () => {
      await result.current.run({ analysis_id: 1, access_token: "token-1", zone_symbol: "MN" });
    });

    expect(result.current.error).toContain(text);
  });
});
