"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { analyzeParcel, ApiError } from "@/lib/api";
import type { AnalyzeRequest, AnalyzeResponse } from "@/lib/types";

type RunOptions = {
  forceRefresh?: boolean;
};

export function useAnalyzeParcel() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(
    async (payload: AnalyzeRequest, options: RunOptions = {}) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      setLoading(true);
      setError(null);
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
    setResult(null);
  }, []);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
    },
    [],
  );

  return { loading, error, result, run, reset };
}
