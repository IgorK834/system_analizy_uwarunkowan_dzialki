"use client";

import { LAYER_STATE_LABELS } from "@/lib/layerState";
import type { LayerState } from "@/lib/types";

/**
 * Generyczny przełącznik warstw mapowych z legendą kolorów.
 *
 * Używany zarówno dla warstw GeoJSON (obrys działki, obszar zabudowy), jak
 * i dla nakładek WMS (MPZP, POG) — logika włączania/wyłączania konkretnej
 * warstwy na mapie należy do wywołującego (ResultPanel/PreviewOverlays),
 * ten komponent tylko renderuje stan i zgłasza zmiany przez onChange.
 */

export type LayerToggleItem = {
  id: string;
  label: string;
  color: string;
  checked: boolean;
  disabled?: boolean;
  disabledReason?: string;
  status?: string;
  /**
   * Stan warstwy (BK-406) — osobno od statusu prawnego i od włączenia
   * przełącznika; pokazywany tekstem, nie tylko kolorem.
   */
  state?: LayerState;
};

export type LayerToggleProps = {
  items: LayerToggleItem[];
  onChange: (id: string, checked: boolean) => void;
  legendLabel?: string;
};

export function LayerToggle({
  items,
  onChange,
  legendLabel = "Warstwy mapy",
}: LayerToggleProps) {
  return (
    <fieldset className="layer-toggle">
      <legend>{legendLabel}</legend>
      <ul className="layer-toggle-list">
        {items.map((item) => (
          <li key={item.id} className="layer-toggle-item">
            <button
              type="button"
              role="switch"
              aria-checked={item.checked}
              disabled={item.disabled}
              title={item.disabled ? item.disabledReason : undefined}
              className={
                item.checked
                  ? "layer-toggle-button layer-toggle-active"
                  : "layer-toggle-button"
              }
              onClick={() => onChange(item.id, !item.checked)}
            >
              <span
                className="layer-toggle-swatch"
                style={{ backgroundColor: item.color }}
                aria-hidden="true"
              />
              <span>{item.label}</span>
            </button>
            {item.disabled && item.disabledReason && (
              <p className="layer-toggle-disabled-reason">{item.disabledReason}</p>
            )}
            {item.state && (
              <p className="layer-toggle-state">
                Stan warstwy:{" "}
                <span
                  className={`layer-state-chip layer-state-chip-${item.state}`}
                  data-layer-state={item.state}
                >
                  {LAYER_STATE_LABELS[item.state]}
                </span>
              </p>
            )}
            {item.status && (
              <p className="layer-toggle-status">{item.status}</p>
            )}
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
