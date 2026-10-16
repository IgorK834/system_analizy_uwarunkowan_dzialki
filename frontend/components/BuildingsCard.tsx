import { BdotReleaseLine, BdotSourceLine } from "@/components/BdotSourceLine";
import {
  BUILDING_RELATION_LABELS,
  bdotReasonLabel,
  bdotStatusLabel,
  buildingsOrUnknown,
  buildingsStatusNote,
  formatMeters,
  formatNumber,
  formatPct,
  formatSqm,
} from "@/lib/bdot";
import { formatPlDate } from "@/lib/pogStatus";
import type { BuildingBufferResult, NeighbourBuildingsResult } from "@/lib/types";

const TABLE_ROWS = 25;

/**
 * Zabudowa sąsiednia z obrysów BDOT10k (Task 23.3): budynki na działce i w buforach 50/100/200 m.
 *
 * To kontekst istniejącej zabudowy, a nie ocena możliwości zabudowy. Statystyki dotyczą pierścienia
 * `buffer(działka, r) − działka` (kumulatywnie); zero budynków jest pokazane jako 0, a wielkości bez obiektów jako „—”
 * (null ≠ 0). Brak pokrycia i awaria zapytania nie są prezentowane jako brak budynków.
 */
export function BuildingsCard({ buildings }: { buildings: NeighbourBuildingsResult | null | undefined }) {
  const value = buildingsOrUnknown(buildings);
  const note = buildingsStatusNote(value.status);
  const reason = value.status === "available" ? null : bdotReasonLabel(value.reason_code);
  const shown = value.buildings.slice(0, TABLE_ROWS);

  return (
    <section className="result-section" aria-label="Zabudowa sąsiednia">
      <h3>Zabudowa sąsiednia</h3>
      <dl className="result-summary bdot-summary">
        <div>
          <dt>Status</dt>
          <dd data-testid="buildings-status" data-status={value.status}>
            {bdotStatusLabel(value.status)}
            {reason ? ` (${reason})` : ""}
          </dd>
        </div>
        {value.on_parcel && (
          <>
            <div>
              <dt>Budynki na działce</dt>
              <dd data-testid="buildings-on-parcel">{value.on_parcel.count}</dd>
            </div>
            <div>
              <dt>Pole zabudowy na działce</dt>
              <dd>{formatSqm(value.on_parcel.union_area_in_parcel_sqm)}</dd>
            </div>
            <div>
              <dt>Pokrycie działki zabudową</dt>
              <dd data-testid="buildings-coverage">{formatPct(value.on_parcel.coverage_pct)}</dd>
            </div>
          </>
        )}
      </dl>
      <p className="result-preview" role="note" data-testid="buildings-note">
        {value.interpretation_note}
      </p>
      {note && (
        <p className="manual-review" data-testid="buildings-status-note">
          {note}
        </p>
      )}
      {value.search_area_within_coverage === false && (
        <p className="manual-review" data-testid="buildings-partial-coverage">
          Największy bufor wykracza poza zasięg zaimportowanego powiatu — liczby dotyczą wyłącznie pokrytej
          części otoczenia i mogą być zaniżone.
        </p>
      )}
      {value.buffers.length > 0 && <BufferTable buffers={value.buffers} />}
      {value.buffers.length > 0 && (
        <p className="field-hint">
          Sąsiedztwo o promieniu r to pierścień {value.ring_definition} mierzony od obrysu działki w EPSG:2180;
          statystyki są kumulatywne (bufor 100 m zawiera bufor 50 m), a budynek liczy się raz.
        </p>
      )}
      {shown.length > 0 && (
        <div
          className="result-table-scroll"
          role="region"
          aria-label="Najbliższe budynki (przewijana tabela)"
          // Przewijana tabela musi być dostępna z klawiatury (WCAG 2.1.1).
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
        >
          <table className="result-table" data-testid="buildings-list">
            <caption>
              Najbliższe budynki (do {formatMeters(value.listed_radius_m, 0)} oraz na działce)
            </caption>
            <thead>
              <tr>
                <th scope="col">Budynek</th>
                <th scope="col">Odległość</th>
                <th scope="col">Położenie</th>
                <th scope="col">Pole obrysu</th>
                <th scope="col">Funkcja</th>
                <th scope="col">Kondygnacje</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((item) => (
                <tr key={item.building_id}>
                  <td>
                    <code>{item.building_id.slice(0, 8)}…</code>
                  </td>
                  <td>{formatMeters(item.distance_m)}</td>
                  <td>{BUILDING_RELATION_LABELS[item.relation]}</td>
                  <td>{formatSqm(item.footprint_sqm)}</td>
                  <td>{item.general_function ?? "nie określono"}</td>
                  <td>{item.floors ?? "nie określono"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(value.buildings_truncated || value.buildings.length > TABLE_ROWS) && (
        <p className="field-hint" data-testid="buildings-truncated">
          Pokazano {shown.length} z {value.buildings.length} najbliższych budynków; pełna lista jest w pakiecie
          audytowym.
        </p>
      )}
      <BdotReleaseLine release={value.release} formatDate={formatPlDate} testId="buildings-release" />
      <BdotSourceLine source={value.source} testId="buildings-source" label="BDOT10k" />
      {value.warnings.map((warning) => (
        <p key={warning} className="field-hint">
          {warning}
        </p>
      ))}
    </section>
  );
}

function BufferTable({ buffers }: { buffers: BuildingBufferResult[] }) {
  return (
    <div
      className="result-table-scroll"
      role="region"
      aria-label="Zabudowa w buforach (przewijana tabela)"
      // Przewijana tabela musi być dostępna z klawiatury (WCAG 2.1.1).
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
    >
      <table className="result-table" data-testid="buildings-buffers">
        <caption>Zabudowa w buforach od obrysu działki</caption>
        <thead>
          <tr>
            <th scope="col">Bufor</th>
            <th scope="col">Budynki</th>
            <th scope="col">w tym zachodzące na działkę</th>
            <th scope="col">Pole sumy obrysów</th>
            <th scope="col">Udział pierścienia</th>
            <th scope="col">Min. odległość</th>
            <th scope="col">Pole obrysu min / mediana / maks.</th>
          </tr>
        </thead>
        <tbody>
          {buffers.map((item) => (
            <tr key={item.radius_m} data-radius={item.radius_m}>
              <td>{formatMeters(item.radius_m, 0)}</td>
              <td data-testid={`buildings-count-${item.radius_m}`}>{item.count}</td>
              <td>{item.straddling_parcel_count}</td>
              <td>{formatSqm(item.union_area_in_ring_sqm)}</td>
              <td>{formatPct(item.built_share_pct)}</td>
              <td>{formatMeters(item.min_distance_m)}</td>
              <td>
                {item.footprint_median_sqm == null
                  ? "—"
                  : `${formatNumber(item.footprint_min_sqm, 1)} / ${formatNumber(item.footprint_median_sqm, 1)} / ${formatNumber(item.footprint_max_sqm, 1)} m²`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
