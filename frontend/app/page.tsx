"use client";

import { useCallback, useState, type FormEvent } from "react";
import type maplibregl from "maplibre-gl";

import { MapViewLoader } from "@/components/MapViewLoader";
import { PlanningOverlay } from "@/components/PlanningOverlay";
import { ResultPanel } from "@/components/ResultPanel";
import { SearchPanel } from "@/components/SearchPanel";
import { useAnalyzeParcel } from "@/hooks/useAnalyzeParcel";
import { useResumeAnalysis } from "@/hooks/useResumeAnalysis";

const ZONE_SYMBOL_MAX_LENGTH = 20;

export default function HomePage() {
  const { loading, error, result, run, reset, setResult } = useAnalyzeParcel();
  const resumeAnalysis = useResumeAnalysis();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const [zoneSymbolInput, setZoneSymbolInput] = useState("");
  const [zoneSymbolError, setZoneSymbolError] = useState<string | null>(null);

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
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!result?.analysis_id) {
        // Niespójny stan backendu: manual_zone_required=true bez analysis_id
        // nie powinien wystąpić (save_analysis zawsze zapisuje analysis_id
        // dla waiting_for_user_input), ale nie zakładamy tego cicho.
        setZoneSymbolError(
          "Brak identyfikatora analizy — nie można wznowić analizy. Uruchom analizę ponownie.",
        );
        return;
      }

      const trimmed = zoneSymbolInput.trim();
      if (!trimmed || trimmed.length > ZONE_SYMBOL_MAX_LENGTH) {
        setZoneSymbolError(
          `Podaj symbol strefy (1–${ZONE_SYMBOL_MAX_LENGTH} znaków).`,
        );
        return;
      }

      setZoneSymbolError(null);
      const updated = await resumeAnalysis.run({
        analysis_id: result.analysis_id,
        zone_symbol: trimmed,
      });
      if (updated) {
        setResult(updated);
        setZoneSymbolInput("");
      }
    },
    [result, zoneSymbolInput, resumeAnalysis, setResult],
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
        <SearchPanel loading={loading} onAnalyze={run} />
        <PlanningOverlay result={result} map={map} />

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
              <form
                className="analysis-card manual-zone-form"
                onSubmit={handleResumeSubmit}
                aria-label="Ręczne podanie symbolu strefy MPZP"
              >
                <h3>Ręczne podanie symbolu strefy MPZP</h3>
                <p className="instruction">
                  Gmina nie udostępnia wektorowych danych MPZP. Odczytaj symbol
                  strefy z mapy rastrowej i podaj go poniżej, aby wznowić analizę.
                </p>
                <label htmlFor="manual-zone-symbol">Symbol strefy</label>
                <input
                  id="manual-zone-symbol"
                  value={zoneSymbolInput}
                  maxLength={ZONE_SYMBOL_MAX_LENGTH}
                  disabled={resumeAnalysis.loading || !result.analysis_id}
                  aria-invalid={Boolean(zoneSymbolError)}
                  aria-describedby="manual-zone-symbol-hint manual-zone-symbol-error"
                  placeholder="Np. 230_U"
                  onChange={(event) => {
                    setZoneSymbolInput(event.target.value);
                    setZoneSymbolError(null);
                  }}
                />
                <p id="manual-zone-symbol-hint" className="field-hint">
                  Ręcznie podany symbol strefy ma niższą wiarygodność i wymaga
                  weryfikacji z dokumentem planu.
                </p>
                {zoneSymbolError && (
                  <p id="manual-zone-symbol-error" className="field-error">
                    {zoneSymbolError}
                  </p>
                )}
                {resumeAnalysis.error && (
                  <p className="field-error">{resumeAnalysis.error}</p>
                )}
                <button
                  type="submit"
                  className="primary-button"
                  disabled={resumeAnalysis.loading || !result.analysis_id}
                >
                  {resumeAnalysis.loading ? "Wznawiam analizę…" : "Wznów analizę"}
                </button>
              </form>
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
    </main>
  );
}
