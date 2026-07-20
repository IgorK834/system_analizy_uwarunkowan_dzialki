"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, resumeAnalysis } from "@/lib/api";
import type { AnalyzeResponse, AnalyzeResumeRequest } from "@/lib/types";

export function useResumeAnalysis() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(
    async (payload: AnalyzeResumeRequest): Promise<AnalyzeResponse | null> => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      setLoading(true);
      setError(null);

      try {
        const response = await resumeAnalysis(payload, {
          signal: controller.signal,
        });
        return controllerRef.current === controller ? response : null;
      } catch (caught) {
        if (controllerRef.current !== controller) return null;
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return null;
        }

        setError(
          caught instanceof ApiError
            ? caught.message
            : "Wystąpił nieoczekiwany błąd podczas wznawiania analizy.",
        );
        return null;
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
  }, []);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
    },
    [],
  );

  return { loading, error, run, reset };
}
