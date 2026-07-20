"use client";

import { useEffect, useRef, useState } from "react";

import { getAnalysisReport } from "@/lib/api";

type ReportDownloadButtonProps = {
  analysisId: number | null;
  parcelIdentifier: string | null;
};

export function ReportDownloadButton({
  analysisId,
  parcelIdentifier,
}: ReportDownloadButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      requestRef.current?.abort();
      requestRef.current = null;
    },
    [],
  );

  async function downloadReport() {
    if (analysisId == null) return;

    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError(null);

    try {
      const pdf = await getAnalysisReport(analysisId, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;

      const objectUrl = URL.createObjectURL(pdf);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `raport_analizy_${analysisId}.pdf`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(
        caught instanceof Error
          ? caught.message
          : "Nie udało się pobrać raportu PDF.",
      );
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }

  return (
    <div className="report-download">
      <button
        className="primary-button report-download-button"
        type="button"
        disabled={analysisId == null || loading}
        onClick={downloadReport}
        aria-describedby={error ? "report-download-error" : undefined}
      >
        {loading ? "Generowanie raportu…" : "Pobierz raport PDF"}
      </button>
      {analysisId == null ? (
        <p className="report-download-note">
          Raport będzie dostępny po zapisaniu analizy działki.
        </p>
      ) : (
        <p className="report-download-note">
          Raport dla działki {parcelIdentifier ?? "aktualnie zaznaczonej"}, analiza #{analysisId}.
        </p>
      )}
      {error && (
        <p id="report-download-error" className="report-download-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
