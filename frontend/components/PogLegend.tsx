"use client";

import {
  POG_LEGAL_STATUS_STYLES,
  POG_OVERLAYS,
  POG_STYLE_VERSION,
  POG_ZONE_DICTIONARY,
  type PogPatternId,
} from "@/lib/pogZones";
import { type PogThemeId, themeById, themeLegendItems, themeScaleCaption } from "@/lib/pogThemes";

type SwatchProps = {
  color: string;
  pattern?: PogPatternId | null;
  outline?: string;
  dash?: number[] | null;
};

function patternMarkup(pattern: PogPatternId, color: string) {
  switch (pattern) {
    case "diagonal-hatch":
    case "diagonal-lines":
      return <path d="M0 6 L6 0 M-1 1 L1 -1 M5 7 L7 5" stroke={color} strokeWidth="1" />;
    case "cross-hatch":
      return <path d="M0 6 L6 0 M0 0 L6 6" stroke={color} strokeWidth="1" />;
    case "cross-lines":
      return <path d="M3 0 L3 6 M0 3 L6 3" stroke={color} strokeWidth="1" />;
    case "dots":
      return <circle cx="3" cy="3" r="1.2" fill={color} />;
    default:
      return null;
  }
}

/** Próbka legendy: kolor + wzór + obrys. Znaczenie przenosi zawsze tekst obok. */
export function PogSwatch({ color, pattern, outline = "#3d3d3d", dash }: SwatchProps) {
  const patternId = pattern ? `pog-swatch-${pattern}-${outline.replace("#", "")}` : null;
  return (
    <svg
      className="pog-swatch"
      width="22"
      height="14"
      viewBox="0 0 22 14"
      aria-hidden="true"
      focusable="false"
    >
      {patternId && pattern && (
        <defs>
          <pattern id={patternId} width="6" height="6" patternUnits="userSpaceOnUse">
            {patternMarkup(pattern, outline)}
          </pattern>
        </defs>
      )}
      <rect x="1" y="1" width="20" height="12" fill={color} />
      {patternId && <rect x="1" y="1" width="20" height="12" fill={`url(#${patternId})`} />}
      <rect
        x="1"
        y="1"
        width="20"
        height="12"
        fill="none"
        stroke={outline}
        strokeWidth="1.2"
        strokeDasharray={dash ? dash.map((value) => value * 1.2).join(" ") : undefined}
      />
    </svg>
  );
}

export type PogLegendProps = {
  theme: PogThemeId;
};

/**
 * Legenda mapy POG (BK-403). Pozycje, kolory i progi pochodzą z tych samych
 * funkcji adaptera co wyrażenia warstw MapLibre (`themeLegendItems`), a nakładki
 * OUZ/OZS/OSDIS mają wzór, obrys i opis tekstowy niezależny od barwy.
 */
export function PogLegend({ theme }: PogLegendProps) {
  const selected = themeById(theme);
  const items = themeLegendItems(selected);
  return (
    <section className="pog-legend" aria-label={`Legenda: ${selected.label}`}>
      <h3>{selected.label}</h3>
      <p className="pog-legend-caption">{themeScaleCaption(selected)}</p>
      <ul className="pog-legend-list">
        {items.map((item) => (
          <li
            key={item.key}
            className="pog-legend-item"
            data-testid="pog-legend-item"
            data-key={item.key}
            data-color={item.color}
            data-pattern={item.pattern ?? ""}
          >
            <PogSwatch color={item.color} pattern={item.pattern} />
            <span>{item.label}</span>
            {item.description && <span className="visually-hidden"> — {item.description}</span>}
          </li>
        ))}
      </ul>

      <h4>Obszary nakładające się na strefy</h4>
      <ul className="pog-legend-list">
        {POG_OVERLAYS.map((overlay) => (
          <li
            key={overlay.id}
            className="pog-legend-item"
            data-testid="pog-overlay-legend-item"
            data-key={overlay.id}
            data-pattern={overlay.pattern ?? ""}
          >
            <PogSwatch
              color="#ffffff"
              pattern={overlay.pattern}
              outline={overlay.outline}
              dash={overlay.line_dasharray}
            />
            <span>
              <strong>{overlay.short_label}</strong> — {overlay.label}.{" "}
              <small>{overlay.description}</small>
            </span>
          </li>
        ))}
      </ul>

      <h4>Status prawny aktu</h4>
      <ul className="pog-legend-list">
        {POG_LEGAL_STATUS_STYLES.map((status) => (
          <li key={status.status} className="pog-legend-item" data-testid="pog-status-legend-item">
            <PogSwatch
              color="#9aa5b1"
              outline="#3d3d3d"
              dash={status.line_dasharray}
            />
            <span>
              {status.label} <small>({status.description})</small>
            </span>
          </li>
        ))}
      </ul>
      <p className="pog-legend-meta">
        Styl {POG_STYLE_VERSION}. Lista stref: słownik {POG_ZONE_DICTIONARY.codelist} (
        {POG_ZONE_DICTIONARY.legal_basis}), zweryfikowano {POG_ZONE_DICTIONARY.verified_at}.
      </p>
    </section>
  );
}
