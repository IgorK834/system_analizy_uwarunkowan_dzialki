/**
 * Pięć trybów tematycznych POG (BK-402) nad wspólnym artefaktem (BK-403).
 *
 * Tryb mapuje się na jedno z czterech dokładnych pól kontraktu BK-105/BK-401
 * (`max_overground_floor_area_ratio`, `max_building_coverage_pct`,
 * `max_building_height_m`, `min_biologically_active_pct`) albo na `zone_code`.
 * Progi, jednostki, kolory i opis kierunku skali pochodzą z
 * `shared/pog-presentation.json`; wyrażenia MapLibre i legenda są budowane z
 * tych samych obiektów, więc nie mogą się rozjechać.
 *
 * Brak wartości (`NULL` — brak klucza w cechze MVT) jest sprawdzany jawnie
 * (`has` / `== null`) PRZED konwersją liczby, dlatego nigdy nie trafia do klasy
 * zawierającej 0.
 */
import {
  POG_NULL_STYLE,
  POG_UNKNOWN_ZONE,
  POG_ZONE_CODES,
  POG_ZONES,
  type PogPatternId,
  type PogPresentationConfig,
  type PogThemeClassConfig,
  type PogThemeConfig,
  POG_PATTERNED_LEGAL_STATUSES,
  legalStatusPattern,
  zoneFillColorExpression,
} from "@/lib/pogZones";
// Progi, jednostki i opisy skali czytamy z tego samego artefaktu co strefy;
// walidację kontraktu wykonuje `pogZones` przy imporcie.
import rawPresentation from "../../shared/pog-presentation.json";

export type PogThemeId =
  | "zones"
  | "intensity"
  | "building_coverage"
  | "height"
  | "biologically_active";

export type PogParameterName =
  | "max_overground_floor_area_ratio"
  | "max_building_height_m"
  | "max_building_coverage_pct"
  | "min_biologically_active_pct";

export const POG_THEME_IDS: readonly PogThemeId[] = [
  "zones",
  "intensity",
  "building_coverage",
  "height",
  "biologically_active",
];

export const POG_PARAMETER_BY_THEME: Readonly<Record<Exclude<PogThemeId, "zones">, PogParameterName>> = {
  intensity: "max_overground_floor_area_ratio",
  building_coverage: "max_building_coverage_pct",
  height: "max_building_height_m",
  biologically_active: "min_biologically_active_pct",
};

export const DEFAULT_POG_THEME: PogThemeId = "zones";

export type PogTheme = PogThemeConfig & { id: PogThemeId };

const THEMES: readonly PogTheme[] = (rawPresentation as PogPresentationConfig).themes.map((theme) => {
  if (!POG_THEME_IDS.includes(theme.id as PogThemeId)) {
    throw new Error(`Nieznany tryb POG ${theme.id}.`);
  }
  return theme as PogTheme;
});

if (THEMES.length !== POG_THEME_IDS.length) {
  throw new Error("Artefakt prezentacji POG musi definiować dokładnie pięć trybów.");
}

export const POG_THEMES: readonly PogTheme[] = POG_THEME_IDS.map(
  (id) => THEMES.find((theme) => theme.id === id) as PogTheme,
);

export function isPogThemeId(value: unknown): value is PogThemeId {
  return typeof value === "string" && POG_THEME_IDS.includes(value as PogThemeId);
}

export function themeById(id: PogThemeId): PogTheme {
  return POG_THEMES.find((theme) => theme.id === id) as PogTheme;
}

function classes(theme: PogTheme): PogThemeClassConfig[] {
  return theme.classes ?? [];
}

/** Progi rozpoczynające klasy 2..n (domknięcie lewostronne: [min, max)). */
export function themeBreaks(theme: PogTheme): number[] {
  return classes(theme)
    .slice(1)
    .map((item) => item.min);
}

/** Klasa wartości; `null`/`undefined`/NaN nie należą do żadnej klasy. */
export function classForValue(
  theme: PogTheme,
  value: number | null | undefined,
): PogThemeClassConfig | null {
  if (theme.kind !== "numeric" || value === null || value === undefined || Number.isNaN(value)) {
    return null;
  }
  let chosen = classes(theme)[0];
  for (const item of classes(theme).slice(1)) {
    if (value >= item.min) chosen = item;
  }
  return chosen;
}

/** Kolor wartości zgodny z wyrażeniem mapy (brak wartości → styl „brak wartości”). */
export function colorForValue(theme: PogTheme, value: number | null | undefined): string {
  return classForValue(theme, value)?.color ?? POG_NULL_STYLE.fill;
}

function missingValueCondition(property: string): unknown[] {
  return ["any", ["!", ["has", property]], ["==", ["get", property], null]];
}

/**
 * `fill-color` warstwy stref dla trybu. Tryb liczbowy: najpierw jawny przypadek
 * braku wartości, dopiero potem `to-number` i `step` po progach artefaktu.
 */
export function themeFillColorExpression(theme: PogTheme): unknown[] {
  if (theme.kind === "categorical") return zoneFillColorExpression();
  const [first, ...rest] = classes(theme);
  return [
    "case",
    missingValueCondition(theme.property),
    POG_NULL_STYLE.fill,
    [
      "step",
      ["to-number", ["get", theme.property]],
      first.color,
      ...rest.flatMap((item) => [item.min, item.color]),
    ],
  ];
}

/** Krycie warstwy wzoru: 1 tylko dla braku wartości (tryb liczbowy) lub kodu spoza słownika. */
export function themePatternOpacityExpression(theme: PogTheme): unknown[] {
  if (theme.kind === "categorical") {
    return ["match", ["get", "zone_code"], [...POG_ZONE_CODES], 0, 1];
  }
  return ["case", missingValueCondition(theme.property), 1, 0];
}

export const POG_PATTERN_IMAGE_PREFIX = "pog-pattern-";

export function patternImageId(pattern: PogPatternId): string {
  return `${POG_PATTERN_IMAGE_PREFIX}${pattern}`;
}

/**
 * BK-406: obraz wzoru danych niewiążących (projekt / akt w trakcie). Jest
 * niezależny od trybu tematycznego — kolor mówi o wartości parametru, a wzór i
 * plakietka o statusie prawnym, więc oba przekazy nie konkurują ze sobą.
 */
export function legalStatusPatternImage(): string {
  return patternImageId(legalStatusPattern().pattern);
}

/** Filtr cech rysowanych wzorem danych niewiążących. */
export function legalStatusPatternFilter(): unknown[] {
  return ["in", ["get", "legal_status"], ["literal", [...POG_PATTERNED_LEGAL_STATUSES]]];
}

/** Obraz wzoru warstwy „brak wartości / kod nierozpoznany” dla trybu. */
export function themePatternImage(theme: PogTheme): string {
  return patternImageId(
    theme.kind === "categorical" ? POG_UNKNOWN_ZONE.pattern : POG_NULL_STYLE.pattern,
  );
}

const NUMBER_FORMAT = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 2 });

/** Wartość z jednostką: bezwymiarowa bez sufiksu, `m` ze spacją, `%` bez. */
export function formatThemeValue(theme: PogTheme, value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return POG_NULL_STYLE.label;
  }
  const number = NUMBER_FORMAT.format(value);
  if (theme.unit === "m") return `${number} m`;
  if (theme.unit === "%") return `${number}%`;
  return number;
}

export type PogLegendItem = {
  key: string;
  label: string;
  color: string;
  pattern?: PogPatternId;
  description?: string;
};

/** Pozycje legendy trybu — te same kolory i progi co `themeFillColorExpression`. */
export function themeLegendItems(theme: PogTheme): PogLegendItem[] {
  if (theme.kind === "categorical") {
    return [
      ...POG_ZONES.map((zone) => ({
        key: zone.code,
        label: `${zone.code} — ${zone.label}`,
        color: zone.fill,
      })),
      {
        key: "unknown",
        label: POG_UNKNOWN_ZONE.label,
        color: POG_UNKNOWN_ZONE.fill,
        pattern: POG_UNKNOWN_ZONE.pattern,
        description: POG_UNKNOWN_ZONE.description,
      },
    ];
  }
  return [
    ...classes(theme).map((item) => ({
      key: `class-${item.min}`,
      label: item.label,
      color: item.color,
    })),
    {
      key: "null",
      label: POG_NULL_STYLE.label,
      color: POG_NULL_STYLE.fill,
      pattern: POG_NULL_STYLE.pattern,
      description: POG_NULL_STYLE.description,
    },
  ];
}

/** Opis jednostki i kierunku skali do legendy/aria. */
export function themeScaleCaption(theme: PogTheme): string {
  if (theme.kind === "categorical") return theme.scale_description;
  return `Jednostka: ${theme.unit_label}${theme.unit && theme.unit !== "1" ? ` (${theme.unit})` : ""}. ${theme.scale_description}`;
}
