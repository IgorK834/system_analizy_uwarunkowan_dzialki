"use client";

import { PogSwatch } from "@/components/PogLegend";
import { POG_UNKNOWN_ZONE, isKnownZoneCode, zoneLabel, zoneStyle } from "@/lib/pogZones";
import type { PogZoneResult } from "@/lib/types";

const PERCENT = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 1 });

/**
 * Wykres udziałów stref POG w działce (BK-403). Kolory pochodzą z tego samego
 * adaptera `pogZones` co mapa, legenda i raport; strefa spoza słownika ma wzór,
 * a pełna informacja jest też w tekście (aria-label i legenda pod paskiem).
 */
export function PogZoneShareChart({ zones }: { zones: PogZoneResult[] }) {
  if (zones.length === 0) return null;
  const description = zones
    .map((zone) => `${zoneLabel(zone.type)}: ${PERCENT.format(zone.area_pct)}%`)
    .join("; ");
  return (
    <figure className="pog-share-figure">
      <div className="pog-share-chart" role="img" aria-label={`Udziały stref POG w działce: ${description}`}>
        {zones.map((zone) => {
          const style = zoneStyle(zone.type);
          const known = isKnownZoneCode(zone.type);
          return (
            <span
              key={zone.id}
              className="pog-share-segment"
              data-testid="pog-share-segment"
              data-color={style.fill}
              style={{
                width: `${Math.max(0, Math.min(100, zone.area_pct))}%`,
                backgroundColor: style.fill,
                backgroundImage: known
                  ? undefined
                  : `repeating-linear-gradient(45deg, ${POG_UNKNOWN_ZONE.outline} 0 1px, transparent 1px 5px)`,
              }}
            />
          );
        })}
      </div>
      <figcaption>
        <ul className="pog-legend-list">
          {zones.map((zone) => {
            const style = zoneStyle(zone.type);
            return (
              <li key={zone.id} className="pog-legend-item">
                <PogSwatch
                  color={style.fill}
                  outline={style.outline}
                  pattern={isKnownZoneCode(zone.type) ? null : POG_UNKNOWN_ZONE.pattern}
                />
                <span>
                  {zoneLabel(zone.type)} — {PERCENT.format(zone.area_pct)}%
                </span>
              </li>
            );
          })}
        </ul>
      </figcaption>
    </figure>
  );
}
