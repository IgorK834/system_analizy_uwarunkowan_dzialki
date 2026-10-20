import { formatPlDate } from "@/lib/pogStatus";
import { BdotReleaseLine, BdotSourceLine } from "@/components/BdotSourceLine";
import { bdotStatusLabel, formatMeters, formatSqm, formatNumber } from "@/lib/bdot";
import { LAND_COVER_COLORS } from "@/lib/layerStyles";
import type { LandCoverResult, LandCoverZone } from "@/lib/types";

function statusLabel(status: LandCoverResult["status"]) {
  return status === "unknown" ? "nieustalony — brak pełnych danych" : bdotStatusLabel(status);
}

function ShareTable({ title, zone }: { title: string; zone: LandCoverZone | null | undefined }) {
  if (!zone) return null;
  return <div className="result-table-scroll" role="region" aria-label={title}>
    <table className="result-table"><caption>{title} ({formatSqm(zone.area_sqm)})</caption>
      <thead><tr><th scope="col">Klasa BDOT10k</th><th scope="col">Pole</th><th scope="col">Udział</th></tr></thead>
      <tbody>{zone.classes.map(c => <tr key={c.class_code}><td><span aria-hidden="true" style={{display:"inline-block",width:10,height:10,marginRight:6,background:LAND_COVER_COLORS[c.class_code]}} />{c.label}</td><td>{formatSqm(c.area_sqm)}</td>
        <td>{formatNumber(c.share_pct, 2)}%</td></tr>)}</tbody>
      <tfoot><tr><th scope="row">Suma sklasyfikowana</th><td>—</td><td>{formatNumber(zone.classified_share_pct, 2)}%</td></tr></tfoot>
    </table></div>;
}

export function LandCoverCard({ value }: { value: LandCoverResult | null | undefined }) {
  return <section className="result-section" aria-label="Pokrycie terenu i lasy">
    <h3>Pokrycie terenu i lasy</h3>
    <p data-testid="land-cover-status">{statusLabel(value?.status ?? "unknown")}</p>
    {value?.status !== "available" && <p className="manual-review">Pokrycia terenu nie ustalono w pełni.
      Brak danych nie oznacza braku lasu ani ograniczeń.</p>}
    <ShareTable title="Na działce" zone={value?.parcel} />
    <ShareTable title="Otoczenie do 200 m od granic działki (bez działki)" zone={value?.surroundings} />
    <dl className="result-summary">
      <div><dt>Najbliższy las (od obrysu działki)</dt><dd>{formatMeters(value?.forest.distance_m)}</dd></div>
      <div><dt>Status pomiaru lasu</dt><dd>{statusLabel(value?.forest.status ?? "unknown")}</dd></div>
    </dl>
    {value?.forest.status === "no_match" && <p>Nie znaleziono lasu w promieniu {formatMeters(value.forest.search_radius_m)}.
      Nie dowodzi to braku lasu poza zasięgiem wyszukiwania.</p>}
    {value?.forest.buffers.map(b => <p key={b.radius_m}>Orientacyjny bufor {b.radius_m} m:
      część działki w buforze {formatSqm(b.overlap_with_parcel_sqm)}.</p>)}
    <p className="result-preview" role="note">{value?.interpretation_note ??
      "Bufory 8/12/16 m to orientacyjne przybliżenie geometryczne, bez interpretacji przepisów. Obliczenia w EPSG:2180."}</p>
    {value?.warnings.map((w, i) => <p className="manual-review" key={i}>{w}</p>)}
    <BdotReleaseLine release={value?.release ?? null} formatDate={formatPlDate} testId="land-cover-release" />
    <BdotSourceLine source={value?.source ?? null} label="BDOT10k" testId="land-cover-source" />
  </section>;
}
