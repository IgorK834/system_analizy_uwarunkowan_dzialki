import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MpzpZoneCard } from "@/components/MpzpZoneCard";
import { ResultPanel } from "@/components/ResultPanel";
import { buildAnalyzeResponse } from "@/test/fixtures";
import type { MpzpParameterEvidence, MpzpZoneResult } from "@/lib/types";

const SHA = "d".repeat(64);

function parameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return {
    name: "max_building_height_m",
    normalized_value: 9,
    raw_value: "9,0 m",
    unit: "m",
    evidence_text: "maksymalna wysokość zabudowy: 9,0 m",
    page_number: 3,
    segment_id: "p3-s2",
    legal_unit_id: 41,
    document_sha256: SHA,
    document_version_id: 7,
    parser_version: "mpzp-parser/2.0",
    extraction_method: "pdf_text",
    confidence: 0.81,
    conflict_group_id: null,
    manual_review_required: false,
    ...overrides,
  };
}

function zone(overrides: Partial<MpzpZoneResult> = {}): MpzpZoneResult {
  return {
    zone_symbol: "1MN",
    primary_use: "single_family_housing",
    supplementary_use: null,
    max_building_height_m: 9,
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
    act_version: "a".repeat(64),
    act_version_id: 3,
    data_release_id: 12,
    touches_boundary: false,
    assignment_method: "vector_intersection",
    parameters: [parameter()],
    manual_review_required: false,
    ...overrides,
  };
}

describe("MpzpZoneCard", () => {
  it("pokazuje obie strefy z udziałem 60/40 i stabilnymi ID w panelu", () => {
    render(
      <ResultPanel
        result={buildAnalyzeResponse({
          mpzp_zones: [
            zone(),
            zone({
              zone_symbol: "2U",
              zone_id: "plan-a:2U:fedcba9876543210",
              intersection_area_sqm: 400,
              intersection_pct: 40,
              is_dominant: false,
              parameters: [parameter({ normalized_value: 12, raw_value: "12 m", legal_unit_id: 42 })],
            }),
          ],
        })}
        map={null}
      />,
    );
    const list = screen.getByRole("list", { name: "Wszystkie strefy MPZP działki" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("data-zone-id"))).toEqual([
      "plan-a:1MN:0123456789abcdef",
      "plan-a:2U:fedcba9876543210",
    ]);
    expect(within(items[0]).getByText(/Udział w powierzchni działki: 60\.0% \(600 m²\)/)).toBeVisible();
    expect(within(items[1]).getByText(/Udział w powierzchni działki: 40\.0% \(400 m²\)/)).toBeVisible();
    expect(within(items[1]).getByText("12 m")).toBeVisible();
    expect(within(items[1]).getByText(/jednostka #42/)).toBeVisible();
  });

  it("pokazuje źródło wartości: stronę, segment, jednostkę, fragment i SHA", () => {
    render(<ul><MpzpZoneCard zone={zone()} /></ul>);
    const table = screen.getByRole("table", { name: "Parametry strefy 1MN i ich źródło w uchwale" });
    expect(within(table).getByText("str. 3, segment p3-s2, jednostka #41")).toBeVisible();
    expect(within(table).getByText("„maksymalna wysokość zabudowy: 9,0 m”")).toBeVisible();
    expect(within(table).getByText(/tekst PDF · SHA-256 dddddddddddd…/)).toHaveAttribute("title", SHA);
    expect(within(table).getByText(/dosłownie: „9,0 m”/)).toBeVisible();
    expect(screen.getByText("Sposób przypisania: przecięcie z wektorem wydzieleń")).toBeVisible();
    expect(screen.getByText(/Plan plan-a · wersja aaaaaaaaaaaa… · wydanie #12/)).toBeVisible();
    expect(screen.getByText(/Pewność przypisania: 95% · dane z 25\.09\.2026/)).toBeVisible();
  });

  it("sprzeczne kandydatury są pokazane obok siebie bez wyboru", () => {
    render(
      <ul>
        <MpzpZoneCard
          zone={zone({
            max_building_height_m: null,
            manual_review_required: true,
            parameters: [
              parameter({ conflict_group_id: "v:1MN:max_building_height_m", manual_review_required: true }),
              parameter({
                normalized_value: 12,
                raw_value: "12 m",
                conflict_group_id: "v:1MN:max_building_height_m",
                manual_review_required: true,
                extraction_method: "ocr",
                page_number: null,
                segment_id: null,
                legal_unit_id: null,
                document_sha256: null,
                evidence_text: null,
              }),
            ],
          })}
        />
      </ul>,
    );
    expect(screen.getByRole("note")).toHaveTextContent("żadna nie została wybrana automatycznie");
    const rows = screen.getAllByRole("row").filter((row) => row.getAttribute("data-conflict") === "true");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("miejsce w dokumencie nieustalone")).toBeVisible();
    expect(screen.getByText("OCR")).toBeVisible();
  });

  it("styczność, fallback bez wektora, wartość null i snapshot legacy", () => {
    const { rerender } = render(
      <ul>
        <MpzpZoneCard
          zone={zone({
            touches_boundary: true,
            intersection_area_sqm: 0,
            intersection_pct: 0,
            is_dominant: false,
            parameters: [],
            zone_id: null,
            act_identifier: null,
          })}
        />
      </ul>,
    );
    expect(screen.getByText("tylko styczność granicy")).toBeVisible();
    expect(screen.getByText(/Brak udziału powierzchniowego/)).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();

    rerender(
      <ul>
        <MpzpZoneCard
          zone={zone({
            assignment_method: "document_candidate",
            manual_review_required: true,
            primary_use: null,
            parameters: [parameter({ normalized_value: null, unit: null, raw_value: null, extraction_method: null })],
            act_version: null,
            data_release_id: null,
            source: { ...zone().source, fetched_at: null, confidence: 0.5 },
          })}
        />
      </ul>,
    );
    expect(screen.getByText("Sposób przypisania: kandydat z discovery/dokumentu (bez wektora)")).toBeVisible();
    expect(screen.getByText("Przypisanie lub parametry wymagają weryfikacji.")).toBeVisible();
    expect(screen.getByText("—")).toBeVisible();
    expect(screen.getByText(/metoda nieznana/)).toBeVisible();
    expect(screen.getByText("Pewność przypisania: 50%")).toBeVisible();

    const legacy = zone();
    delete legacy.assignment_method;
    delete legacy.parameters;
    rerender(<ul><MpzpZoneCard zone={{ ...legacy, parameters: undefined }} /></ul>);
    expect(screen.getByText("Sposób przypisania: snapshot sprzed wersjonowania stref")).toBeVisible();
  });
});

describe("MpzpZoneCard — symbol podany ręcznie (BK-204)", () => {
  it("nie zmyśla udziału i oznacza każdy parametr do weryfikacji", async () => {
    const { manualZone } = await import("@/test/manualZoneFixtures");
    render(
      <ul>
        <MpzpZoneCard zone={manualZone()} />
      </ul>,
    );

    expect(screen.getByTestId("manual-zone-banner")).toHaveTextContent("Symbol strefy podano ręcznie");
    expect(screen.getByText(/Udział w powierzchni działki: nieustalony/)).toBeVisible();
    expect(screen.queryByText(/100\.0%/)).toBeNull();
    expect(screen.queryByText("największy udział")).toBeNull();
    expect(screen.getAllByTestId("parameter-review")).toHaveLength(1);
    expect(screen.getByLabelText("Decyzja użytkownika")).toHaveTextContent("230_U");
    expect(screen.getByLabelText("Decyzja użytkownika")).toHaveTextContent("MPZP/2020/1 · 230_U, 231_MN");
    expect(screen.getByLabelText("Decyzja użytkownika")).toHaveTextContent(
      "kopia przypięta przy wstrzymaniu · SHA-256 bbbbbbbbbbbb…",
    );
  });

  it("opisuje symbol spoza kandydatów i brak przypiętego dokumentu", async () => {
    const { manualZone } = await import("@/test/manualZoneFixtures");
    const base = manualZone();
    render(
      <ul>
        <MpzpZoneCard
          zone={{
            ...base,
            parameters: [],
            manual_selection: {
              ...base.manual_selection!,
              symbol_in_candidates: false,
              candidate_zone_symbols: [],
              plan_id: null,
              document_pinned: false,
              document_sha256: null,
            },
          }}
        />
      </ul>,
    );

    const details = screen.getByLabelText("Decyzja użytkownika");
    expect(details).toHaveTextContent("spoza kandydatów discovery");
    expect(details).toHaveTextContent("plan nieustalony · brak kandydatów");
    expect(details).toHaveTextContent("dokument nie został przypięty — parametry nieustalone");
  });

  it("wynik z ręcznym symbolem ma notę na poziomie całego panelu", async () => {
    const { manualZone } = await import("@/test/manualZoneFixtures");
    render(
      <ResultPanel
        result={buildAnalyzeResponse({ status: "partial", mpzp_zones: [manualZone()] })}
        map={null}
      />,
    );

    expect(screen.getByTestId("manual-zone-result-note")).toHaveTextContent(
      "Symbol strefy podano ręcznie — wynik jest częściowy",
    );
  });
});
