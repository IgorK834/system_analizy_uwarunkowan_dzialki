"use client";

import { useCallback, useState } from "react";
import type maplibregl from "maplibre-gl";

import { ManualZonePanel } from "@/components/ManualZonePanel";
import { MapViewLoader } from "@/components/MapViewLoader";
import { PreviewOverlays } from "@/components/PreviewOverlays";
import { ResultPanel } from "@/components/ResultPanel";
import { SearchPanel } from "@/components/SearchPanel";
import { useAnalyzeParcel } from "@/hooks/useAnalyzeParcel";
import { useResumeAnalysis } from "@/hooks/useResumeAnalysis";

export default function HomePage() {
  const { loading, error, result, run, reset, setResult } = useAnalyzeParcel();
  const resumeAnalysis = useResumeAnalysis();
  const [map, setMap] = useState<maplibregl.Map | null>(null);

  const handleMapClick = useCallback(
    (lon: number, lat: number) => {
      void run({ method: "map", lon, lat });
    },
    [run],
  );

  const handleMapReady = useCallback((instance: maplibregl.Map) => {
    setMap(instance);
  }, []);

  const handleResumeSubmit = useCallback(
    async (zoneSymbol: string): Promise<boolean> => {
      if (!result?.analysis_id) return false;
      const updated = await resumeAnalysis.run({
        analysis_id: result.analysis_id,
        zone_symbol: zoneSymbol,
      });
      if (updated) setResult(updated);
      return Boolean(updated);
    },
    [result, resumeAnalysis, setResult],
  );

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <span className="eyebrow">Analiza przestrzenna</span>
          <h1>Uwarunkowania działki</h1>
        </div>
        <p>Wynik ma charakter informacyjny i wymaga weryfikacji.</p>
      </header>

      <div className="workspace">
        <MapViewLoader onMapClick={handleMapClick} onMapReady={handleMapReady} />
        <SearchPanel loading={loading} onAnalyze={run} map={map} />
        <PreviewOverlays result={result} map={map} />

        {loading && (
          <div className="analysis-card analysis-loading" role="status">
            <span className="spinner" aria-hidden="true" />
            Trwa analiza działki…
          </div>
        )}

        {error && (
          <div className="analysis-card analysis-error" role="alert">
            <strong>Analiza nie powiodła się</strong>
            <p>{error}</p>
            <button type="button" className="secondary-button" onClick={reset}>
              Zamknij
            </button>
          </div>
        )}

        {result && (
          <div className="result-stack">
            <ResultPanel result={result} map={map} />

            {result.manual_zone_required && (
              <ManualZonePanel
                key={result.analysis_id ?? "manual-zone"}
                result={result}
                loading={resumeAnalysis.loading}
                error={resumeAnalysis.error}
                onSubmit={handleResumeSubmit}
              />
            )}

            <button
              type="button"
              className="secondary-button result-clear-button"
              onClick={reset}
            >
              Wyczyść wynik
            </button>
          </div>
        )}
      </div>
      <footer className="preview-legal-footer">
        Warstwy MPZP, POG i uzbrojenia są podglądem oficjalnych usług
        publicznych. Brak obiektów na mapie nie potwierdza braku planu ani sieci.
      </footer>
    </main>
  );
}
