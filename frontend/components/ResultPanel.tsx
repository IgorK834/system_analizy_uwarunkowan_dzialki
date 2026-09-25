"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";

import { LayerToggle, type LayerToggleItem } from "@/components/LayerToggle";
import { MpzpZoneCard } from "@/components/MpzpZoneCard";
import { PogOfficialSources } from "@/components/PogOfficialSources";
import { ReportDownloadButton } from "@/components/ReportDownloadButton";
import {
  BUILDABLE_AREA_FILL_COLOR,
  BUILDABLE_AREA_FILL_LAYER_ID,
  BUILDABLE_AREA_FILL_OPACITY,
  BUILDABLE_AREA_LINE_COLOR,
  BUILDABLE_AREA_LINE_DASH,
  BUILDABLE_AREA_LINE_LAYER_ID,
  BUILDABLE_AREA_LINE_WIDTH,
  BUILDABLE_AREA_SOURCE_ID,
  LAYER_LEGEND,
  NETWORK_COLOR,
  NETWORK_FILL_LAYER_ID,
  NETWORK_LINE_LAYER_ID,
  NETWORK_LINE_WIDTH,
  NETWORK_SOURCE_ID,
  PARCEL_FILL_COLOR,
  PARCEL_FILL_LAYER_ID,
  PARCEL_FILL_OPACITY,
  PARCEL_LINE_COLOR,
  PARCEL_LINE_LAYER_ID,
  PARCEL_LINE_WIDTH,
  PARCEL_SOURCE_ID,
  PROTECTION_ZONE_FILL_COLOR,
  PROTECTION_ZONE_FILL_LAYER_ID,
  PROTECTION_ZONE_FILL_OPACITY,
  PROTECTION_ZONE_LINE_COLOR,
  PROTECTION_ZONE_LINE_LAYER_ID,
  PROTECTION_ZONE_LINE_WIDTH,
  PROTECTION_ZONE_SOURCE_ID,
  RISK_FILL_COLOR,
  RISK_FILL_LAYER_ID,
  RISK_FILL_OPACITY,
  RISK_LINE_COLOR,
  RISK_LINE_LAYER_ID,
  RISK_LINE_WIDTH,
  RISK_SOURCE_ID,
} from "@/lib/layerStyles";
import {
  coverageStatusLabel,
  dataAvailabilityLabel,
  formatPlDate,
  legalStatusLabel,
  pogStatusNotes,
} from "@/lib/pogStatus";
import { verifiedHttpsHref } from "@/lib/safeLink";
import type {
  AnalyzeResponse,
  InfrastructureResult,
  MpzpZoneResult,
  RiskResult,
  SourceMetadata,
  WarningMessage,
} from "@/lib/types";

export type ResultPanelProps = {
  result: AnalyzeResponse;
  /** Instancja mapy współdzielona z MapView przez page.tsx (patrz onMapReady). */
  map: maplibregl.Map | null;
};

const SEVERITY_LABEL: Record<WarningMessage["severity"], string> = {
  info: "Informacja",
  warning: "Ostrzeżenie",
  error: "Błąd",
};

export function ResultPanel({ result, map }: ResultPanelProps) {
  const [parcelVisible, setParcelVisible] = useState(true);
  const [buildableAreaVisible, setBuildableAreaVisible] = useState(true);
  const [networksVisible, setNetworksVisible] = useState(true);
  const [protectionZonesVisible, setProtectionZonesVisible] = useState(true);
  const [risksVisible, setRisksVisible] = useState(true);
  const layersReadyRef = useRef(false);
  const fittedResultRef = useRef<{
    map: maplibregl.Map;
    resultKey: number | string;
  } | null>(null);

  const parcelGeojson = result.parcel?.geometry_geojson ?? null;
  const buildableAreaGeojson = result.parcel?.buildable_area_geojson ?? null;
  const networksGeojson = useMemo(
    () =>
      toFeatureCollection(
        result.infrastructure.map((item) => item.network_geometry_geojson),
      ),
    [result.infrastructure],
  );
  const protectionZonesGeojson = useMemo(
    () =>
      toFeatureCollection(
        result.infrastructure.map((item) => item.protection_zone_geojson),
      ),
    [result.infrastructure],
  );
  const risksGeojson = useMemo(
    () => toFeatureCollection(result.risks.map((item) => item.geometry_geojson)),
    [result.risks],
  );

  // Warstwy GeoJSON (obrys działki, obszar zabudowy) są dodawane/usuwane na
  // istniejącej instancji mapy przekazanej z page.tsx — nigdy nie tworzymy
  // tu drugiej instancji MapLibre, zgodnie ze wzorcem z MapView.tsx.
  useEffect(() => {
    if (!map) return;

    // Kolejność dodawania jest równocześnie kolejnością renderowania. WMS-y są
    // dodawane wcześniej przez PreviewOverlays, a warstwy wynikowe układamy od
    // powierzchniowych ryzyk do najważniejszego obrysu działki na samej górze.
    addOrUpdateGeojsonLayer(map, {
      sourceId: RISK_SOURCE_ID,
      fillLayerId: RISK_FILL_LAYER_ID,
      lineLayerId: RISK_LINE_LAYER_ID,
      geojson: risksGeojson,
      fillColor: RISK_FILL_COLOR,
      fillOpacity: RISK_FILL_OPACITY,
      lineColor: RISK_LINE_COLOR,
      lineWidth: RISK_LINE_WIDTH,
    });
    addOrUpdateGeojsonLayer(map, {
      sourceId: PROTECTION_ZONE_SOURCE_ID,
      fillLayerId: PROTECTION_ZONE_FILL_LAYER_ID,
      lineLayerId: PROTECTION_ZONE_LINE_LAYER_ID,
      geojson: protectionZonesGeojson,
      fillColor: PROTECTION_ZONE_FILL_COLOR,
      fillOpacity: PROTECTION_ZONE_FILL_OPACITY,
      lineColor: PROTECTION_ZONE_LINE_COLOR,
      lineWidth: PROTECTION_ZONE_LINE_WIDTH,
    });
    addOrUpdateGeojsonLayer(map, {
      sourceId: NETWORK_SOURCE_ID,
      fillLayerId: NETWORK_FILL_LAYER_ID,
      lineLayerId: NETWORK_LINE_LAYER_ID,
      geojson: networksGeojson,
      lineColor: NETWORK_COLOR,
      lineWidth: NETWORK_LINE_WIDTH,
    });
    addOrUpdateGeojsonLayer(map, {
      sourceId: BUILDABLE_AREA_SOURCE_ID,
      fillLayerId: BUILDABLE_AREA_FILL_LAYER_ID,
      lineLayerId: BUILDABLE_AREA_LINE_LAYER_ID,
      geojson: buildableAreaGeojson,
      fillColor: BUILDABLE_AREA_FILL_COLOR,
      fillOpacity: BUILDABLE_AREA_FILL_OPACITY,
      lineColor: BUILDABLE_AREA_LINE_COLOR,
      lineWidth: BUILDABLE_AREA_LINE_WIDTH,
      lineDasharray: BUILDABLE_AREA_LINE_DASH,
    });
    addOrUpdateGeojsonLayer(map, {
      sourceId: PARCEL_SOURCE_ID,
      fillLayerId: PARCEL_FILL_LAYER_ID,
      lineLayerId: PARCEL_LINE_LAYER_ID,
      geojson: parcelGeojson,
      fillColor: PARCEL_FILL_COLOR,
      fillOpacity: PARCEL_FILL_OPACITY,
      lineColor: PARCEL_LINE_COLOR,
      lineWidth: PARCEL_LINE_WIDTH,
    });
    layersReadyRef.current = true;

    return () => {
      removeGeojsonLayer(map, {
        sourceId: PARCEL_SOURCE_ID,
        fillLayerId: PARCEL_FILL_LAYER_ID,
        lineLayerId: PARCEL_LINE_LAYER_ID,
      });
      removeGeojsonLayer(map, {
        sourceId: BUILDABLE_AREA_SOURCE_ID,
        fillLayerId: BUILDABLE_AREA_FILL_LAYER_ID,
        lineLayerId: BUILDABLE_AREA_LINE_LAYER_ID,
      });
      removeGeojsonLayer(map, {
        sourceId: NETWORK_SOURCE_ID,
        fillLayerId: NETWORK_FILL_LAYER_ID,
        lineLayerId: NETWORK_LINE_LAYER_ID,
      });
      removeGeojsonLayer(map, {
        sourceId: PROTECTION_ZONE_SOURCE_ID,
        fillLayerId: PROTECTION_ZONE_FILL_LAYER_ID,
        lineLayerId: PROTECTION_ZONE_LINE_LAYER_ID,
      });
      removeGeojsonLayer(map, {
        sourceId: RISK_SOURCE_ID,
        fillLayerId: RISK_FILL_LAYER_ID,
        lineLayerId: RISK_LINE_LAYER_ID,
      });
      layersReadyRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    map,
    parcelGeojson,
    buildableAreaGeojson,
    networksGeojson,
    protectionZonesGeojson,
    risksGeojson,
  ]);

  useEffect(() => {
    if (!map || !parcelGeojson) return;
    const resultKey =
      result.analysis_id ??
      result.parcel?.parcel_identifier ??
      result.analyzed_at;
    if (
      fittedResultRef.current?.map === map &&
      fittedResultRef.current.resultKey === resultKey
    ) {
      return;
    }

    const bounds = geojsonBounds(parcelGeojson);
    if (!bounds) return;
    map.fitBounds(bounds, {
      padding: 64,
      maxZoom: 18,
      duration: 550,
    });
    fittedResultRef.current = { map, resultKey };
  }, [map, parcelGeojson, result.analysis_id, result.analyzed_at, result.parcel]);

  useEffect(() => {
    if (!map || !layersReadyRef.current) return;
    setLayerVisibility(map, [PARCEL_FILL_LAYER_ID, PARCEL_LINE_LAYER_ID], parcelVisible);
  }, [map, parcelVisible]);

  useEffect(() => {
    if (!map || !layersReadyRef.current) return;
    setLayerVisibility(
      map,
      [BUILDABLE_AREA_FILL_LAYER_ID, BUILDABLE_AREA_LINE_LAYER_ID],
      buildableAreaVisible,
    );
  }, [map, buildableAreaVisible]);

  useEffect(() => {
    if (!map || !layersReadyRef.current) return;
    setLayerVisibility(map, [NETWORK_LINE_LAYER_ID], networksVisible);
  }, [map, networksVisible]);

  useEffect(() => {
    if (!map || !layersReadyRef.current) return;
    setLayerVisibility(
      map,
      [PROTECTION_ZONE_FILL_LAYER_ID, PROTECTION_ZONE_LINE_LAYER_ID],
      protectionZonesVisible,
    );
  }, [map, protectionZonesVisible]);

  useEffect(() => {
    if (!map || !layersReadyRef.current) return;
    setLayerVisibility(
      map,
      [RISK_FILL_LAYER_ID, RISK_LINE_LAYER_ID],
      risksVisible,
    );
  }, [map, risksVisible]);

  const toggleItems: LayerToggleItem[] = [
    {
      id: "parcel",
      label: LAYER_LEGEND.parcel.label,
      color: LAYER_LEGEND.parcel.color,
      checked: Boolean(parcelGeojson) && parcelVisible,
      disabled: !parcelGeojson,
      disabledReason: !parcelGeojson ? "Brak geometrii działki w wyniku analizy." : undefined,
    },
    {
      id: "buildable_area",
      label: LAYER_LEGEND.buildable_area.label,
      color: LAYER_LEGEND.buildable_area.color,
      checked: Boolean(buildableAreaGeojson) && buildableAreaVisible,
      disabled: !buildableAreaGeojson,
      disabledReason: !buildableAreaGeojson
        ? "Geometria obszaru zabudowy jest niedostępna dla tej działki (odsunięcie zredukowało obszar do zera)."
        : undefined,
    },
    {
      id: "networks",
      label: LAYER_LEGEND.networks.label,
      color: LAYER_LEGEND.networks.color,
      checked: Boolean(networksGeojson) && networksVisible,
      disabled: !networksGeojson,
      disabledReason: !networksGeojson
        ? "Brak geometrii sieci dla tej analizy."
        : undefined,
    },
    {
      id: "protection_zones",
      label: LAYER_LEGEND.protection_zones.label,
      color: LAYER_LEGEND.protection_zones.color,
      checked: Boolean(protectionZonesGeojson) && protectionZonesVisible,
      disabled: !protectionZonesGeojson,
      disabledReason: !protectionZonesGeojson
        ? "Brak efektywnych stref ochronnych dla tej analizy."
        : undefined,
    },
    {
      id: "risks",
      label: LAYER_LEGEND.risks.label,
      color: LAYER_LEGEND.risks.color,
      checked: Boolean(risksGeojson) && risksVisible,
      disabled: !risksGeojson,
      disabledReason: !risksGeojson
        ? "Brak geometrii ryzyk dla tej analizy."
        : undefined,
    },
  ];

  return (
    <section className="analysis-card result-card" aria-live="polite">
      <div className="result-heading">
        <div>
          <span className="eyebrow">Wynik analizy</span>
          <h2>{result.parcel?.parcel_identifier ?? "Działka"}</h2>
        </div>
        <span className="status-badge">{result.status}</span>
      </div>

      <p className="result-disclaimer">
        Wynik ma charakter informacyjny i nie zastępuje dokumentów planistycznych
        ani decyzji administracyjnej.
      </p>

      <ReportDownloadButton
        key={result.analysis_id ?? "unsaved-analysis"}
        analysisId={result.analysis_id}
        parcelIdentifier={result.parcel?.parcel_identifier ?? null}
      />

      <LayerToggle items={toggleItems} onChange={handleToggleChange} legendLabel="Warstwy na mapie" />

      <GeometrySection result={result} />
      <MpzpSection zones={result.mpzp_zones} />
      <PogSection result={result} />
      <InfrastructureSection items={result.infrastructure} />
      <RisksSection items={result.risks} />
      <SourcesSection sources={result.sources} />
      <WarningsSection warnings={result.warnings} />
    </section>
  );

  function handleToggleChange(id: string, checked: boolean) {
    if (id === "parcel") setParcelVisible(checked);
    if (id === "buildable_area") setBuildableAreaVisible(checked);
    if (id === "networks") setNetworksVisible(checked);
    if (id === "protection_zones") setProtectionZonesVisible(checked);
    if (id === "risks") setRisksVisible(checked);
  }
}

function GeometrySection({ result }: { result: AnalyzeResponse }) {
  const parcel = result.parcel;
  return (
    <section className="result-section" aria-label="Geometria działki">
      <h3>Geometria</h3>
      {!parcel && <p className="section-empty">Nie udało się ustalić geometrii działki.</p>}
      <dl className="result-summary">
        <div>
          <dt>ID analizy</dt>
          <dd>{result.analysis_id ?? "—"}</dd>
        </div>
      </dl>
      {parcel && (
        <dl className="result-summary">
          <div>
            <dt>Powierzchnia</dt>
            <dd>{parcel.metrics.area_sqm.toLocaleString("pl-PL")} m²</dd>
          </div>
          <div>
            <dt>Powierzchnia (ha)</dt>
            <dd>{parcel.metrics.area_ha.toLocaleString("pl-PL")} ha</dd>
          </div>
          <div>
            <dt>Obwód</dt>
            <dd>{parcel.metrics.perimeter_m.toLocaleString("pl-PL")} m</dd>
          </div>
          <div>
            <dt>Geometria naprawiana</dt>
            <dd>{parcel.metrics.geometry_repaired ? "Tak" : "Nie"}</dd>
          </div>
        </dl>
      )}
      {result.buildable_area_sqm != null && (
        <p className="result-preview">
          Szacowana powierzchnia zabudowy (przybliżenie techniczne):{" "}
          {result.buildable_area_sqm.toLocaleString("pl-PL")} m²
        </p>
      )}
    </section>
  );
}

function MpzpSection({ zones }: { zones: MpzpZoneResult[] }) {
  return (
    <section className="result-section" aria-label="Strefy MPZP">
      <h3>MPZP</h3>
      {zones.length === 0 && (
        <p className="section-empty">
          Nie sprawdzono albo nie znaleziono stref MPZP przecinających działkę.
        </p>
      )}
      {zones.length > 0 && (
        <ul className="result-list" aria-label="Wszystkie strefy MPZP działki">
          {zones.map((zone, index) => (
            <MpzpZoneCard key={zone.zone_id ?? `${zone.zone_symbol}-${index}`} zone={zone} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PogSection({ result }: { result: AnalyzeResponse }) {
  const pog = result.pog;
  const legalDisclaimer =
    pog?.raw_attributes && typeof pog.raw_attributes === "object"
      ? (pog.raw_attributes as { scenario?: { legal_disclaimer?: string | null } })
          .scenario?.legal_disclaimer ?? null
      : null;

  return (
    <section className="result-section" aria-label="Plan Ogólny Gminy i OUZ">
      <h3>Plan Ogólny Gminy (POG)</h3>
      {!pog && (
        <p className="section-empty">
          Niedostępne — brak danych POG dla tej działki.
        </p>
      )}
      {pog && (
        <>
          <dl className="result-summary" aria-label="Status POG">
            <div>
              <dt>Status prawny aktu</dt>
              <dd data-testid="pog-legal-status">{legalStatusLabel(pog.legal_status)}</dd>
            </div>
            <div>
              <dt>Zakres danych przestrzennych</dt>
              <dd data-testid="pog-coverage-status">{coverageStatusLabel(pog.coverage_status)}</dd>
            </div>
            <div>
              <dt>Aktualność źródła</dt>
              <dd data-testid="pog-data-availability">
                {dataAvailabilityLabel(pog.data_availability)}
                {pog.status_confirmed_at
                  ? ` (potwierdzono ${formatPlDate(pog.status_confirmed_at)})`
                  : ""}
              </dd>
            </div>
            {pog.legal_status_evidence && (
              <div>
                <dt>Podstawa statusu</dt>
                <dd>
                  {pog.legal_status_evidence.source_name}
                  {pog.legal_status_evidence.raw_value
                    ? ` — kod ${pog.legal_status_evidence.raw_value}`
                    : ""}
                </dd>
              </div>
            )}
            {pog.coverage_evidence && (
              <div>
                <dt>Potwierdzenie braku aktu</dt>
                <dd>
                  {pog.coverage_evidence.source_name}
                  {pog.coverage_evidence.reference ? ` — ${pog.coverage_evidence.reference}` : ""}
                </dd>
              </div>
            )}
            <div>
              <dt>Strefa planistyczna</dt>
              <dd>{pog.zone_type ?? pog.planning_zone ?? "—"}</dd>
            </div>
            <div>
              <dt>Obszar uzupełnienia zabudowy (OUZ)</dt>
              <dd>{pog.in_ouz ? "Tak" : "Nie"}</dd>
            </div>
            <div>
              <dt>Obszar śródmiejski</dt>
              <dd>{pog.in_downtown_area ? "Tak" : "Nie"}</dd>
            </div>
          </dl>
          {pog.zones.length > 0 && (
            <div className="result-table-scroll">
              <table className="result-table">
                <caption>Strefy POG przecinające działkę</caption>
                <thead>
                  <tr>
                    <th>Strefa</th><th>Powierzchnia</th><th>Udział</th>
                    <th>Intensywność</th><th>Wysokość</th>
                    <th>Zabudowa</th><th>Biologicznie czynna</th><th>Źródło</th>
                  </tr>
                </thead>
                <tbody>
                  {pog.zones.map((zone) => (
                    <tr key={zone.id}>
                      <th scope="row">{zone.symbol ?? zone.type}{zone.label ? ` — ${zone.label}` : ""}</th>
                      <td>{zone.area_sqm.toFixed(1)} m²</td>
                      <td>{zone.area_pct.toFixed(1)}%</td>
                      <td>{formatOptional(zone.max_overground_floor_area_ratio)}</td>
                      <td>{formatOptional(zone.max_building_height_m, "m")}</td>
                      <td>{formatOptional(zone.max_building_coverage_pct, "%")}</td>
                      <td>{formatOptional(zone.min_biologically_active_pct, "%")}</td>
                      <td>
                        <ZoneSourceLink href={verifiedHttpsHref(zone.gml_url, zone.gml_url_verified)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pog.social_infrastructure_standard_areas.length > 0 && (
            <p className="result-preview">
              Standardy dostępności infrastruktury społecznej: {pog.social_infrastructure_standard_areas.length}
            </p>
          )}
          {pog.conflict_with_mpzp !== null && (
            <p className={pog.conflict_with_mpzp ? "field-error" : "result-preview"}>
              {pog.conflict_with_mpzp
                ? "Wykryto niezgodność ze strefą MPZP — wymaga weryfikacji."
                : "Zgodność z dominującą strefą MPZP potwierdzona wstępnie."}
            </p>
          )}
          {pogStatusNotes(pog).map((note) => (
            <p key={note} className="field-hint pog-status-note">
              {note}
            </p>
          ))}
          {pog.manual_review_required && (
            <p className="manual-review">Wynik POG wymaga ręcznej weryfikacji.</p>
          )}
          {pog.act && <PogOfficialSources act={pog.act} />}
          {legalDisclaimer && <p className="legal-disclaimer">{legalDisclaimer}</p>}
          <ConfidenceBadge source={pog.source} />
        </>
      )}
    </section>
  );
}

function ZoneSourceLink({ href }: { href: string | null }) {
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      GML
    </a>
  ) : (
    <>—</>
  );
}

function formatOptional(value: number | null, unit = ""): string {
  return value === null ? "—" : `${value}${unit ? ` ${unit}` : ""}`;
}

function InfrastructureSection({ items }: { items: InfrastructureResult[] }) {
  return (
    <section className="result-section" aria-label="Infrastruktura i sieci uzbrojenia terenu">
      <h3>Infrastruktura</h3>
      {items.length === 0 && (
        <p className="section-empty">
          Nie sprawdzono albo nie wykryto sieci uzbrojenia terenu na działce.
        </p>
      )}
      {items.length > 0 && (
        <ul className="result-list">
          {items.map((item, index) => (
            <li key={`${item.network_type}-${index}`} className="result-list-item">
              <strong>{item.network_type}</strong>
              <p>Bufor techniczny: {item.buffer_m.toLocaleString("pl-PL")} m</p>
              {item.affects_buildable_area && (
                <p className="manual-review">
                  Strefa pomniejszyła szacowany obszar zabudowy o{" "}
                  {item.zone_area_sqm.toLocaleString("pl-PL")} m².
                </p>
              )}
              {item.rule_note && <p className="field-hint">{item.rule_note}</p>}
              <ConfidenceBadge source={item.source} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RisksSection({ items }: { items: RiskResult[] }) {
  return (
    <section className="result-section" aria-label="Ryzyka środowiskowe i przestrzenne">
      <h3>Ryzyka</h3>
      {items.length === 0 && (
        <p className="section-empty">
          Nie sprawdzono albo nie wykryto ryzyk środowiskowych ani przestrzennych.
        </p>
      )}
      {items.length > 0 && (
        <ul className="result-list">
          {items.map((item, index) => (
            <li key={`${item.risk_type}-${index}`} className="result-list-item">
              <strong>{item.risk_type}</strong>
              <p>{item.description}</p>
              <ConfidenceBadge source={item.source} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SourcesSection({ sources }: { sources: SourceMetadata[] }) {
  return (
    <section className="result-section" aria-label="Źródła danych">
      <h3>Źródła</h3>
      {sources.length === 0 && (
        <p className="section-empty">Brak zarejestrowanych źródeł dla tej analizy.</p>
      )}
      {sources.length > 0 && (
        <ul className="result-list">
          {sources.map((source, index) => (
            <li key={`${source.source_name}-${index}`} className="result-list-item">
              <strong>{source.source_name}</strong>
              <ConfidenceBadge source={source} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function WarningsSection({ warnings }: { warnings: WarningMessage[] }) {
  return (
    <section className="result-section" aria-label="Ostrzeżenia">
      <h3>Ostrzeżenia</h3>
      {warnings.length === 0 && (
        <p className="section-empty">Brak ostrzeżeń dla tej analizy.</p>
      )}
      {warnings.length > 0 && (
        <ul className="result-list">
          {warnings.map((warning, index) => (
            <li key={`${warning.code}-${index}`} className="result-list-item">
              <span className={`severity-badge severity-${warning.severity}`}>
                {SEVERITY_LABEL[warning.severity]}
              </span>
              <p>{warning.message}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ConfidenceBadge({ source }: { source: SourceMetadata | null }) {
  if (!source) {
    return <p className="section-empty">Brak metadanych źródła.</p>;
  }
  return (
    <p className="field-hint">
      Pewność danych: {(source.confidence * 100).toFixed(0)}%
      {source.manual_review_required && " · wymaga weryfikacji"}
    </p>
  );
}

// --- Pomocnicze funkcje zarządzające warstwami GeoJSON na instancji mapy ---

function toFeatureCollection(
  items: Array<Record<string, unknown> | null>,
): Record<string, unknown> | null {
  const features = items.flatMap((item) => {
    if (!item) return [];
    if (item.type === "FeatureCollection" && Array.isArray(item.features)) {
      return item.features.filter(
        (feature): feature is Record<string, unknown> =>
          Boolean(feature) && typeof feature === "object",
      );
    }
    return [item];
  });
  return features.length > 0
    ? { type: "FeatureCollection", features }
    : null;
}

function geojsonBounds(
  geojson: Record<string, unknown>,
): [[number, number], [number, number]] | null {
  let minLng = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLng = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;

  const includeCoordinates = (value: unknown): void => {
    if (!Array.isArray(value)) return;
    if (
      value.length >= 2 &&
      typeof value[0] === "number" &&
      typeof value[1] === "number" &&
      Number.isFinite(value[0]) &&
      Number.isFinite(value[1])
    ) {
      minLng = Math.min(minLng, value[0]);
      minLat = Math.min(minLat, value[1]);
      maxLng = Math.max(maxLng, value[0]);
      maxLat = Math.max(maxLat, value[1]);
      return;
    }
    for (const item of value) includeCoordinates(item);
  };

  const visit = (value: unknown): void => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return;
    const item = value as Record<string, unknown>;
    if (item.type === "Feature") {
      visit(item.geometry);
      return;
    }
    if (item.type === "FeatureCollection") {
      if (Array.isArray(item.features)) item.features.forEach(visit);
      return;
    }
    if (item.type === "GeometryCollection") {
      if (Array.isArray(item.geometries)) item.geometries.forEach(visit);
      return;
    }
    includeCoordinates(item.coordinates);
  };

  visit(geojson);
  return Number.isFinite(minLng)
    ? [
        [minLng, minLat],
        [maxLng, maxLat],
      ]
    : null;
}

type GeojsonLayerConfig = {
  sourceId: string;
  fillLayerId: string;
  lineLayerId: string;
  geojson: Record<string, unknown> | null;
  fillColor?: string;
  fillOpacity?: number;
  lineColor: string;
  lineWidth: number;
  lineDasharray?: [number, number];
};

function addOrUpdateGeojsonLayer(map: maplibregl.Map, config: GeojsonLayerConfig): void {
  const { sourceId, fillLayerId, lineLayerId, geojson } = config;

  if (!geojson) {
    removeGeojsonLayer(map, { sourceId, fillLayerId, lineLayerId });
    return;
  }

  const existingSource = map.getSource(sourceId) as
    | { setData: (data: unknown) => void }
    | undefined;

  if (existingSource) {
    existingSource.setData(geojson);
    return;
  }

  map.addSource(sourceId, {
    type: "geojson",
    data: geojson as unknown as GeoJSON.GeoJSON,
  });

  if (config.fillColor !== undefined) {
    map.addLayer({
      id: fillLayerId,
      type: "fill",
      source: sourceId,
      paint: {
        "fill-color": config.fillColor,
        "fill-opacity": config.fillOpacity ?? 0.15,
      },
    });
  }

  map.addLayer({
    id: lineLayerId,
    type: "line",
    source: sourceId,
    paint: {
      "line-color": config.lineColor,
      "line-width": config.lineWidth,
      ...(config.lineDasharray ? { "line-dasharray": config.lineDasharray } : {}),
    },
  });
}

function removeGeojsonLayer(
  map: maplibregl.Map,
  config: { sourceId: string; fillLayerId: string; lineLayerId: string },
): void {
  if (map.getLayer(config.lineLayerId)) map.removeLayer(config.lineLayerId);
  if (map.getLayer(config.fillLayerId)) map.removeLayer(config.fillLayerId);
  if (map.getSource(config.sourceId)) map.removeSource(config.sourceId);
}

function setLayerVisibility(
  map: maplibregl.Map,
  layerIds: string[],
  visible: boolean,
): void {
  for (const layerId of layerIds) {
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    }
  }
}
