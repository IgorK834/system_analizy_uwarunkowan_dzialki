import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import rawPresentation from "../../shared/pog-presentation.json";
import { PogLegend, PogSwatch } from "@/components/PogLegend";
import { POG_THEME_IDS, themeById, themeFillColorExpression, type PogThemeId } from "@/lib/pogThemes";
import { POG_NULL_STYLE, POG_UNKNOWN_ZONE } from "@/lib/pogZones";

type LegendEntry = { key: string; color: string };

function legendEntries(): LegendEntry[] {
  return screen.getAllByTestId("pog-legend-item").map((item) => ({
    key: item.dataset.key ?? "",
    color: item.dataset.color ?? "",
  }));
}

/** Kolory i progi, które faktycznie rysuje warstwa MapLibre danego trybu. */
function layerEntries(themeId: PogThemeId): LegendEntry[] {
  const expression = themeFillColorExpression(themeById(themeId));
  if (expression[0] === "match") {
    const pairs = expression.slice(2, -1);
    const entries: LegendEntry[] = [];
    for (let index = 0; index < pairs.length; index += 2) {
      entries.push({ key: String(pairs[index]), color: String(pairs[index + 1]) });
    }
    return [...entries, { key: "unknown", color: String(expression.at(-1)) }];
  }
  const step = expression[3] as unknown[];
  const [, , first, ...rest] = step;
  const entries: LegendEntry[] = [{ key: "class-0", color: String(first) }];
  for (let index = 0; index < rest.length; index += 2) {
    entries.push({ key: `class-${rest[index]}`, color: String(rest[index + 1]) });
  }
  return [...entries, { key: "null", color: String(expression[2]) }];
}

/** Test kontraktowy: wykrywa rozjazd progów/kolorów między legendą a warstwą. */
export function legendMatchesLayer(legend: LegendEntry[], layer: LegendEntry[]): boolean {
  return JSON.stringify(legend) === JSON.stringify(layer);
}

describe("PogLegend", () => {
  it.each(POG_THEME_IDS)("tryb %s: legenda = warstwa mapy = artefakt JSON", (themeId) => {
    render(<PogLegend theme={themeId} />);
    const legend = legendEntries();
    expect(legendMatchesLayer(legend, layerEntries(themeId))).toBe(true);

    const raw = rawPresentation.themes.find((theme) => theme.id === themeId);
    if (raw?.kind === "numeric") {
      expect(legend.slice(0, -1).map((item) => item.color)).toEqual(
        raw.classes?.map((item) => item.color),
      );
      expect(legend.at(-1)).toEqual({ key: "null", color: POG_NULL_STYLE.fill });
      for (const item of raw.classes ?? []) expect(screen.getByText(item.label)).toBeInTheDocument();
      expect(screen.getByText(POG_NULL_STYLE.label)).toBeInTheDocument();
      expect(screen.getByText(/nie jest wartość 0/)).toBeInTheDocument();
    } else {
      expect(legend).toHaveLength(14);
      expect(screen.getByText(POG_UNKNOWN_ZONE.label)).toBeInTheDocument();
      expect(screen.getByText(/SU — strefa usługowa/)).toBeInTheDocument();
    }
  });

  it("wykrywa rozjazd progu albo koloru", () => {
    render(<PogLegend theme="height" />);
    const legend = legendEntries();
    const shiftedThreshold = layerEntries("height").map((item) =>
      item.key === "class-12" ? { ...item, key: "class-13" } : item,
    );
    const changedColor = layerEntries("height").map((item, index) =>
      index === 0 ? { ...item, color: "#000000" } : item,
    );
    expect(legendMatchesLayer(legend, shiftedThreshold)).toBe(false);
    expect(legendMatchesLayer(legend, changedColor)).toBe(false);
  });

  it("OUZ/OZS/OSDIS mają wzór i opis tekstowy, a statusy — opis projektu", () => {
    render(<PogLegend theme="zones" />);
    const overlays = screen.getAllByTestId("pog-overlay-legend-item");
    expect(overlays.map((item) => item.dataset.key)).toEqual([
      "ouz",
      "downtown",
      "social_infrastructure_standard",
      "act_boundary",
    ]);
    expect(new Set(overlays.slice(0, 3).map((item) => item.dataset.pattern)).size).toBe(3);
    expect(within(overlays[0]).getByText("OUZ")).toBeInTheDocument();
    expect(within(overlays[1]).getByText(/Śliwkowy obrys kropkowany/)).toBeInTheDocument();
    expect(within(overlays[2]).getByText("OSDIS")).toBeInTheDocument();
    const statuses = screen.getAllByTestId("pog-status-legend-item");
    expect(statuses).toHaveLength(5);
    expect(screen.getByText(/projekt — dane niewiążące/)).toBeInTheDocument();
    // BK-406: projekt ma wzór i tekst plakietki, akt wiążący — osobny opis bez wzoru.
    const project = statuses.find((item) => item.dataset.key === "project") as HTMLElement;
    const binding = statuses.find((item) => item.dataset.key === "binding") as HTMLElement;
    expect(project.dataset.pattern).toBe("horizontal-lines");
    expect(within(project).getByText("[projekt / dane niewiążące]")).toBeInTheDocument();
    expect(project.querySelector("pattern path")).not.toBeNull();
    expect(binding.dataset.pattern).toBe("");
    expect(binding).toHaveTextContent(/^akt obowiązujący/);
    expect(screen.getByText(/słownik RodzajStrefyPlanistycznejKod/)).toBeInTheDocument();
  });

  it("próbka rysuje wzór i obrys przerywany", () => {
    const { container } = render(
      <>
        <PogSwatch color="#ffffff" pattern="dots" outline="#123456" dash={[1, 2]} />
        <PogSwatch color="#ffffff" pattern="cross-lines" />
        <PogSwatch color="#ffffff" pattern="cross-hatch" />
        <PogSwatch color="#ffffff" pattern="diagonal-lines" />
        <PogSwatch color="#ffffff" pattern="horizontal-lines" />
        <PogSwatch color="#ffffff" pattern={"inny" as never} />
        <PogSwatch color="#ffffff" />
      </>,
    );
    expect(container.querySelector("pattern#pog-swatch-dots-123456 circle")).not.toBeNull();
    expect(container.querySelector("rect[stroke-dasharray='1.2 2.4']")).not.toBeNull();
    expect(container.querySelectorAll("pattern")).toHaveLength(6);
  });
});
