import {
  ROAD_RELATION_LABELS,
  bdotReasonLabel,
  bdotStatusLabel,
  formatMeters,
  roadContextOrUnknown,
  roadStatusNote,
} from "@/lib/bdot";
import { formatPlDate } from "@/lib/pogStatus";
import type { RoadContextResult } from "@/lib/types";
import { BdotReleaseLine, BdotSourceLine } from "@/components/BdotSourceLine";

/**
 * Najbliższa droga z lokalnego wydania BDOT10k (Task 23.2).
 *
 * Pokazuje fakty geometryczne (odległość od poligonu działki do osi jezdni, relację) oraz opis ze źródła (kategoria,
 * klasa). Uwaga o braku potwierdzenia prawnego dostępu do drogi publicznej jest widoczna zawsze — także gdy danych
 * brak — a stany bez drogi (pusty promień, brak pokrycia, awaria) mają osobne komunikaty.
 */
export function RoadContextCard({ road }: { road: RoadContextResult | null | undefined }) {
  const value = roadContextOrUnknown(road);
  const note = roadStatusNote(value.status);
  const reason = value.status === "available" ? null : bdotReasonLabel(value.reason_code);
  const nearest = value.nearest;

  return (
    <section className="result-section" aria-label="Droga i dostęp do drogi">
      <h3>Droga i dostęp do drogi</h3>
      <dl className="result-summary bdot-summary">
        <div>
          <dt>Status</dt>
          <dd data-testid="road-status" data-status={value.status}>
            {bdotStatusLabel(value.status)}
            {reason ? ` (${reason})` : ""}
          </dd>
        </div>
        {nearest && (
          <>
            <div>
              <dt>Odległość do osi jezdni</dt>
              <dd data-testid="road-distance">{formatMeters(nearest.distance_m)}</dd>
            </div>
            <div>
              <dt>Relacja geometryczna</dt>
              <dd data-testid="road-relation">{ROAD_RELATION_LABELS[nearest.relation]}</dd>
            </div>
            <div>
              <dt>Kategoria zarządzania (źródło)</dt>
              <dd data-testid="road-category">{nearest.category ?? "nie określono"}</dd>
            </div>
            <div>
              <dt>Klasa drogi (źródło)</dt>
              <dd data-testid="road-class">{nearest.road_class ?? "nie określono"}</dd>
            </div>
            {(nearest.road_number || nearest.street_name) && (
              <div>
                <dt>Numer / ulica</dt>
                <dd>
                  {nearest.road_number ?? "—"} / {nearest.street_name ?? "—"}
                </dd>
              </div>
            )}
            <div>
              <dt>Nawierzchnia, szerokość, położenie</dt>
              <dd>
                {nearest.surface_material ?? "nie określono"};{" "}
                {nearest.surface_width_m != null ? formatMeters(nearest.surface_width_m, 1) : "nie określono"};{" "}
                {nearest.position ?? "nie określono"}
              </dd>
            </div>
            {value.search_radius_m != null && (
              <div>
                <dt>Promień wyszukiwania</dt>
                <dd data-testid="road-radius">
                  {formatMeters(value.search_radius_m, 0)} (maksymalnie{" "}
                  {formatMeters(value.max_search_radius_m, 0)})
                </dd>
              </div>
            )}
            {nearest.distance_m === 0 && (
              <div>
                <dt>Osie w kontakcie z działką</dt>
                <dd>
                  przecinające: {value.intersecting_road_count ?? "—"}; stykające się:{" "}
                  {value.touching_road_count ?? "—"}
                </dd>
              </div>
            )}
          </>
        )}
        {!nearest && value.status === "no_match" && value.search_radius_m != null && (
          <div>
            <dt>Sprawdzony promień</dt>
            <dd data-testid="road-radius">{formatMeters(value.search_radius_m, 0)}</dd>
          </div>
        )}
      </dl>
      <p className="result-preview" role="note" data-testid="road-note">
        {value.interpretation_note}
      </p>
      {note && (
        <p className="manual-review" data-testid="road-status-note">
          {note}
        </p>
      )}
      {value.search_area_within_coverage === false && (
        <p className="manual-review" data-testid="road-partial-coverage">
          Obszar wyszukiwania wykracza poza zasięg zaimportowanego powiatu — bliższa droga poza zasięgiem
          nie została sprawdzona.
        </p>
      )}
      <BdotReleaseLine release={value.release} formatDate={formatPlDate} testId="road-release" />
      <BdotSourceLine source={value.source} testId="road-source" label="BDOT10k" />
      {value.warnings.map((warning) => (
        <p key={warning} className="field-hint">
          {warning}
        </p>
      ))}
    </section>
  );
}
