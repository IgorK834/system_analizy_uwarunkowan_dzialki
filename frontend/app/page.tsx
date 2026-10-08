"use client";

import { useCallback, useMemo, useState } from "react";
import type * as maplibregl from "maplibre-gl";

import { ManualZonePanel } from "@/components/ManualZonePanel";
import { MapViewLoader } from "@/components/MapViewLoader";
import { PogFeatureInspector } from "@/components/PogFeatureInspector";
import { PogMapPanel, STATUS_FILTER_LABELS } from "@/components/PogMapPanel";
import { PreviewOverlays } from "@/components/PreviewOverlays";
import { ResultPanel } from "@/components/ResultPanel";
import { SearchPanel } from "@/components/SearchPanel";
import { useAnalyzeParcel } from "@/hooks/useAnalyzeParcel";
import { usePogTileActivity } from "@/hooks/usePogTileActivity";
import { usePogMapPreferences, usePogTileRelease } from "@/hooks/usePogTileRelease";
import { useResumeAnalysis } from "@/hooks/useResumeAnalysis";
import { derivePogLayerStatus } from "@/lib/pogLayerState";
import type { PogPointQuery } from "@/lib/types";

export default function HomePage() {
  const { loading, error, result, run, reset, setResult } = useAnalyzeParcel();
  const resumeAnalysis = useResumeAnalysis();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  // Wydanie POG jest pobierane raz na sesję mapy i przypina URL kafli (BK-401);
  // nowsze wydanie pojawia się tylko po jawnym ponowieniu (BK-406).
  const pogRelease = usePogTileRelease();
  const pogPreferences = usePogMapPreferences();
  const pogTiles = usePogTileActivity(map, pogRelease.release?.release_id ?? null);
  const pogLayerStatus = useMemo(
    () =>
      derivePogLayerStatus({
        release: pogRelease,
        tiles: pogTiles.tiles,
        viewport: pogTiles.viewport,
      }),
    [pogRelease, pogTiles.tiles, pogTiles.viewport],
  );
  // BK-404: kliknięcie mapy otwiera inspektor obiektu — nie uruchamia analizy.
  const [inspection, setInspection] = useState<PogPointQuery | null>(null);

  const { retry: retryRelease } = pogRelease;
  const { retry: retryTiles } = pogTiles;
  const handlePogRetry = useCallback(() => {
    retryRelease();
    retryTiles();
  }, [retryRelease, retryTiles]);

  const handleAnalyzePoint = useCallback(
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
      // Bez tokenu z wyniku backend odpowie 403, więc nie wysyłamy żądania.
      if (!result?.analysis_id || !result.access_token) return false;
      const updated = await resumeAnalysis.run({
        analysis_id: result.analysis_id,
        access_token: result.access_token,
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
        <MapViewLoader
          onMapReady={handleMapReady}
          onPogInspect={setInspection}
          pogRelease={pogRelease.release}
          pogTheme={pogPreferences.theme}
          pogStatusFilter={pogPreferences.statusFilter}
        />
        <SearchPanel loading={loading} onAnalyze={run} map={map} />
        <div className="map-controls">
          <PogMapPanel
            layerStatus={pogLayerStatus}
            theme={pogPreferences.theme}
            onThemeChange={pogPreferences.setTheme}
            statusFilter={pogPreferences.statusFilter}
            onStatusFilterChange={pogPreferences.setStatusFilter}
            onRetry={handlePogRetry}
            retrying={pogRelease.retrying}
          />
          <PreviewOverlays result={result} map={map} />
        </div>

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

        {(inspection || result) && (
          <div className="result-stack">
            {inspection && (
              <PogFeatureInspector
                query={inspection}
                layerStatus={pogLayerStatus}
                statusFilter={pogPreferences.statusFilter}
                statusFilterLabel={STATUS_FILTER_LABELS[pogPreferences.statusFilter]}
                analyzing={loading}
                onAnalyze={handleAnalyzePoint}
                onClose={() => setInspection(null)}
              />
            )}

            {result && <ResultPanel result={result} map={map} />}

            {result?.manual_zone_required && (
              <ManualZonePanel
                key={result.analysis_id ?? "manual-zone"}
                result={result}
                loading={resumeAnalysis.loading}
                error={resumeAnalysis.error}
                onSubmit={handleResumeSubmit}
              />
            )}

            {result && (
              <button
                type="button"
                className="secondary-button result-clear-button"
                onClick={reset}
              >
                Wyczyść wynik
              </button>
            )}
          </div>
        )}
      </div>
      <footer className="preview-legal-footer">
        Warstwy MPZP, POG i uzbrojenia są podglądem oficjalnych usług
        publicznych, a wektorowa mapa POG pokazuje zapisane lokalne wydanie
        danych. Brak obiektów na mapie nie potwierdza braku planu ani sieci.
      </footer>
    </main>
  );
}
