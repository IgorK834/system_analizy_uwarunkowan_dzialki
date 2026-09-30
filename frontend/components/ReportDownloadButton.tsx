"use client";

import { useEffect, useRef, useState } from "react";

import { getAnalysisAuditPackage, getAnalysisReport } from "@/lib/api";

// Raport v2 (BK-501–503) powstaje wyłącznie z zapisanego snapshotu analizy;
// mapy są renderowane z zamrożonych danych, bez pobierania WMS.
export const REPORT_SCOPE_NOTE =
  "PDF zawiera 10 sekcji (od identyfikacji działki po ograniczenia interpretacyjne) " +
  "i powstaje wyłącznie z zapisanego snapshotu analizy — bez ponownego pobierania " +
  "źródeł. Mapy są zamrożone z chwili analizy; raport nie zawiera oceny punktowej.";

// Pakiet audytowy (BK-505): ZIP do niezależnej weryfikacji wyniku, geometrii i
// pochodzenia danych bez ponownego odpytywania źródeł.
export const AUDIT_PACKAGE_NOTE =
  "Pakiet ZIP zawiera analysis.json, sources.json, parcel.geojson (EPSG:4326), dozwolone " +
  "warstwy pochodne, README i manifest z SHA-256 każdego pliku. Powstaje wyłącznie z " +
  "zapisanego snapshotu; dane źródeł, dla których katalog nie zezwala na redystrybucję, " +
  "nie są kopiowane — w manifeście zostaje referencja, hash i powód pominięcia.";

type ReportDownloadButtonProps = {
  analysisId: number | null;
  accessToken?: string | null;
  parcelIdentifier: string | null;
};

type DownloadState = { loading: boolean; error: string | null };

function saveBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export function ReportDownloadButton({
  analysisId,
  accessToken = null,
  parcelIdentifier,
}: ReportDownloadButtonProps) {
  const [report, setReport] = useState<DownloadState>({ loading: false, error: null });
  const [audit, setAudit] = useState<DownloadState>({ loading: false, error: null });
  const [packageSha, setPackageSha] = useState<string | null>(null);
  const reportRequestRef = useRef<AbortController | null>(null);
  const auditRequestRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      reportRequestRef.current?.abort();
      reportRequestRef.current = null;
      auditRequestRef.current?.abort();
      auditRequestRef.current = null;
    },
    [],
  );

  async function run<T>(
    ref: { current: AbortController | null },
    setState: (state: DownloadState) => void,
    fallbackMessage: string,
    task: (signal: AbortSignal) => Promise<T>,
    onDone: (value: T) => void,
  ) {
    if (analysisId == null) return;

    ref.current?.abort();
    const controller = new AbortController();
    ref.current = controller;
    setState({ loading: true, error: null });
    let error: string | null = null;

    try {
      const value = await task(controller.signal);
      if (controller.signal.aborted) return;
      onDone(value);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      error = caught instanceof Error ? caught.message : fallbackMessage;
    } finally {
      if (ref.current === controller) {
        ref.current = null;
        setState({ loading: false, error });
      }
    }
  }

  function downloadReport() {
    if (analysisId == null) return;
    void run(
      reportRequestRef,
      setReport,
      "Nie udało się pobrać raportu PDF.",
      (signal) => getAnalysisReport(analysisId, { accessToken, signal }),
      (pdf) => saveBlob(pdf, `raport_analizy_${analysisId}.pdf`),
    );
  }

  function downloadAuditPackage() {
    if (analysisId == null) return;
    setPackageSha(null);
    void run(
      auditRequestRef,
      setAudit,
      "Nie udało się pobrać pakietu audytowego.",
      (signal) => getAnalysisAuditPackage(analysisId, { accessToken, signal }),
      (download) => {
        saveBlob(download.blob, `analiza_${analysisId}_pakiet_audytowy.zip`);
        setPackageSha(download.sha256);
      },
    );
  }

  return (
    <div className="report-download">
      <button
        className="primary-button report-download-button"
        type="button"
        disabled={analysisId == null || report.loading}
        onClick={downloadReport}
        aria-describedby={report.error ? "report-download-error" : undefined}
      >
        {report.loading ? "Generowanie raportu…" : "Pobierz raport PDF"}
      </button>
      {analysisId == null ? (
        <p className="report-download-note">
          Raport będzie dostępny po zapisaniu analizy działki.
        </p>
      ) : (
        <>
          <p className="report-download-note">
            Raport dla działki {parcelIdentifier ?? "aktualnie zaznaczonej"}, analiza #{analysisId}.
          </p>
          <p className="report-download-note" data-testid="report-scope-note">
            {REPORT_SCOPE_NOTE}
          </p>
        </>
      )}
      {report.error && (
        <p id="report-download-error" className="report-download-error" role="alert">
          {report.error}
        </p>
      )}
      <button
        className="secondary-button audit-package-button"
        type="button"
        disabled={analysisId == null || audit.loading}
        onClick={downloadAuditPackage}
        aria-describedby={audit.error ? "audit-package-error" : undefined}
      >
        {audit.loading ? "Przygotowanie pakietu…" : "Pobierz pakiet audytowy (ZIP)"}
      </button>
      {analysisId != null && (
        <p className="audit-package-note" data-testid="audit-package-note">
          {AUDIT_PACKAGE_NOTE}
        </p>
      )}
      {packageSha && (
        <p className="audit-package-hash" data-testid="audit-package-hash">
          Suma SHA-256 paczki (poza archiwum): <span className="mono">{packageSha}</span>
        </p>
      )}
      {audit.error && (
        <p id="audit-package-error" className="report-download-error" role="alert">
          {audit.error}
        </p>
      )}
    </div>
  );
}
