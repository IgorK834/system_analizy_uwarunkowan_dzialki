"use client";

import { PogLegend } from "@/components/PogLegend";
import { PogThemeSelector } from "@/components/PogThemeSelector";
import type { PogReleaseState } from "@/hooks/usePogTileRelease";
import type { PogStatusFilter } from "@/lib/pogLayers";
import { legalStatusShort } from "@/lib/pogStatus";
import {
  POG_PARAMETER_BY_THEME,
  type PogThemeId,
  formatThemeValue,
  themeById,
} from "@/lib/pogThemes";
import { zoneLabel } from "@/lib/pogZones";
import type { PogZoneTileProperties } from "@/lib/types";

const STATUS_FILTER_LABELS: Record<PogStatusFilter, string> = {
  all: "Wszystkie akty",
  binding: "Tylko akty obowiązujące",
  non_binding: "Tylko projekty (niewiążące)",
};

export type PogMapPanelProps = {
  releaseState: PogReleaseState;
  theme: PogThemeId;
  onThemeChange: (theme: PogThemeId) => void;
  statusFilter: PogStatusFilter;
  onStatusFilterChange: (filter: PogStatusFilter) => void;
  selectedZone?: PogZoneTileProperties | null;
};

function releaseMessage(state: PogReleaseState): string {
  switch (state.status) {
    case "loading":
      return "Ładowanie lokalnego wydania planu ogólnego…";
    case "no_release":
      return (
        "Brak lokalnego wydania POG w bazie — warstwa jest niedostępna. " +
        "Nie oznacza to braku planu ogólnego w gminie."
      );
    case "error":
      return "Warstwa POG jest chwilowo niedostępna; mapa podstawowa nadal działa.";
    default:
      return (
        `Wydanie ${state.release.version_label} (#${state.release.release_id}), ` +
        `styl ${state.release.style_version}. ${state.release.legal_note}`
      );
  }
}

function SelectedZone({ zone }: { zone: PogZoneTileProperties }) {
  return (
    <section className="pog-selected-zone" aria-label="Wybrana strefa z mapy POG">
      <h3>Strefa pod kursorem (z kafla mapy)</h3>
      <dl>
        <dt>Strefa</dt>
        <dd>
          {zone.symbol ? `${zone.symbol}: ` : ""}
          {zoneLabel(zone.zone_code)}
        </dd>
        {(Object.entries(POG_PARAMETER_BY_THEME) as Array<[Exclude<PogThemeId, "zones">, keyof PogZoneTileProperties]>).map(
          ([themeId, property]) => {
            const theme = themeById(themeId);
            return (
              <div key={property} className="pog-selected-zone-row">
                <dt>{theme.label}</dt>
                <dd data-testid={`pog-selected-${property}`}>
                  {formatThemeValue(theme, zone[property] as number | undefined)}
                </dd>
              </div>
            );
          },
        )}
        <dt>Status aktu</dt>
        <dd>{legalStatusShort(zone.legal_status)}</dd>
        <dt>Wydanie danych</dt>
        <dd>#{zone.data_release_id}</dd>
      </dl>
      {zone.parameters_informational && (
        <p className="layer-toggle-status">Parametry mają charakter informacyjny (PDF/uzasadnienie).</p>
      )}
    </section>
  );
}

/** Panel warstwy wektorowej POG: tryby, filtr statusu, legenda (BK-401–403). */
export function PogMapPanel({
  releaseState,
  theme,
  onThemeChange,
  statusFilter,
  onStatusFilterChange,
  selectedZone = null,
}: PogMapPanelProps) {
  const available = releaseState.status === "available";
  const statuses = available ? releaseState.release.acts_by_legal_status : {};
  const hasNonBinding = Boolean((statuses.project ?? 0) + (statuses.in_progress ?? 0));
  return (
    <details className="pog-map-panel" open aria-label="Plan ogólny gminy — mapa analityczna">
      <summary>Plan ogólny — mapa analityczna (lokalne wydanie)</summary>
      <p className="layer-toggle-status" role="status">
        {releaseMessage(releaseState)}
      </p>
      {hasNonBinding && (
        <p className="pog-non-binding-badge">
          Wydanie zawiera projekty — dane niewiążące są oznaczone obrysem przerywanym.
        </p>
      )}
      <PogThemeSelector value={theme} onChange={onThemeChange} disabled={!available} />
      <fieldset className="pog-status-filter" disabled={!available}>
        <legend>Status prawny</legend>
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
      {available && <PogLegend theme={theme} />}
      {selectedZone && <SelectedZone zone={selectedZone} />}
    </details>
  );
}
