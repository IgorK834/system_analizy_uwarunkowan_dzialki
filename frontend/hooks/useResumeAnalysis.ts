"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, resumeAnalysis } from "@/lib/api";
import type { AnalyzeResponse, AnalyzeResumeRequest } from "@/lib/types";

/**
 * Komunikaty specyficzne dla wznowienia (BK-204). Ogólne mapowanie w `api.ts`
 * opisuje 404 jako brak działki, co przy resume byłoby mylące.
 */
export function resumeErrorMessage(error: ApiError): string {
  if (error.status === 403) {
    return "Brak dostępu do tej analizy — uruchom analizę działki ponownie, aby wznowić ją z poprawnym tokenem.";
  }
  if (error.status === 404) {
    return "Analiza o tym identyfikatorze nie istnieje — uruchom analizę działki ponownie.";
  }
  if (error.status === 409) {
    return "Analiza nie czeka już na symbol strefy (mogła zostać wznowiona) — uruchom analizę ponownie, aby zobaczyć aktualny wynik.";
  }
  if (error.status === 503) {
    return `${error.message} Nic nie zapisano — analiza nadal czeka na symbol strefy.`;
  }
  return error.message;
}

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
            ? resumeErrorMessage(caught)
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
