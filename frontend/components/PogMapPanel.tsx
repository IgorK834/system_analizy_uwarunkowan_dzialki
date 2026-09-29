"use client";

import { PogLayerStatus } from "@/components/PogLayerStatus";
import { PogLegend } from "@/components/PogLegend";
import { PogThemeSelector } from "@/components/PogThemeSelector";
import type { PogStatusFilter } from "@/lib/pogLayers";
import type { PogLayerStatus as PogLayerStatusValue } from "@/lib/pogLayerState";
import type { PogThemeId } from "@/lib/pogThemes";

export const STATUS_FILTER_LABELS: Record<PogStatusFilter, string> = {
  all: "Wszystkie akty",
  binding: "Tylko akty obowiązujące",
  non_binding: "Tylko projekty (niewiążące)",
};

export type PogMapPanelProps = {
  layerStatus: PogLayerStatusValue;
  theme: PogThemeId;
  onThemeChange: (theme: PogThemeId) => void;
  statusFilter: PogStatusFilter;
  onStatusFilterChange: (filter: PogStatusFilter) => void;
  onRetry?: () => void;
  retrying?: boolean;
};

/**
 * Panel warstwy wektorowej POG: stan warstwy i plakietki statusu (BK-406) są
 * zawsze widoczne w nagłówku; tryby (BK-402), jawna edycja danych (projekt /
 * akt wiążący, obsługa klawiaturą) i legenda (BK-403) są w rozwijanej części.
 */
export function PogMapPanel({
  layerStatus,
  theme,
  onThemeChange,
  statusFilter,
  onStatusFilterChange,
  onRetry,
  retrying = false,
}: PogMapPanelProps) {
  const hasRelease = Boolean(layerStatus.release);
  return (
    <section className="pog-map-panel" aria-label="Plan ogólny gminy — mapa analityczna">
      <h2 className="pog-map-panel-title">Plan ogólny — mapa analityczna (lokalne wydanie)</h2>
      <PogLayerStatus status={layerStatus} onRetry={onRetry} retrying={retrying} />
      <details className="pog-map-panel-controls" open>
        <summary>Tryb mapy, edycja danych i legenda</summary>
        <PogThemeSelector value={theme} onChange={onThemeChange} disabled={!hasRelease} />
        <fieldset className="pog-status-filter" disabled={!hasRelease}>
          <legend>Edycja danych (status prawny aktu)</legend>
          {(Object.keys(STATUS_FILTER_LABELS) as PogStatusFilter[]).map((filter) => (
            <label key={filter}>
              <input
                type="radio"
                name="pog-status-filter"
                value={filter}
                checked={statusFilter === filter}
                onChange={() => onStatusFilterChange(filter)}
              />
              {STATUS_FILTER_LABELS[filter]}
            </label>
          ))}
        </fieldset>
        {hasRelease && <PogLegend theme={theme} />}
      </details>
    </section>
  );
}
