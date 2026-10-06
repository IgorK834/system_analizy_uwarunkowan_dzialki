import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MpzpZoneCard } from "@/components/MpzpZoneCard";
import { ResultPanel } from "@/components/ResultPanel";
import { MODEL_READING_DISCLAIMER, MODEL_READING_MARK, NO_DATA_NOT_NO_RESTRICTION, NULL_NOT_ZERO } from "@/lib/mpzpProvenance";
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
    // PV3-18: brak wartości to jawny „brak danych” (nie zero i nie brak ograniczenia), a nie samo „—”.
    expect(screen.getByText(/^brak danych/)).toBeVisible();
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

  describe("wartości warunkowe (PV3-08)", () => {
    const flatRoof = { kind: "roof_type", label: "dach płaski", quote: "dachem płaskim" } as const;

    it("pokazuje dwie wartości z warunkami jako warunkowe, a nie jako sprzeczność", () => {
      render(
        <ul>
          <MpzpZoneCard
            zone={zone({
              max_building_height_m: null,
              parameters: [
                parameter({
                  normalized_value: 8,
                  raw_value: "8,0 m",
                  value_kind: "conditional",
                  conditions: [flatRoof],
                }),
                parameter({
                  normalized_value: 10,
                  raw_value: "10,0 m",
                  value_kind: "conditional",
                  conditions: [{ kind: "roof_type", label: "dach stromy", quote: "dachem stromym" }],
                }),
              ],
            })}
          />
        </ul>,
      );
      const rows = screen.getAllByRole("row").filter((row) => row.getAttribute("data-value-kind"));
      expect(rows.map((row) => row.getAttribute("data-value-kind"))).toEqual(["conditional", "conditional"]);
      expect(rows.every((row) => row.getAttribute("data-conflict") === null)).toBe(true);
      expect(within(rows[0]).getByText(/rodzaj dachu: dach płaski — „dachem płaskim”/)).toBeInTheDocument();
      expect(within(rows[1]).getByText(/rodzaj dachu: dach stromy — „dachem stromym”/)).toBeInTheDocument();
      expect(screen.getAllByTestId("conditional-tag")).toHaveLength(2);
      expect(screen.getByTestId("conditional-values-note")).toHaveTextContent(/to nie jest sprzeczność/);
      expect(screen.queryByText(/sprzeczne wartości parametru/)).not.toBeInTheDocument();
    });

    it("prawdziwa sprzeczność nadal jest ostrzeżeniem, także obok wartości warunkowej", () => {
      render(
        <ul>
          <MpzpZoneCard
            zone={zone({
              parameters: [
                parameter({ normalized_value: 9, value_kind: "conflict", conflict_group_id: "g" }),
                parameter({ normalized_value: 12, value_kind: "conflict", conflict_group_id: "g" }),
                parameter({ normalized_value: 6, value_kind: "conditional", conditions: [flatRoof] }),
              ],
            })}
          />
        </ul>,
      );
      expect(screen.getByText(/sprzeczne wartości parametru/)).toBeInTheDocument();
      const rows = screen.getAllByRole("row").filter((row) => row.getAttribute("data-value-kind"));
      expect(rows.map((row) => row.getAttribute("data-value-kind"))).toEqual(["conflict", "conflict", "conditional"]);
      expect(screen.getAllByText(/sprzeczna kandydatura/)).toHaveLength(2);
    });

    it("odpowiedź sprzed PV3-08 (bez warunków i bez value_kind) renderuje się jak wcześniej", () => {
      render(
        <ul>
          <MpzpZoneCard zone={zone({ parameters: [parameter({ conflict_group_id: "x" }), parameter()] })} />
        </ul>,
      );
      const rows = screen.getAllByRole("row").filter((row) => row.getAttribute("data-value-kind"));
      expect(rows.map((row) => row.getAttribute("data-value-kind"))).toEqual(["conflict", "unconditional"]);
      expect(screen.queryByTestId("conditional-values-note")).not.toBeInTheDocument();
    });
  });
});

const RESPONSE_SHA = "c0ffee" + "d".repeat(58);

function modelParameter(overrides: Partial<MpzpParameterEvidence> = {}): MpzpParameterEvidence {
  return parameter({
    name: "max_storeys",
    normalized_value: 3,
    unit: null,
    raw_value: "3 kondygnacje",
    evidence_text: "zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne",
    page_number: 13,
    segment_id: "zb-1",
    legal_unit_id: null,
    extraction_method: "llm_verified",
    review_status: "ai_candidate",
    manual_review_required: true,
    confidence: 0.62,
    model_id: "gemini-3.8-flash",
    prompt_version: "mpzp-extraction/1",
    response_sha256: RESPONSE_SHA,
    ...overrides,
  });
}

describe("MpzpZoneCard — odczyt automatyczny modelem (PV3-18)", () => {
  it("oznacza wartość z modelu, pokazuje cytat, stronę i provenance, a nie przedstawia jej jako interpretacji prawnej", () => {
    render(<ul><MpzpZoneCard zone={zone({ parameters: [modelParameter()] })} /></ul>);
    const row = screen.getByRole("row", { name: /max_storeys/ });
    expect(row).toHaveAttribute("data-model-reading", "true");
    expect(within(row).getByTestId("model-reading-tag")).toHaveTextContent(MODEL_READING_MARK);
    expect(within(row).getByText("„zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne”")).toBeVisible();
    expect(within(row).getByText(/str\. 13, segment zb-1/)).toBeVisible();
    expect(within(row).getByText(/dosłownie: „3 kondygnacje”/)).toBeVisible();
    const provenance = within(row).getByTestId("model-provenance");
    expect(provenance).toHaveTextContent("Model: gemini-3.8-flash · instrukcja: mpzp-extraction/1 · SHA-256 odpowiedzi: c0ffeeddddd");
    expect(within(provenance).getByText(/^c0ffeeddddd/)).toHaveAttribute("title", RESPONSE_SHA);
    expect(within(row).getByText(/odczyt automatyczny \(model językowy\) · SHA-256 dddddddddddd…/)).toBeVisible();
    const note = screen.getByTestId("model-reading-note");
    expect(note).toHaveTextContent("1 wartość to odczyt automatyczny (model językowy).");
    expect(note).toHaveTextContent(MODEL_READING_DISCLAIMER);
    expect(screen.queryByText(/verified/i)).toBeNull(); // nigdy status „verified”
  });

  it("nie oznacza wartości deterministycznych jako odczytu modelu", () => {
    render(
      <ul>
        <MpzpZoneCard zone={zone({ parameters: [parameter(), modelParameter(), parameter({ normalized_value: 12, raw_value: "12 m" })] })} />
      </ul>,
    );
    const rows = screen.getAllByRole("row").filter((row) => row.hasAttribute("data-value-kind"));
    expect(rows.map((row) => row.getAttribute("data-model-reading"))).toEqual([null, "true", null]);
    expect(screen.getAllByTestId("model-reading-tag")).toHaveLength(1);
    expect(within(rows[0]).queryByTestId("model-reading-tag")).toBeNull();
    expect(within(rows[0]).queryByTestId("model-provenance")).toBeNull();
    expect(within(rows[0]).getByText(/tekst PDF/)).toBeVisible();
  });

  it("wartość deterministyczna z niską pewnością albo flagą weryfikacji nadal nie jest odczytem modelu", () => {
    render(
      <ul>
        <MpzpZoneCard
          zone={zone({ parameters: [parameter({ confidence: 0.1, manual_review_required: true, extraction_method: "ocr" })] })}
        />
      </ul>,
    );
    expect(screen.queryByTestId("model-reading-tag")).toBeNull();
    expect(screen.queryByTestId("model-reading-note")).toBeNull();
    expect(screen.getByTestId("parameter-review")).toBeVisible();
  });

  it("pokazuje warunki wartości z modelu razem z ich cytatem", () => {
    render(
      <ul>
        <MpzpZoneCard
          zone={zone({
            parameters: [
              modelParameter({
                name: "max_building_height_m",
                normalized_value: 8,
                unit: "m",
                raw_value: "8 m",
                value_kind: "conditional",
                conditions: [{ kind: "roof_type", label: "dach płaski", quote: "dachem płaskim" }],
              }),
            ],
          })}
        />
      </ul>,
    );
    const row = screen.getByRole("row", { name: /max_building_height_m/ });
    expect(within(row).getByText(/rodzaj dachu: dach płaski — „dachem płaskim”/)).toBeVisible();
    expect(within(row).getByTestId("conditional-tag")).toBeVisible();
    expect(within(row).getByTestId("model-reading-tag")).toBeVisible();
  });

  it("licznik w nocie zgadza się z liczbą wartości z modelu", () => {
    render(<ul><MpzpZoneCard zone={zone({ parameters: [modelParameter(), modelParameter({ name: "max_intensity", normalized_value: 0.8 })] })} /></ul>);
    expect(screen.getByTestId("model-reading-note")).toHaveTextContent("2 wartości to odczyt automatyczny");
    expect(screen.getAllByTestId("model-reading-tag")).toHaveLength(2);
  });

  it("filtr „do ręcznej weryfikacji” zawęża tabelę, pokazuje licznik i wraca do pełnej listy", async () => {
    const user = userEvent.setup();
    render(
      <ul>
        <MpzpZoneCard
          zone={zone({
            parameters: [
              parameter({ name: "max_building_height_m" }),
              modelParameter(),
              parameter({ name: "setback_m", manual_review_required: true, normalized_value: 4, raw_value: "4 m" }),
              parameter({ name: "max_intensity", normalized_value: 0.8, raw_value: "0,8" }),
            ],
          })}
        />
      </ul>,
    );
    const filter = screen.getByRole("button", { name: "Tylko do ręcznej weryfikacji (2)" });
    expect(filter).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("status")).toHaveTextContent("Widoczne parametry: 4 z 4.");
    expect(screen.getAllByRole("row")).toHaveLength(5);

    await user.click(filter);
    expect(filter).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Widoczne parametry: 2 z 4.");
    const rows = screen.getAllByRole("row").filter((row) => row.hasAttribute("data-value-kind"));
    const headers = rows.map((row) => within(row).getByRole("rowheader").textContent ?? "");
    expect(headers).toHaveLength(2);
    expect(headers[0]).toMatch(/^max_storeys/);
    expect(headers[1]).toMatch(/^setback_m/);
    expect(rows.every((row) => row.getAttribute("data-needs-review") === "true")).toBe(true);

    await user.click(filter);
    expect(filter).toHaveAttribute("aria-pressed", "false");
    expect(screen.getAllByRole("row")).toHaveLength(5);
  });

  it("nie pokazuje filtra, gdy żaden parametr nie wymaga ręcznej weryfikacji", () => {
    render(<ul><MpzpZoneCard zone={zone()} /></ul>);
    expect(screen.queryByTestId("manual-review-filter")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("sprzeczność też trafia do filtra ręcznej weryfikacji", async () => {
    const user = userEvent.setup();
    render(
      <ul>
        <MpzpZoneCard
          zone={zone({
            parameters: [
              parameter({ conflict_group_id: "g", value_kind: "conflict" }),
              parameter({ normalized_value: 12, conflict_group_id: "g", value_kind: "conflict" }),
              parameter({ name: "max_intensity", normalized_value: 0.8 }),
            ],
          })}
        />
      </ul>,
    );
    await user.click(screen.getByRole("button", { name: /Tylko do ręcznej weryfikacji \(2\)/ }));
    expect(screen.getAllByRole("row").filter((row) => row.hasAttribute("data-value-kind"))).toHaveLength(2);
  });

  it("brak danych (null) jest różny od zera i od braku ograniczenia", () => {
    render(
      <ul>
        <MpzpZoneCard
          zone={zone({
            parameters: [
              parameter({ name: "min_biologically_active_percent", normalized_value: null, raw_value: null, unit: null }),
              parameter({ name: "max_building_coverage_percent", normalized_value: 0, raw_value: "0%", unit: "%" }),
              parameter({ name: "max_building_height_m" }),
            ],
          })}
        />
      </ul>,
    );
    const states = screen.getAllByRole("row").filter((row) => row.hasAttribute("data-value-kind"))
      .map((row) => within(row).getByText((_, node) => node?.hasAttribute("data-value-state") ?? false).getAttribute("data-value-state"));
    expect(states).toEqual(["null", "zero", "value"]);
    const zeroRow = screen.getByRole("row", { name: /max_building_coverage_percent/ });
    expect(within(zeroRow).getByText("0 %")).toBeVisible();
    const nullRow = screen.getByRole("row", { name: /min_biologically_active_percent/ });
    expect(within(nullRow).getByText(/^brak danych/)).toBeVisible();
    expect(within(nullRow).queryByText("0 %")).toBeNull();
    expect(within(nullRow).getByText(/to nie jest zero ani brak ograniczenia/)).toHaveClass("visually-hidden");
    expect(screen.getByTestId("no-data-note")).toHaveTextContent(NO_DATA_NOT_NO_RESTRICTION);
    expect(screen.getByTestId("no-data-note")).toHaveTextContent(NULL_NOT_ZERO);
  });

  it("w panelu wyniku odczyt modelu jest oznaczony tak samo jak w samej karcie", () => {
    render(
      <ResultPanel
        result={buildAnalyzeResponse({ mpzp_zones: [zone({ parameters: [modelParameter()] })] })}
        map={null}
      />,
    );
    expect(screen.getByTestId("model-reading-tag")).toHaveTextContent(MODEL_READING_MARK);
    expect(screen.getByTestId("model-reading-note")).toBeVisible();
  });

  it("odpowiedź sprzed PV3-18 (bez pól provenance) renderuje się bez oznaczenia modelu", () => {
    const legacy = parameter();
    for (const key of ["review_status", "model_id", "prompt_version", "response_sha256"] as const) {
      expect(legacy[key]).toBeUndefined();
    }
    render(<ul><MpzpZoneCard zone={zone({ parameters: [legacy] })} /></ul>);
    expect(screen.queryByTestId("model-reading-tag")).toBeNull();
    expect(screen.queryByTestId("model-provenance")).toBeNull();
  });

  it("brakujące pola provenance wartości z modelu są jawne, nie puste", () => {
    render(<ul><MpzpZoneCard zone={zone({ parameters: [modelParameter({ model_id: undefined, prompt_version: undefined, response_sha256: undefined })] })} /></ul>);
    expect(screen.getByTestId("model-provenance")).toHaveTextContent("Model: nieznany · instrukcja: nieznana · SHA-256 odpowiedzi: —");
  });
});
