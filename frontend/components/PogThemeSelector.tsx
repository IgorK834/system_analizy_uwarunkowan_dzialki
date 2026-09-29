"use client";

import {
  POG_THEMES,
  type PogTheme,
  type PogThemeId,
  themeById,
  themeScaleCaption,
} from "@/lib/pogThemes";

export type PogThemeSelectorProps = {
  value: PogThemeId;
  onChange: (theme: PogThemeId) => void;
  disabled?: boolean;
};

function unitHint(theme: PogTheme): string {
  if (theme.kind === "categorical") return "kody SW…SK";
  if (theme.unit === "1") return "bezwymiarowa";
  return theme.unit ?? "";
}

/**
 * Przełącznik pięciu trybów tematycznych POG (BK-402).
 *
 * Natywna grupa `radio` daje obsługę klawiaturą (Tab, strzałki) i poprawną
 * semantykę dla czytników ekranu. Zmiana trybu wywołuje wyłącznie `onChange` —
 * nie pobiera danych i nie uruchamia analizy.
 */
export function PogThemeSelector({ value, onChange, disabled = false }: PogThemeSelectorProps) {
  const selected = themeById(value);
  return (
    <fieldset className="pog-theme-selector" disabled={disabled}>
      <legend>Tryb mapy planu ogólnego</legend>
      <div className="pog-theme-options">
        {POG_THEMES.map((theme) => (
          <label key={theme.id} className="pog-theme-option">
            <input
              type="radio"
              name="pog-theme"
              value={theme.id}
              checked={value === theme.id}
              onChange={() => onChange(theme.id)}
              aria-describedby="pog-theme-scale"
            />
            <span className="pog-theme-label">{theme.label}</span>
            <span className="pog-theme-unit">({unitHint(theme)})</span>
          </label>
        ))}
      </div>
      <p id="pog-theme-scale" className="pog-theme-scale" aria-live="polite">
        {selected.kind === "numeric" && (
          <span className="pog-theme-direction" aria-hidden="true">
            {selected.direction === "descending" ? "↓ " : "↑ "}
          </span>
        )}
        {themeScaleCaption(selected)}
      </p>
    </fieldset>
  );
}
