"use client";

import { PogSwatch } from "@/components/PogLegend";
import { LAYER_STATE_LABELS } from "@/lib/layerState";
import {
  type PogLayerReason,
  type PogLayerStatus as PogLayerStatusValue,
  formatCheckedAt,
  pogLayerStatusMessage,
  releaseDescription,
} from "@/lib/pogLayerState";
import { legalStatusStyle } from "@/lib/pogZones";

export type PogLayerStatusProps = {
  status: PogLayerStatusValue;
  onRetry?: () => void;
  retrying?: boolean;
};

// Ponowienie ma sens tylko przy awarii metadanych/kafli albo nieaktualnym
// wydaniu — niepełne dane importu (BK-405) czy limit kafla nie znikną po nim.
const RETRYABLE: ReadonlySet<PogLayerReason> = new Set([
  "release_error",
  "refresh_failed",
  "release_not_active",
  "tiles_failed",
  "tile_errors",
]);

/**
 * Stan warstwy POG (BK-406): sześć stanów z odrębnym komunikatem, opis
 * przypiętego wydania z datą oraz stałe plakietki statusu prawnego. Plakietka
 * „projekt / dane niewiążące” zależy od aktów w wydaniu, a nie od stanu
 * warstwy — pozostaje widoczna również przy awarii kafli i danych `stale`.
 */
export function PogLayerStatus({ status, onRetry, retrying = false }: PogLayerStatusProps) {
  const { title, detail } = pogLayerStatusMessage(status);
  const release = status.release;
  const canRetry = Boolean(onRetry) && RETRYABLE.has(status.reason);
  return (
    <section
      className={`pog-layer-status layer-state-${status.state}`}
      aria-label="Stan warstwy planu ogólnego"
      data-layer-state={status.state}
    >
      <p className="pog-layer-status-title" role="status" aria-live="polite">
        <span
          className={`layer-state-chip layer-state-chip-${status.state}`}
          data-testid="pog-layer-state"
        >
          {LAYER_STATE_LABELS[status.state]}
        </span>{" "}
        {title}
      </p>
      <p className="pog-layer-status-detail">{detail}</p>
      {release && (
        <p className="pog-layer-status-release" data-testid="pog-release-line">
          {releaseDescription(release)}, styl {release.style_version}
          {status.state === "stale" && (
            <strong>
              {" "}
              — dane nieaktualne, ostatnio potwierdzone{" "}
              {formatCheckedAt(status.checkedAt) ?? "w nieznanej chwili"}
            </strong>
          )}
          . {release.legal_note}
        </p>
      )}
      {status.badges.map(({ status: legalStatus, badge }) => {
        const style = legalStatusStyle(legalStatus);
        return (
          <p
            key={badge}
            className="pog-non-binding-badge"
            data-testid="pog-status-badge"
            data-status={legalStatus}
          >
            <PogSwatch
              color="#fff4dc"
              outline="#3d3d3d"
              pattern={style.pattern}
              dash={style.line_dasharray}
            />{" "}
            {badge}
          </p>
        );
      })}
      {canRetry && (
        <button
          type="button"
          className="secondary-button pog-layer-retry"
          onClick={onRetry}
          disabled={retrying}
        >
          {retrying ? "Ponawianie… (dotychczasowe dane pozostają)" : "Ponów wczytanie warstwy"}
        </button>
      )}
    </section>
  );
}
