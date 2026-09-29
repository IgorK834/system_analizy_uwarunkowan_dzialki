import { describe, expect, it } from "vitest";

import rawPresentation from "../../shared/pog-presentation.json";
import {
  DEFAULT_POG_THEME,
  POG_PARAMETER_BY_THEME,
  POG_THEME_IDS,
  POG_THEMES,
  classForValue,
  colorForValue,
  formatThemeValue,
  isPogThemeId,
  patternImageId,
  themeBreaks,
  themeById,
  themeFillColorExpression,
  themeLegendItems,
  themePatternImage,
  themePatternOpacityExpression,
  themeScaleCaption,
} from "@/lib/pogThemes";
import { POG_NULL_STYLE, POG_UNKNOWN_ZONE, POG_ZONE_CODES } from "@/lib/pogZones";

type RawTheme = (typeof rawPresentation.themes)[number];

const NUMERIC = POG_THEMES.filter((theme) => theme.kind === "numeric");

/** Wyciąga progi i kolory z wyrażenia `case → step` warstwy MapLibre. */
function stepFromExpression(expression: unknown[]): { colors: string[]; breaks: number[] } {
  const step = expression[3] as unknown[];
  expect(step[0]).toBe("step");
  const [, , first, ...rest] = step;
  const colors = [first as string];
  const breaks: number[] = [];
  for (let index = 0; index < rest.length; index += 2) {
    breaks.push(rest[index] as number);
    colors.push(rest[index + 1] as string);
  }
  return { colors, breaks };
}

describe("pogThemes — pięć trybów z artefaktu", () => {
  it("ma dokładnie pięć trybów w stałej kolejności i mapuje je na pola BK-105", () => {
    expect(POG_THEME_IDS).toEqual([
      "zones",
      "intensity",
      "building_coverage",
      "height",
      "biologically_active",
    ]);
    expect(POG_THEMES.map((theme) => theme.id)).toEqual(POG_THEME_IDS);
    for (const [themeId, property] of Object.entries(POG_PARAMETER_BY_THEME)) {
      expect(themeById(themeId as never).property).toBe(property);
    }
    expect(themeById("zones").property).toBe("zone_code");
    expect(DEFAULT_POG_THEME).toBe("zones");
    expect(isPogThemeId("height")).toBe(true);
    expect(isPogThemeId("area")).toBe(false);
    expect(isPogThemeId(3)).toBe(false);
  });

  it("jednostki: wysokość w m, udziały w %, intensywność bezwymiarowa", () => {
    expect(themeById("height").unit).toBe("m");
    expect(themeById("building_coverage").unit).toBe("%");
    expect(themeById("biologically_active").unit).toBe("%");
    expect(themeById("intensity").unit).toBe("1");
    expect(themeById("intensity").unit_label).toBe("wartość bezwymiarowa");
    expect(themeScaleCaption(themeById("height"))).toContain("metry (m)");
    expect(themeScaleCaption(themeById("intensity"))).toContain("wartość bezwymiarowa.");
    expect(themeScaleCaption(themeById("intensity"))).not.toContain("(1)");
    expect(themeScaleCaption(themeById("zones"))).toBe(themeById("zones").scale_description);
  });

  it("formatuje wartości z jednostką, a brak wartości jako osobny stan", () => {
    expect(formatThemeValue(themeById("height"), 12.5)).toBe("12,5 m");
    expect(formatThemeValue(themeById("building_coverage"), 0)).toBe("0%");
    expect(formatThemeValue(themeById("intensity"), 0.9)).toBe("0,9");
    expect(formatThemeValue(themeById("intensity"), null)).toBe(POG_NULL_STYLE.label);
    expect(formatThemeValue(themeById("intensity"), undefined)).toBe(POG_NULL_STYLE.label);
    expect(formatThemeValue(themeById("intensity"), Number.NaN)).toBe(POG_NULL_STYLE.label);
  });

  it.each(NUMERIC.map((theme) => [theme.id, theme] as const))(
    "%s: null, 0 i każda wartość progowa mają odrębnie sprawdzone klasy",
    (_id, theme) => {
      const raw = (rawPresentation.themes as RawTheme[]).find((item) => item.id === theme.id);
      const classes = raw?.classes ?? [];
      expect(classForValue(theme, null)).toBeNull();
      expect(classForValue(theme, undefined)).toBeNull();
      expect(colorForValue(theme, null)).toBe(POG_NULL_STYLE.fill);
      expect(classForValue(theme, 0)).toEqual(classes[0]);
      expect(colorForValue(theme, 0)).not.toBe(POG_NULL_STYLE.fill);
      classes.forEach((item, index) => {
        expect(classForValue(theme, item.min)?.color).toBe(item.color);
        if (index > 0) {
          expect(classForValue(theme, item.min - 1e-9)?.color).toBe(classes[index - 1].color);
        }
      });
      expect(themeBreaks(theme)).toEqual(classes.slice(1).map((item) => item.min));
    },
  );

  it.each(NUMERIC.map((theme) => [theme.id, theme] as const))(
    "%s: wyrażenie sprawdza has/null PRZED to-number, a progi/kolory = legenda = JSON",
    (_id, theme) => {
      const expression = themeFillColorExpression(theme);
      expect(expression[0]).toBe("case");
      expect(expression[1]).toEqual([
        "any",
        ["!", ["has", theme.property]],
        ["==", ["get", theme.property], null],
      ]);
      expect(expression[2]).toBe(POG_NULL_STYLE.fill);
      expect((expression[3] as unknown[])[1]).toEqual(["to-number", ["get", theme.property]]);

      const { colors, breaks } = stepFromExpression(expression);
      const legend = themeLegendItems(theme);
      const raw = (rawPresentation.themes as RawTheme[]).find((item) => item.id === theme.id);
      expect(colors).toEqual(legend.slice(0, -1).map((item) => item.color));
      expect(colors).toEqual(raw?.classes?.map((item) => item.color));
      expect(breaks).toEqual(raw?.classes?.slice(1).map((item) => item.min));
      expect(legend.at(-1)).toMatchObject({
        key: "null",
        color: POG_NULL_STYLE.fill,
        pattern: POG_NULL_STYLE.pattern,
      });

      expect(themePatternOpacityExpression(theme)).toEqual([
        "case",
        ["any", ["!", ["has", theme.property]], ["==", ["get", theme.property], null]],
        1,
        0,
      ]);
      expect(themePatternImage(theme)).toBe(patternImageId(POG_NULL_STYLE.pattern));
    },
  );

  it("tryb stref koloruje po zone_code i wzorem oznacza kod spoza słownika", () => {
    const zones = themeById("zones");
    const expression = themeFillColorExpression(zones);
    expect(expression.slice(0, 2)).toEqual(["match", ["get", "zone_code"]]);
    expect(expression.at(-1)).toBe(POG_UNKNOWN_ZONE.fill);
    expect(themePatternOpacityExpression(zones)).toEqual([
      "match",
      ["get", "zone_code"],
      [...POG_ZONE_CODES],
      0,
      1,
    ]);
    expect(themePatternImage(zones)).toBe(patternImageId(POG_UNKNOWN_ZONE.pattern));
    const legend = themeLegendItems(zones);
    expect(legend).toHaveLength(14);
    expect(legend.at(-1)).toMatchObject({ key: "unknown", pattern: POG_UNKNOWN_ZONE.pattern });
    expect(classForValue(zones, 1)).toBeNull();
  });
});
