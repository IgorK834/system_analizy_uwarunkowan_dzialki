import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MpzpZoneCard } from "@/components/MpzpZoneCard";
import { MODEL_READING_MARK, MODEL_READING_SR_TEXT } from "@/lib/mpzpProvenance";
import type { MpzpParameterEvidence, MpzpZoneResult } from "@/lib/types";
import { contrastRatio, declarations, resolveColor, textContrast } from "@/test/contrast";

/**
 * Dostępność nowych elementów PV3-18 (oznaczenie odczytu modelu, filtr „do ręcznej weryfikacji”, brak
 * danych): czytnik ekranu (role, nazwy, regiony, komunikaty), klawiatura (kolejność Tab, Enter/Spacja,
 * fokus) i kontrast WCAG AA liczony z rzeczywistego arkusza stylów. Automatyzacja w przeglądarce (axe,
 * rzeczywisty czytnik) jest w ramach BK-701; ręczny odbiór UI: docs/evaluation/pv3-18-20-verification.md.
 */
const SHA = "d".repeat(64);

function parameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return {
    name: "max_building_height_m",
    normalized_value: 9,
    raw_value: "9,0 m",
    unit: "m",
    evidence_text: "maksymalna wysokość zabudowy: 9,0 m",
    page_number: 3,
    segment_id: null,
    legal_unit_id: null,
    document_sha256: SHA,
    document_version_id: 7,
    parser_version: "mpzp-parser/3.0-det",
    extraction_method: "pdf_text",
    confidence: 0.81,
    conflict_group_id: null,
    manual_review_required: false,
    ...overrides,
  };
}

function modelParameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return parameter({
    name: "max_storeys",
    normalized_value: 3,
    unit: null,
    raw_value: "3 kondygnacje",
    extraction_method: "llm_verified",
    review_status: "ai_candidate",
    manual_review_required: true,
    confidence: 0.62,
    model_id: "gemini-3.8-flash",
    prompt_version: "mpzp-extraction/1",
    response_sha256: "c".repeat(64),
    ...overrides,
  });
}

function zone(parameters: MpzpParameterEvidence[]): MpzpZoneResult {
  return {
    zone_symbol: "1MN",
    primary_use: null,
    supplementary_use: null,
    max_building_height_m: null,
    max_floors: null,
    min_biologically_active_pct: null,
    max_floor_area_ratio: null,
    min_floor_area_ratio: null,
    max_building_coverage_pct: null,
    intersection_area_sqm: 600,
    intersection_pct: 60,
    is_dominant: true,
    source: {
      source_name: "MPZP_WEKTOR:mpzp_krakow",
      source_url: null,
      fetched_at: "2026-09-25T08:00:00Z",
      response_status: null,
      confidence: 0.95,
      manual_review_required: false,
    },
    zone_id: "plan-a:1MN:0123456789abcdef",
    act_identifier: "plan-a",
    act_version: null,
    act_version_id: null,
    data_release_id: null,
    touches_boundary: false,
    assignment_method: "vector_intersection",
    parameters,
    manual_review_required: false,
  };
}

function renderCard(parameters: MpzpParameterEvidence[]) {
  return render(
    <ul>
      <MpzpZoneCard zone={zone(parameters)} />
    </ul>,
  );
}

describe("dostępność — czytnik ekranu", () => {
  it("oznaczenie odczytu modelu ma pełny tekst w nazwie nagłówka wiersza i tekst dla czytnika", () => {
    renderCard([parameter(), modelParameter()]);
    const rowHeader = screen
      .getAllByRole("rowheader")
      .find((header) => (header.textContent ?? "").includes(MODEL_READING_MARK));
    expect(rowHeader).toBeDefined();
    if (!rowHeader) return;
    expect(rowHeader).toHaveAccessibleName(expect.stringContaining(MODEL_READING_MARK));
    expect(rowHeader).toHaveTextContent(MODEL_READING_SR_TEXT);
    // Tekst dla czytnika jest ukryty wizualnie, ale nie przez display:none/aria-hidden (czytnik go odczytuje).
    const hidden = within(rowHeader).getByText(MODEL_READING_SR_TEXT);
    expect(hidden).toHaveClass("visually-hidden");
    expect(hidden.closest("[aria-hidden='true']")).toBeNull();
    // Wartość deterministyczna nie dostaje tego tekstu.
    expect(screen.getByRole("rowheader", { name: /^max_building_height_m$/ })).toBeInTheDocument();
  });

  it("tabela ma opis, nagłówki kolumn z zakresem i nagłówki wierszy", () => {
    renderCard([modelParameter()]);
    const table = screen.getByRole("table", { name: "Parametry strefy 1MN i ich źródło w uchwale" });
    const headers = within(table).getAllByRole("columnheader");
    expect(headers.map((header) => header.textContent)).toEqual(["Parametr", "Wartość", "Źródło", "Pewność"]);
    expect(headers.every((header) => header.getAttribute("scope") === "col")).toBe(true);
    expect(within(table).getByRole("rowheader")).toHaveAttribute("scope", "row");
  });

  it("noty i filtr mają role zrozumiałe dla czytnika, a licznik jest komunikatem na żywo", () => {
    renderCard([parameter(), modelParameter()]);
    expect(screen.getByRole("note")).toHaveTextContent("1 wartość to odczyt automatyczny (model językowy).");
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    const filter = screen.getByRole("button", { name: "Tylko do ręcznej weryfikacji (1)" });
    expect(filter).toHaveAttribute("aria-describedby", status.id);
    expect(filter).toHaveAttribute("aria-pressed");
    // Przewijana tabela jest nazwanym regionem dostępnym z klawiatury.
    const region = screen.getByRole("region", { name: "Parametry strefy 1MN (przewijana tabela)" });
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("brak danych ma tekst czytelny dla czytnika zamiast samego „—”", () => {
    renderCard([parameter({ normalized_value: null, raw_value: null, unit: null })]);
    const row = screen.getByRole("row", { name: /max_building_height_m/ });
    expect(row).toHaveTextContent("brak danych");
    expect(row).toHaveTextContent("to nie jest zero ani brak ograniczenia");
    expect(within(row).queryByText("—")).toBeNull();
  });

  it("zmiana filtra jest zapowiadana przez region komunikatów (liczba widocznych parametrów)", async () => {
    const user = userEvent.setup();
    renderCard([parameter(), modelParameter(), parameter({ name: "setback_m", normalized_value: 4 })]);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Widoczne parametry: 3 z 3.");
    await user.click(screen.getByRole("button", { name: /Tylko do ręcznej weryfikacji/ }));
    expect(screen.getByRole("status")).toBe(status); // ten sam węzeł: czytnik zapowie zmianę treści
    expect(status).toHaveTextContent("Widoczne parametry: 1 z 3.");
  });
});

describe("dostępność — klawiatura", () => {
  it("filtr i przewijana tabela są w kolejności Tab; Enter i Spacja przełączają filtr, fokus zostaje na przycisku", async () => {
    const user = userEvent.setup();
    renderCard([parameter(), modelParameter()]);
    const filter = screen.getByRole("button", { name: /Tylko do ręcznej weryfikacji/ });
    const region = screen.getByRole("region", { name: /przewijana tabela/ });

    await user.tab();
    expect(filter).toHaveFocus();
    await user.tab();
    expect(region).toHaveFocus();
    await user.tab({ shift: true });
    expect(filter).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(filter).toHaveAttribute("aria-pressed", "true");
    expect(filter).toHaveFocus();
    await user.keyboard(" ");
    expect(filter).toHaveAttribute("aria-pressed", "false");
    expect(filter).toHaveFocus();
  });

  it("filtr jest natywnym przyciskiem (nie wymaga dodatkowych skrótów) i nie pułapkuje fokusu", async () => {
    const user = userEvent.setup();
    renderCard([modelParameter()]);
    const filter = screen.getByRole("button", { name: /Tylko do ręcznej weryfikacji/ });
    expect(filter.tagName).toBe("BUTTON");
    expect(filter).toHaveAttribute("type", "button");
    await user.tab();
    await user.tab();
    await user.tab(); // za regionem fokus opuszcza kartę (nic go nie pułapkuje)
    expect(filter).not.toHaveFocus();
  });
});

describe("dostępność — kontrast (WCAG 2.x AA)", () => {
  const AA_TEXT = 4.5;
  const AA_UI = 3;

  it("oznaczenie odczytu modelu i nota spełniają AA dla tekstu", () => {
    expect(textContrast(".model-reading-tag")).toBeGreaterThanOrEqual(AA_TEXT);
    expect(textContrast(".model-reading-note")).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it("obramowanie oznaczenia jest widoczne na tle strony (co najmniej 3:1)", () => {
    const border = resolveColor(declarations(".model-reading-tag").border.split(" ").pop() ?? "");
    expect(contrastRatio(border, "#ffffff")).toBeGreaterThanOrEqual(AA_UI);
  });

  it("przycisk filtra w obu stanach spełnia AA, a stan nie zależy wyłącznie od koloru", () => {
    expect(textContrast(".review-filter", "#f4f7f5")).toBeGreaterThanOrEqual(AA_TEXT);
    expect(textContrast('.review-filter[aria-pressed="true"]')).toBeGreaterThanOrEqual(AA_TEXT);
    // Stan jest też w aria-pressed (czytnik) — kolor jest tylko dodatkiem wizualnym.
    renderCard([modelParameter()]);
    expect(screen.getByRole("button", { name: /Tylko do ręcznej weryfikacji/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("brak danych (wyciszony, kursywa) spełnia AA na białym tle", () => {
    expect(textContrast(".value-null")).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it("fokus ma widoczny obrys o kontraście co najmniej 3:1 względem tła", () => {
    const focus = declarations(".review-filter:focus-visible");
    expect(focus.outline).toMatch(/^3px solid /);
    expect(contrastRatio(resolveColor(focus.outline.split(" ").pop() ?? ""), "#ffffff")).toBeGreaterThanOrEqual(AA_UI);
    expect(declarations(".result-table-scroll:focus-visible").outline).toBe(focus.outline);
  });

  it("licznik kontrastu zgadza się z wartościami referencyjnymi WCAG", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
  });
});
