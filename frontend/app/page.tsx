"use client";

import { useCallback } from "react";

import { MapViewLoader } from "@/components/MapViewLoader";
import { SearchPanel } from "@/components/SearchPanel";
import { useAnalyzeParcel } from "@/hooks/useAnalyzeParcel";

export default function HomePage() {
  const { loading, error, result, run, reset } = useAnalyzeParcel();

  const handleMapClick = useCallback(
    (lon: number, lat: number) => {
      void run({ method: "map", lon, lat });
    },
    [run],
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
        <MapViewLoader onMapClick={handleMapClick} />
        <SearchPanel loading={loading} onAnalyze={run} />

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
          <section className="analysis-card result-card" aria-live="polite">
            <div className="result-heading">
              <div>
                <span className="eyebrow">Wynik analizy</span>
                <h2>{result.parcel?.parcel_identifier ?? "Działka"}</h2>
              </div>
              <span className="status-badge">{result.status}</span>
            </div>
            <dl className="result-summary">
              <div>
                <dt>ID analizy</dt>
                <dd>{result.analysis_id ?? "—"}</dd>
              </div>
              <div>
                <dt>Ostrzeżenia</dt>
                <dd>{result.warnings.length}</dd>
              </div>
              <div>
                <dt>Strefy MPZP</dt>
                <dd>{result.mpzp_zones.length}</dd>
              </div>
              <div>
                <dt>Powierzchnia</dt>
                <dd>
                  {result.parcel
                    ? `${result.parcel.metrics.area_sqm.toLocaleString("pl-PL")} m²`
                    : "—"}
                </dd>
              </div>
            </dl>
            {result.manual_zone_required && (
              <p className="manual-review">
                Wynik wymaga ręcznego odczytania symbolu strefy MPZP.
              </p>
            )}
            {result.mpzp_zones[0]?.primary_use && (
              <p className="result-preview">
                Dominujące przeznaczenie: {result.mpzp_zones[0].primary_use}
              </p>
            )}
            <button type="button" className="secondary-button" onClick={reset}>
              Wyczyść wynik
            </button>
          </section>
        )}
      </div>
    </main>
  );
}
