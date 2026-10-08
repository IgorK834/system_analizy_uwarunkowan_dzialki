"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { analyzeParcel, ApiError, rateLimitMessage } from "@/lib/api";
import type { AnalyzeRequest, AnalyzeResponse } from "@/lib/types";

type RunOptions = {
  forceRefresh?: boolean;
};

export function useAnalyzeParcel() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Pozostały czas do odnowienia limitu po 429 (z nagłówka `Retry-After`); `null` poza 429.
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(
    async (payload: AnalyzeRequest, options: RunOptions = {}) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      setLoading(true);
      setError(null);
      setRetryAfter(null);
      setResult(null);

      try {
        const response = await analyzeParcel(payload, {
          forceRefresh: options.forceRefresh,
          signal: controller.signal,
        });

        if (controllerRef.current === controller) {
          setResult(response);
        }
      } catch (caught) {
        if (controllerRef.current !== controller) return;
        if (caught instanceof DOMException && caught.name === "AbortError") return;

        setError(
          caught instanceof ApiError
            ? caught.message
            : "Wystąpił nieoczekiwany błąd podczas analizy.",
        );
        setRetryAfter(
          caught instanceof ApiError && caught.status === 429
            ? caught.retryAfterSeconds
            : null,
        );
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
          setLoading(false);
        }
      }
    },
    [],
  );

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setLoading(false);
    setError(null);
    setRetryAfter(null);
    setResult(null);
  }, []);

  // Odliczanie do odnowienia limitu: komunikat 429 aktualizuje się co sekundę. Interwał żyje, dopóki
  // zostały sekundy (efekt zależy od flagi, nie od licznika), więc tykanie nie odtwarza timera.
  const counting = retryAfter !== null && retryAfter > 0;
  useEffect(() => {
    if (!counting) return;
    const timer = window.setInterval(
      () => setRetryAfter((current) => (current === null ? null : Math.max(0, current - 1))),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [counting]);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
    },
    [],
  );

  const visibleError =
    error !== null && retryAfter !== null ? rateLimitMessage(retryAfter) : error;

  return {
    loading,
    error: visibleError,
    retryAfterSeconds: retryAfter,
    result,
    run,
    reset,
    setResult,
  };
}
