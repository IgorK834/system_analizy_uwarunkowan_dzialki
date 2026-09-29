"use client";

import { useEffect, useId, useState } from "react";

import { getPogAreaSummary } from "@/lib/api";
import { formatPlDate } from "@/lib/pogStatus";
import {
  POG_NULL_STYLE,
  POG_UNKNOWN_ZONE,
  isKnownZoneCode,
  type PogPatternId,
  zoneLabel,
  zoneStyle,
} from "@/lib/pogZones";
import type { PogAreaSummary as PogAreaSummaryValue, PogLegalStatus } from "@/lib/types";

const AREA = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const SHARE = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const INCOMPLETE_REASON_LABELS: Record<string, string> = {
  no_boundary: "brak granicy aktu w źródle — udziałów nie obliczono (to nie jest 100%)",
  no_zones: "brak stref w danych aktu",
  missing_area: "luka: część obszaru aktu nie ma przypisanej strefy",
  overlapping_zones: "strefy nakładają się na siebie",
  zones_outside_boundary: "część stref leży poza granicą aktu",
  share_sum_out_of_tolerance: "suma udziałów odbiega od 100% ponad tolerancję",
};

export function formatSqkm(value: number): string {
  return `${AREA.format(value)} km²`;
}

export function formatShare(value: number | null): string {
  return value === null ? "nie obliczono" : `${SHARE.format(value)}%`;
}

export type SummaryRow = {
  key: string;
  label: string;
  color: string;
  outline: string;
  pattern: PogPatternId | null;
  areaText: string;
  shareText: string;
  countText: string;
  /** Długość słupka 0–1: udział, a bez mianownika — pole względem największej strefy. */
  barFraction: number;
};

/**
 * Wiersze wspólne dla wykresu i tabeli — obie prezentacje formatują te same
 * liczby tą samą funkcją, więc nie mogą pokazać różnych wartości. Luka
 * (obszar aktu bez strefy) jest jawnym wierszem, a nie ukrytą resztą do 100%.
 */
export function summaryRows(summary: PogAreaSummaryValue): SummaryRow[] {
  const denominator = summary.denominator_area_sqkm;
  const maxArea = Math.max(...summary.zones.map((zone) => zone.area_sqkm), 0);
  const fraction = (areaSqkm: number, share: number | null) =>
    share !== null
      ? Math.max(0, Math.min(1, share / 100))
      : maxArea > 0
        ? areaSqkm / maxArea
        : 0;
  const rows: SummaryRow[] = summary.zones.map((zone) => {
    const style = zoneStyle(zone.zone_code);
    return {
      key: zone.zone_code,
      label: zoneLabel(zone.zone_code),
      color: style.fill,
      outline: style.outline,
      pattern: isKnownZoneCode(zone.zone_code) ? null : POG_UNKNOWN_ZONE.pattern,
      areaText: formatSqkm(zone.area_sqkm),
      shareText: formatShare(zone.share_pct),
      countText: String(zone.zone_count),
      barFraction: fraction(zone.area_sqkm, zone.share_pct),
    };
  });
  const missing = summary.missing_area_sqkm;
  if (denominator !== null && missing !== null && missing * 1e6 > summary.area_tolerance_sqm) {
    const share = (100 * missing) / denominator;
    rows.push({
      key: "missing",
      label: "luka — obszar aktu bez strefy w danych",
      color: POG_NULL_STYLE.fill,
      outline: POG_NULL_STYLE.outline,
      pattern: POG_NULL_STYLE.pattern,
      areaText: formatSqkm(missing),
      shareText: formatShare(share),
      countText: "—",
      barFraction: fraction(missing, share),
    });
  }
  return rows;
}

function editionForStatus(status: PogLegalStatus): "binding" | "project" | null {
  if (status === "binding") return "binding";
  if (status === "project" || status === "in_progress") return "project";
  return null;
}

type LoadState =
  | { status: "loading" }
  | { status: "ready"; summary: PogAreaSummaryValue }
  | { status: "not_found" }
  | { status: "error" };

export type PogAreaSummaryProps = {
  releaseId: number;
  actId: string;
  teryt?: string | null;
  legalStatus: PogLegalStatus;
};

const ROW_HEIGHT = 22;
const LABEL_WIDTH = 42;
const BAR_WIDTH = 190;

function SummaryChart({ rows, title, relative }: { rows: SummaryRow[]; title: string; relative: boolean }) {
  const titleId = useId();
  const patternPrefix = useId().replace(/:/g, "");
  const height = rows.length * ROW_HEIGHT + 4;
  return (
    <svg
      className="pog-area-chart"
      role="img"
      aria-labelledby={titleId}
      viewBox={`0 0 340 ${height}`}
      width="100%"
      data-testid="pog-area-chart"
    >
      <title id={titleId}>{title}</title>
      <defs>
        {rows
          .filter((row) => row.pattern)
          .map((row) => (
            <pattern
              key={row.key}
              id={`${patternPrefix}-${row.key}`}
              width="6"
              height="6"
              patternUnits="userSpaceOnUse"
            >
              <path d="M0 6 L6 0" stroke={row.outline} strokeWidth="1" />
            </pattern>
          ))}
      </defs>
      {rows.map((row, index) => {
        const y = index * ROW_HEIGHT + 2;
        const width = Math.max(1, row.barFraction * BAR_WIDTH);
        return (
          <g key={row.key} data-testid="pog-area-bar" data-key={row.key}>
            <text x="0" y={y + 14} className="pog-area-chart-label">
              {row.key === "missing" ? "luka" : row.key}
            </text>
            <rect x={LABEL_WIDTH} y={y} width={width} height={ROW_HEIGHT - 6} fill={row.color} stroke={row.outline} />
            {row.pattern && (
              <rect
                x={LABEL_WIDTH}
                y={y}
                width={width}
                height={ROW_HEIGHT - 6}
                fill={`url(#${patternPrefix}-${row.key})`}
              />
            )}
            <text x={LABEL_WIDTH + width + 4} y={y + 14} className="pog-area-chart-value" data-testid="pog-area-bar-value">
              {relative ? row.areaText : row.shareText}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Struktura powierzchniowa stref aktu albo gminy (BK-405): prosty wykres SVG i
 * równoważna tabela tekstowa z tymi samymi liczbami. Dane niepełne
 * (`is_complete = false`) są zawsze oznaczone wraz z przyczyną, a brak
 * mianownika nie jest pokazywany jako 100%.
 */
export function PogAreaSummary({ releaseId, actId, teryt = null, legalStatus }: PogAreaSummaryProps) {
  const edition = editionForStatus(legalStatus);
  const [scope, setScope] = useState<"act" | "municipality">("act");
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const name = useId();

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    const request =
      scope === "act" || !teryt || !edition
        ? { actId }
        : { teryt, edition };
    void getPogAreaSummary(releaseId, request, { signal: controller.signal })
      .then((summary) => {
        if (controller.signal.aborted) return;
        setState(summary ? { status: "ready", summary } : { status: "not_found" });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ status: "error" });
      });
    return () => controller.abort();
  }, [actId, edition, releaseId, scope, teryt]);

  const municipalityAvailable = Boolean(teryt && edition);
  return (
    <section className="pog-area-summary" aria-label="Struktura stref aktu lub gminy">
      <fieldset className="pog-area-summary-scope">
        <legend>Zakres struktury stref</legend>
        <label>
          <input
            type="radio"
            name={name}
            checked={scope === "act"}
            onChange={() => setScope("act")}
          />
          Akt
        </label>
        <label>
          <input
            type="radio"
            name={name}
            checked={scope === "municipality"}
            disabled={!municipalityAvailable}
            onChange={() => setScope("municipality")}
          />
          Gmina {teryt ?? ""}{" "}
          {edition === "project" ? "(projekty)" : edition === "binding" ? "(akty obowiązujące)" : ""}
        </label>
        {!municipalityAvailable && (
          <p className="pog-area-summary-note">
            Agregat gminy obejmuje akty obowiązujące albo projekty; akt o nieustalonym lub
            nieaktualnym statusie ma wyłącznie agregat aktu.
          </p>
        )}
      </fieldset>
      <div aria-live="polite">
        {state.status === "loading" && <p className="layer-toggle-status">Wczytywanie gotowego agregatu stref…</p>}
        {state.status === "not_found" && (
          <p className="layer-toggle-status">
            Agregat stref nie jest dostępny dla tego zakresu w wydaniu #{releaseId} (np. wydanie
            sprzed obliczania agregatów). Nie oznacza to braku stref ani planu.
          </p>
        )}
        {state.status === "error" && (
          <p className="layer-toggle-status">Nie udało się pobrać agregatu stref — spróbuj ponownie.</p>
        )}
      </div>
      {state.status === "ready" && <SummaryBody summary={state.summary} />}
    </section>
  );
}

function SummaryBody({ summary }: { summary: PogAreaSummaryValue }) {
  const rows = summaryRows(summary);
  const relative = summary.denominator_area_sqkm === null;
  const scopeLabel =
    summary.scope === "act"
      ? `aktu ${summary.act_id ?? ""}`
      : `gminy ${summary.teryt ?? ""} (${summary.edition === "project" ? "projekty" : "akty obowiązujące"}, ${summary.act_count} akt.)`;
  const title = relative
    ? `Powierzchnia stref ${scopeLabel} w km² — skala względna, udziałów nie obliczono`
    : `Udziały stref w powierzchni ${scopeLabel}`;
  return (
    <div className="pog-area-summary-body" data-complete={String(summary.is_complete)}>
      {!summary.is_complete && (
        <p className="pog-area-summary-partial" role="note" data-testid="pog-area-partial">
          <strong>Dane niepełne.</strong>{" "}
          {summary.incomplete_reasons
            .map((reason) => INCOMPLETE_REASON_LABELS[reason] ?? reason)
            .join("; ")}
          .
        </p>
      )}
      <p className="pog-area-summary-denominator">
        {summary.denominator_area_sqkm !== null
          ? `Mianownik: ${formatSqkm(summary.denominator_area_sqkm)} (${
              summary.scope === "act" ? "granica aktu" : "suma granic aktów bez podwójnego liczenia"
            } ze źródła).`
          : "Mianownik: brak — granica aktu nie występuje w źródle, więc udziały nie są liczone."}
      </p>
      <SummaryChart rows={rows} title={title} relative={relative} />
      <table className="pog-area-table" data-testid="pog-area-table">
        <caption>Tabela równoważna wykresowi: {title}</caption>
        <thead>
          <tr>
            <th scope="col">Strefa</th>
            <th scope="col">Powierzchnia</th>
            <th scope="col">Udział</th>
            <th scope="col">Obiekty</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} data-key={row.key}>
              <th scope="row">{row.label}</th>
              <td data-testid="pog-area-cell-area">{row.areaText}</td>
              <td data-testid="pog-area-cell-share">{row.shareText}</td>
              <td>{row.countText}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Suma udziałów stref</th>
            <td>{formatSqkm(summary.zones_area_sqkm)}</td>
            <td>{formatShare(summary.share_sum_pct)}</td>
            <td>{summary.zone_count}</td>
          </tr>
        </tfoot>
      </table>
      <p className="pog-area-summary-meta">
        Wydanie #{summary.release_id} ({summary.release_label}), metoda {summary.method_version},
        policzono {formatPlDate(summary.computed_at)} przy imporcie; tolerancja sumy udziałów ±
        {SHARE.format(summary.share_tolerance_pct)} pp.
        {summary.deduplicated_area_sqm
          ? ` Nakładające się akty policzono raz (${formatSqkm(summary.deduplicated_area_sqm / 1e6)}).`
          : ""}
      </p>
    </div>
  );
}
