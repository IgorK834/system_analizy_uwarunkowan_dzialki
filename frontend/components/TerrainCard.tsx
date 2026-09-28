import { formatPlDate } from "@/lib/pogStatus";
import {
  aspectDescription,
  formatDegrees,
  formatMeters,
  formatNumber,
  formatPercent,
  profileChart,
  reasonLabel,
  terrainOrUnknown,
  terrainStatusLabel,
  terrainStatusNote,
} from "@/lib/terrain";
import type { SourceMetadata, TerrainReliefResult, TerrainResult } from "@/lib/types";

const PROFILE_WIDTH = 320;
const PROFILE_HEIGHT = 110;

/**
 * Rzeźba terenu działki (BK-301) i pochodne rastra NMT (BK-302).
 *
 * Wysokości są pokazywane wyłącznie dla zmierzonego wyniku. Brak pokrycia,
 * niedostępność usługi i zapis bez danych mają osobne komunikaty i nigdy nie są
 * prezentowane jako płaski teren.
 */
export function TerrainCard({ terrain }: { terrain: TerrainResult | null | undefined }) {
  const value = terrainOrUnknown(terrain);
  const note = terrainStatusNote(value);
  const reason = value.status === "available" ? null : reasonLabel(value.reason_code);

  return (
    <section className="result-section" aria-label="Rzeźba terenu (NMT)">
      <h3>Rzeźba terenu (NMT)</h3>
      <dl className="result-summary terrain-summary">
        <div>
          <dt>Status pomiaru</dt>
          <dd data-testid="terrain-status" data-status={value.status}>
            {terrainStatusLabel(value.status)}
            {reason ? ` (${reason})` : ""}
          </dd>
        </div>
        {value.status === "available" && (
          <>
            <div>
              <dt>Najniższa wysokość (Hmin)</dt>
              <dd data-testid="terrain-min">{formatMeters(value.min_height_m)}</dd>
            </div>
            <div>
              <dt>Najwyższa wysokość (Hmax)</dt>
              <dd data-testid="terrain-max">{formatMeters(value.max_height_m)}</dd>
            </div>
            <div>
              <dt>Deniwelacja</dt>
              <dd data-testid="terrain-height-difference">
                {formatMeters(value.height_difference_m)}
              </dd>
            </div>
          </>
        )}
        {value.grid_size_m != null && (
          <div>
            <dt>Siatka próbkowania</dt>
            <dd>{formatMeters(value.grid_size_m)}</dd>
          </div>
        )}
        {value.sampled_points != null && (
          <div>
            <dt>Punkty siatki</dt>
            <dd>{value.sampled_points.toLocaleString("pl-PL")}</dd>
          </div>
        )}
      </dl>
      {note && (
        <p
          className={value.status === "available" ? "result-preview" : "manual-review"}
          data-testid="terrain-note"
        >
          {note}
        </p>
      )}
      <SourceLine source={value.source} testId="terrain-source" />
      {value.warnings.map((warning) => (
        <p key={warning} className="field-hint">
          {warning}
        </p>
      ))}
      <ReliefSection relief={value.relief} />
    </section>
  );
}

function ReliefSection({ relief }: { relief: TerrainReliefResult | null }) {
  if (!relief) {
    return (
      <p className="section-empty" data-testid="relief-missing">
        Nie liczono spadku, ekspozycji ani profilu dla tej analizy.
      </p>
    );
  }
  const reason = relief.status === "available" ? null : reasonLabel(relief.reason_code);
  return (
    <div className="terrain-relief" data-testid="terrain-relief" data-status={relief.status}>
      <h4>Spadek, ekspozycja i profil (raster NMT)</h4>
      {relief.status !== "available" && (
        <p className="manual-review" data-testid="relief-unavailable">
          Spadku, ekspozycji i profilu nie policzono — {terrainStatusLabel(relief.status)}
          {reason ? ` (${reason})` : ""}. Brak statystyk nie oznacza płaskiego terenu.
        </p>
      )}
      {relief.status === "available" && relief.slope && (
        <>
          <dl className="result-summary terrain-summary">
            <div>
              <dt>Rozdzielczość danych</dt>
              <dd data-testid="relief-resolution">{formatMeters(relief.resolution_m)}</dd>
            </div>
            <div>
              <dt>Zmierzona część działki</dt>
              <dd>{formatPercent(relief.valid_area_share_pct)}</dd>
            </div>
            <div>
              <dt>Ekspozycja</dt>
              <dd data-testid="relief-aspect">
                {relief.aspect ? aspectDescription(relief.aspect) : "—"}
              </dd>
            </div>
          </dl>
          <div className="result-table-scroll">
            <table className="result-table">
              <caption>Spadek terenu w obrysie działki</caption>
              <thead>
                <tr>
                  <th>Miara</th>
                  <th>Stopnie</th>
                  <th>Procent</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ["Średni", relief.slope.mean_deg, relief.slope.mean_pct],
                    ["Mediana", relief.slope.median_deg, relief.slope.median_pct],
                    ["P90", relief.slope.p90_deg, relief.slope.p90_pct],
                    ["Maksymalny", relief.slope.max_deg, relief.slope.max_pct],
                  ] as const
                ).map(([label, degrees, percent]) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    <td>{formatDegrees(degrees)}</td>
                    <td>{formatPercent(percent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="result-table-scroll">
            <table className="result-table" data-testid="relief-classes">
              <caption>Klasy nachylenia ({relief.slope_classes_version})</caption>
              <thead>
                <tr>
                  <th>Klasa</th>
                  <th>Powierzchnia</th>
                  <th>Udział</th>
                </tr>
              </thead>
              <tbody>
                {relief.slope_classes.map((item) => (
                  <tr key={item.class_id}>
                    <th scope="row">{item.label}</th>
                    <td>{formatNumber(item.area_sqm, 0)} m²</td>
                    <td>{formatPercent(item.share_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ProfileChart relief={relief} />
          {relief.raster && (
            <p className="field-hint" data-testid="relief-raster">
              Raster {relief.raster.coverage_id}
              {relief.raster.vertical_datum ? `, układ wysokości ${relief.raster.vertical_datum}` : ""},{" "}
              {relief.raster.width_px}×{relief.raster.height_px} px (bufor{" "}
              {formatMeters(relief.raster.buffer_m)}); algorytm {relief.algorithm_version}
              {relief.raster.gdal_version ? `; ${relief.raster.gdal_version}` : ""}.
            </p>
          )}
        </>
      )}
      <SourceLine source={relief.source} testId="relief-source" />
      {relief.warnings.map((warning) => (
        <p key={warning} className="field-hint">
          {warning}
        </p>
      ))}
    </div>
  );
}

function ProfileChart({ relief }: { relief: TerrainReliefResult }) {
  const profile = relief.profile;
  if (!profile) return null;
  const chart = profileChart(profile, PROFILE_WIDTH, PROFILE_HEIGHT);
  const description =
    `Profil wysokościowy wzdłuż dłuższej osi działki: długość ${formatMeters(profile.length_m)}, ` +
    `krok ${formatMeters(profile.step_m)}, ${profile.samples.length} próbek` +
    (chart.missingCount ? `, w tym ${chart.missingCount} bez danych` : "") +
    (chart.minHeight != null
      ? `; wysokości od ${formatMeters(chart.minHeight)} do ${formatMeters(chart.maxHeight)}.`
      : "; brak wysokości do narysowania.");
  return (
    <figure className="terrain-profile" data-testid="relief-profile">
      {chart.segments.length > 0 && (
        <svg
          viewBox={`0 0 ${PROFILE_WIDTH} ${PROFILE_HEIGHT}`}
          role="img"
          aria-label={description}
          width="100%"
          height={PROFILE_HEIGHT}
        >
          <rect x="0" y="0" width={PROFILE_WIDTH} height={PROFILE_HEIGHT} className="terrain-profile-bg" />
          {chart.segments.map((points) => (
            <polyline key={points} points={points} className="terrain-profile-line" />
          ))}
        </svg>
      )}
      <figcaption className="field-hint">{description}</figcaption>
    </figure>
  );
}

function SourceLine({ source, testId }: { source: SourceMetadata | null; testId: string }) {
  if (!source) {
    return (
      <p className="section-empty" data-testid={testId}>
        Brak metadanych źródła NMT.
      </p>
    );
  }
  const fetched = formatPlDate(source.fetched_at);
  return (
    <p className="field-hint" data-testid={testId}>
      Źródło: {source.source_name}
      {source.source_version ? ` (${source.source_version})` : ""}
      {fetched ? `, pobrano ${fetched}` : ""} · pewność {(source.confidence * 100).toFixed(0)}%
      {source.manual_review_required ? " · wymaga weryfikacji" : ""}
    </p>
  );
}
