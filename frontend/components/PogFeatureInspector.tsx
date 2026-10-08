"use client";

import { type KeyboardEvent, type MouseEvent, useEffect, useId, useRef, useState } from "react";

import { PogAreaSummary } from "@/components/PogAreaSummary";
import { PogSwatch } from "@/components/PogLegend";
import { getPogFeatureDetails } from "@/lib/api";
import type { PogStatusFilter } from "@/lib/pogLayers";
import { type PogLayerStatus, inspectorEmptyMessage } from "@/lib/pogLayerState";
import { formatPlDate, legalStatusShort } from "@/lib/pogStatus";
import {
  POG_PARAMETER_BY_THEME,
  type PogParameterName,
  type PogThemeId,
  formatThemeValue,
  themeById,
} from "@/lib/pogThemes";
import { legalStatusStyle, overlayStyle, zoneLabel } from "@/lib/pogZones";
import type {
  PogFeatureDetails,
  PogInspectorHit,
  PogOverlayTileProperties,
  PogPointQuery,
  PogZoneTileProperties,
} from "@/lib/types";

type DetailsEntry =
  | { status: "loading" }
  | { status: "ready"; details: PogFeatureDetails }
  | { status: "error" };

/**
 * Szczegóły spoza kafla MVT dla wszystkich trafień punktu — pobierane w tle.
 * Panel pokazuje dane z kafla natychmiast; błąd szczegółów niczego nie zeruje.
 */
function useFeatureDetails(hits: PogInspectorHit[]): Record<string, DetailsEntry> {
  const [entries, setEntries] = useState<Record<string, DetailsEntry>>({});
  useEffect(() => {
    const controller = new AbortController();
    setEntries(Object.fromEntries(hits.map((hit) => [hit.key, { status: "loading" }])));
    for (const hit of hits) {
      void getPogFeatureDetails(hit.properties.data_release_id, hit.properties.feature_id, {
        signal: controller.signal,
      })
        .then((details) => {
          if (controller.signal.aborted) return;
          setEntries((current) => ({ ...current, [hit.key]: { status: "ready", details } }));
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setEntries((current) => ({ ...current, [hit.key]: { status: "error" } }));
        });
    }
    return () => controller.abort();
  }, [hits]);
  return entries;
}

function LegalStatusLine({ status }: { status: PogZoneTileProperties["legal_status"] }) {
  const style = legalStatusStyle(status);
  return (
    <p className="pog-inspector-status">
      Status aktu: <strong>{legalStatusShort(status)}</strong>
      {style.badge && (
        <span className="pog-non-binding-badge pog-inline-badge" data-testid="pog-inspector-badge">
          <PogSwatch color="#fff4dc" outline="#3d3d3d" pattern={style.pattern} dash={style.line_dasharray} />{" "}
          {style.badge}
        </span>
      )}
    </p>
  );
}

function profileText(codes: string | undefined, details: PogFeatureDetails["primary_profiles"] | undefined) {
  if (details && details.length > 0) {
    return details.map((profile) => (profile.label ? `${profile.code} — ${profile.label}` : profile.code)).join("; ");
  }
  return codes ? codes.split(",").join(", ") : "brak w danych";
}

function DetailsNote({ entry, releaseId }: { entry: DetailsEntry | undefined; releaseId: number }) {
  if (!entry || entry.status === "ready") return null;
  return (
    <p className="layer-toggle-status" data-testid="pog-inspector-details-state">
      {entry.status === "loading"
        ? `Pobieranie szczegółów z wydania #${releaseId}…`
        : `Szczegóły niedostępne — powyżej dane z kafla wydania #${releaseId}.`}
    </p>
  );
}

function ActDetails({ zone, entry }: { zone: PogZoneTileProperties | PogOverlayTileProperties; entry?: DetailsEntry }) {
  const act = entry?.status === "ready" ? entry.details.act : null;
  const resolution = act?.resolution_number
    ? `uchwała ${act.resolution_number}${act.resolution_date ? ` z ${formatPlDate(act.resolution_date)}` : ""}`
    : null;
  return (
    <>
      <dt>Akt</dt>
      <dd>
        <span className="pog-inspector-id">{zone.act_id}</span>
        {act?.name && <span>. {act.name}</span>}
        {resolution && <span>; {resolution}</span>}
      </dd>
    </>
  );
}

function ZoneHit({
  hit,
  entry,
  releaseLabel,
}: {
  hit: Extract<PogInspectorHit, { layer: "zones" }>;
  entry?: DetailsEntry;
  releaseLabel: string | null;
}) {
  const zone = hit.properties;
  const headingId = useId();
  const [showSummary, setShowSummary] = useState(false);
  const details = entry?.status === "ready" ? entry.details : null;
  return (
    <article className="pog-inspector-hit" aria-labelledby={headingId} data-testid="pog-inspector-zone">
      <h3 id={headingId}>
        {zone.symbol ? `${zone.symbol}: ` : ""}
        {zoneLabel(zone.zone_code)}
      </h3>
      {(details?.label ?? zone.label) && <p className="pog-inspector-label">{details?.label ?? zone.label}</p>}
      <LegalStatusLine status={zone.legal_status} />
      <dl className="pog-inspector-list">
        {(Object.entries(POG_PARAMETER_BY_THEME) as Array<[Exclude<PogThemeId, "zones">, PogParameterName]>).map(
          ([themeId, property]) => {
            const theme = themeById(themeId);
            return (
              <div key={property} className="pog-inspector-row">
                <dt>{theme.label}</dt>
                <dd data-testid={`pog-inspector-${property}`}>{formatThemeValue(theme, zone[property])}</dd>
              </div>
            );
          },
        )}
        <dt>Profil podstawowy</dt>
        <dd data-testid="pog-inspector-primary-profiles">
          {profileText(zone.primary_profiles, details?.primary_profiles)}
        </dd>
        <dt>Profil dodatkowy</dt>
        <dd>{profileText(zone.additional_profiles, details?.additional_profiles)}</dd>
        <ActDetails zone={zone} entry={entry} />
        <dt>Wydanie danych</dt>
        <dd data-testid="pog-inspector-release">
          #{zone.data_release_id}
          {releaseLabel ? ` (${releaseLabel})` : ""}
          {zone.feature_version ? `, wersja obiektu ${zone.feature_version}` : ""}
        </dd>
      </dl>
      {zone.parameters_informational && (
        <p className="layer-toggle-status">Parametry mają charakter informacyjny (PDF/uzasadnienie).</p>
      )}
      <DetailsNote entry={entry} releaseId={zone.data_release_id} />
      <button
        type="button"
        className="secondary-button"
        aria-expanded={showSummary}
        onClick={() => setShowSummary((value) => !value)}
      >
        {showSummary ? "Ukryj strukturę stref" : "Struktura stref aktu i gminy"}
      </button>
      {showSummary && (
        <PogAreaSummary
          releaseId={zone.data_release_id}
          actId={zone.act_id}
          teryt={zone.teryt ?? null}
          legalStatus={zone.legal_status}
        />
      )}
    </article>
  );
}

function OverlayHit({ hit, entry }: { hit: Exclude<PogInspectorHit, { layer: "zones" }>; entry?: DetailsEntry }) {
  const style = overlayStyle(hit.layer);
  const overlay = hit.properties;
  const details = entry?.status === "ready" ? entry.details : null;
  return (
    <li className="pog-inspector-overlay" data-testid="pog-inspector-overlay" data-layer={hit.layer}>
      <PogSwatch color="#ffffff" pattern={style.pattern} outline={style.outline} dash={style.line_dasharray} />
      <span>
        <strong>{style.short_label}</strong> — {style.label}
        {overlay.symbol ? ` (${overlay.symbol})` : ""}
        {(details?.label ?? overlay.label) ? `: ${details?.label ?? overlay.label}` : ""}.{" "}
        Status: {legalStatusShort(overlay.legal_status)}; wydanie #{overlay.data_release_id}.
      </span>
    </li>
  );
}

export type PogFeatureInspectorProps = {
  query: PogPointQuery;
  layerStatus: PogLayerStatus;
  statusFilter: PogStatusFilter;
  statusFilterLabel: string;
  analyzing?: boolean;
  onAnalyze: (lon: number, lat: number) => void;
  onClose: () => void;
};

const COORDINATE = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 5, maximumFractionDigits: 5 });

/**
 * Inspektor wskazanego punktu mapy POG (BK-404). Pokazuje natychmiast dane
 * wszystkich wyrenderowanych obiektów w punkcie (strefy, OUZ/OZS/OSDIS) z
 * przypiętego wydania — bez uruchamiania analizy. Pełną analizę działki
 * uruchamia wyłącznie osobny przycisk. Brak trafień jest opisany w kontekście
 * stanu warstwy i pokrycia, nigdy jako „brak planu”.
 */
export function PogFeatureInspector({
  query,
  layerStatus,
  statusFilter,
  statusFilterLabel,
  analyzing = false,
  onAnalyze,
  onClose,
}: PogFeatureInspectorProps) {
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const returnFocus = useRef<Element | null>(null);
  const headingId = useId();
  const entries = useFeatureDetails(query.hits);

  useEffect(() => {
    if (returnFocus.current === null && typeof document !== "undefined") {
      returnFocus.current = document.activeElement;
    }
    headingRef.current?.focus();
  }, [query]);

  const close = () => {
    onClose();
    const target = returnFocus.current;
    if (target instanceof HTMLElement && target.isConnected) target.focus();
  };

  const zones = query.hits.filter(
    (hit): hit is Extract<PogInspectorHit, { layer: "zones" }> => hit.layer === "zones",
  );
  const overlays = query.hits.filter(
    (hit): hit is Exclude<PogInspectorHit, { layer: "zones" }> => hit.layer !== "zones",
  );
  const releaseIds = [...new Set(query.hits.map((hit) => hit.properties.data_release_id))];
  const release = layerStatus.release;
  const releaseLabel = (id: number) => (release && release.release_id === id ? release.version_label : null);
  const empty = inspectorEmptyMessage(
    layerStatus,
    query,
    statusFilter === "all" ? null : statusFilterLabel,
  );

  return (
    // Kliknięcie i Escape w inspektorze nie mogą dotrzeć do mapy (nie uruchamiają analizy ani zamknięcia);
    // sama sekcja nie jest kontrolką — działanie wykonują przyciski wewnątrz.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <section
      className="analysis-card pog-inspector"
      aria-labelledby={headingId}
      data-testid="pog-inspector"
      onClick={(event: MouseEvent) => event.stopPropagation()}
      onKeyDown={(event: KeyboardEvent) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          close();
        }
      }}
    >
      <header className="pog-inspector-header">
        <h2 id={headingId} ref={headingRef} tabIndex={-1}>
          Plan ogólny w punkcie {COORDINATE.format(query.lat)}, {COORDINATE.format(query.lon)}
        </h2>
        <button type="button" className="secondary-button" onClick={close} aria-label="Zamknij inspektor (Escape)">
          Zamknij
        </button>
      </header>
      <p className="pog-inspector-source">
        Dane z kafli mapy{releaseIds.length === 1 ? ` — wydanie #${releaseIds[0]}` : ""}; podgląd bez
        uruchamiania analizy działki.
      </p>
      {releaseIds.length > 1 && (
        <p className="pog-area-summary-partial" role="note">
          Obiekty pochodzą z różnych wydań ({releaseIds.map((id) => `#${id}`).join(", ")}) — odśwież mapę.
        </p>
      )}

      {query.hits.length === 0 ? (
        <p className="pog-inspector-empty" data-testid="pog-inspector-empty" data-kind={empty.kind}>
          {empty.text}
        </p>
      ) : (
        <>
          {zones.length > 1 && (
            <p className="layer-toggle-status">W punkcie nakłada się {zones.length} stref — pokazano wszystkie.</p>
          )}
          {zones.length === 0 && (
            <p className="pog-inspector-empty" data-testid="pog-inspector-no-zone" data-kind={empty.kind}>
              {empty.kind === "no_object"
                ? `W tym punkcie wydanie #${releaseIds[0]} nie zawiera strefy planistycznej (luka w danych wydania). ` +
                  "To nie jest urzędowe potwierdzenie braku planu."
                : empty.text}
            </p>
          )}
          {zones.map((hit) => (
            <ZoneHit key={hit.key} hit={hit} entry={entries[hit.key]} releaseLabel={releaseLabel(hit.properties.data_release_id)} />
          ))}
          <section className="pog-inspector-overlays" aria-label="OUZ, OZS i OSDIS w punkcie">
            <h3>OUZ / OZS / OSDIS w punkcie</h3>
            {overlays.length > 0 ? (
              <ul className="pog-legend-list">
                {overlays.map((hit) => (
                  <OverlayHit key={hit.key} hit={hit} entry={entries[hit.key]} />
                ))}
              </ul>
            ) : (
              <p className="layer-toggle-status" data-testid="pog-inspector-no-overlays">
                {empty.kind === "no_object"
                  ? `W tym punkcie wydanie #${releaseIds[0]} nie zawiera obszarów OUZ, OZS ani OSDIS.`
                  : empty.text}
              </p>
            )}
          </section>
        </>
      )}

      <div className="pog-inspector-actions">
        <button
          type="button"
          className="primary-button"
          disabled={analyzing}
          onClick={(event: MouseEvent) => {
            event.stopPropagation();
            onAnalyze(query.lon, query.lat);
          }}
        >
          Analizuj działkę w tym punkcie
        </button>
        <p className="layer-toggle-status">
          Pełna analiza (działka ULDK, MPZP, POG, ryzyka) uruchamia się dopiero po kliknięciu przycisku.
        </p>
      </div>
    </section>
  );
}
