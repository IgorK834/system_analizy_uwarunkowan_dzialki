import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type maplibregl from "maplibre-gl";

import { ResultPanel } from "@/components/ResultPanel";
import { buildAnalyzeResponse, buildPogResult } from "@/test/fixtures";

function createMapMock() {
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const layers = new Set<string>();

  const map = {
    getSource: vi.fn((id: string) => sources.get(id)),
    addSource: vi.fn((id: string) => {
      sources.set(id, { setData: vi.fn() });
    }),
    removeSource: vi.fn((id: string) => {
      sources.delete(id);
    }),
    getLayer: vi.fn((id: string) => (layers.has(id) ? {} : undefined)),
    addLayer: vi.fn((layer: { id: string }) => {
      layers.add(layer.id);
    }),
    removeLayer: vi.fn((id: string) => {
      layers.delete(id);
    }),
    setLayoutProperty: vi.fn(),
    fitBounds: vi.fn(),
  };

  return map as unknown as maplibregl.Map & typeof map;
}

describe("ResultPanel", () => {
  let map: ReturnType<typeof createMapMock>;

  beforeEach(() => {
    map = createMapMock();
  });

  it("renderuje sekcje geometrii, MPZP, POG, infrastruktury, ryzyk i źródeł niezależnie od stanu innych sekcji", () => {
    const result = buildAnalyzeResponse({
      pog: null,
      infrastructure: [],
      mpzp_zones: [
        {
          zone_symbol: "MN",
          primary_use: "zabudowa mieszkaniowa jednorodzinna",
          supplementary_use: null,
          max_building_height_m: 9,
          max_floors: 2,
          min_biologically_active_pct: 40,
          max_floor_area_ratio: 0.8,
          min_floor_area_ratio: 0.01,
          max_building_coverage_pct: 30,
          intersection_area_sqm: 800,
          intersection_pct: 80,
          is_dominant: true,
          source: {
            source_name: "KIMPZP",
            source_url: null,
            fetched_at: null,
            response_status: null,
            confidence: 0.8,
            manual_review_required: false,
          },
        },
      ],
    });

    render(<ResultPanel result={result} map={null} />);

    // Geometria i MPZP renderują się mimo braku danych POG i infrastruktury.
    expect(screen.getByRole("heading", { name: "Geometria" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "MPZP" })).toBeVisible();
    expect(screen.getByText("MN")).toBeVisible();

    // Sekcje bez danych pokazują jawny stan "brak danych", nie pustą tabelę.
    expect(
      screen.getByText("Niedostępne — brak danych POG dla tej działki."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Nie sprawdzono albo nie wykryto sieci uzbrojenia terenu na działce.",
      ),
    ).toBeVisible();
  });

  it("renderuje sekcję ryzyk, źródeł i ostrzeżeń z danymi", () => {
    const result = buildAnalyzeResponse({
      risks: [
        {
          risk_type: "flood_zone",
          description: "Część działki w obszarze zagrożenia powodziowego.",
          geometry_geojson: null,
          source: {
            source_name: "ISOK",
            source_url: null,
            fetched_at: null,
            response_status: null,
            confidence: 0.9,
            manual_review_required: false,
          },
        },
      ],
      sources: [
        {
          source_name: "ULDK",
          source_url: null,
          fetched_at: null,
          response_status: null,
          confidence: 1,
          manual_review_required: false,
        },
      ],
      warnings: [
        {
          code: "MPZP_PARTIAL",
          message: "Dane częściowe",
          severity: "warning",
          source_name: "mpzp",
        },
      ],
    });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByText(/zagrożenia powodziowego/)).toBeVisible();
    expect(screen.getByText("ULDK")).toBeVisible();
    expect(screen.getByText("Dane częściowe")).toBeVisible();
  });

  it("dodaje warstwy GeoJSON działki i obszaru zabudowy na mapie", () => {
    const result = buildAnalyzeResponse({
      parcel: {
        parcel_identifier: "122101_1.0001.1",
        geometry_geojson: { type: "Polygon", coordinates: [] },
        metrics: {
          area_sqm: 1000,
          area_ha: 0.1,
          perimeter_m: 140,
          is_valid: true,
          geometry_repaired: false,
        },
        source: {
          source_name: "ULDK",
          source_url: null,
          fetched_at: null,
          response_status: null,
          confidence: 1,
          manual_review_required: false,
        },
        buildable_area_geojson: {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [] },
          properties: { layer: "buildable_area", is_technical_approximation: true },
        },
      },
    });

    render(<ResultPanel result={result} map={map} />);

    expect(map.addSource).toHaveBeenCalledWith(
      "parcel-source",
      expect.objectContaining({ type: "geojson" }),
    );
    expect(map.addSource).toHaveBeenCalledWith(
      "buildable-area-source",
      expect.objectContaining({ type: "geojson" }),
    );
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "parcel-fill-layer", type: "fill" }),
    );
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "buildable-area-fill-layer", type: "fill" }),
    );
  });

  it("dopasowuje mapę do nowej działki tylko raz dla danego wyniku analizy", () => {
    const parcel = {
      parcel_identifier: "122101_1.0001.1",
      geometry_geojson: {
        type: "Polygon",
        coordinates: [
          [
            [19.9, 50.0],
            [20.1, 50.0],
            [20.1, 50.2],
            [19.9, 50.0],
          ],
        ],
      },
      metrics: {
        area_sqm: 1000,
        area_ha: 0.1,
        perimeter_m: 140,
        is_valid: true,
        geometry_repaired: false,
      },
      source: {
        source_name: "ULDK",
        source_url: null,
        fetched_at: null,
        response_status: null,
        confidence: 1,
        manual_review_required: false,
      },
      buildable_area_geojson: null,
    };
    const { rerender } = render(
      <ResultPanel
        result={buildAnalyzeResponse({ analysis_id: 42, parcel })}
        map={map}
      />,
    );

    expect(map.fitBounds).toHaveBeenCalledWith(
      [
        [19.9, 50.0],
        [20.1, 50.2],
      ],
      expect.objectContaining({ padding: 64, maxZoom: 18 }),
    );

    rerender(
      <ResultPanel
        result={buildAnalyzeResponse({
          analysis_id: 42,
          status: "partial",
          parcel: { ...parcel },
        })}
        map={map}
      />,
    );
    expect(map.fitBounds).toHaveBeenCalledOnce();

    rerender(
      <ResultPanel
        result={buildAnalyzeResponse({ analysis_id: 43, parcel: { ...parcel } })}
        map={map}
      />,
    );
    expect(map.fitBounds).toHaveBeenCalledTimes(2);
  });

  it("przełącznik warstwy zmienia widoczność przez setLayoutProperty", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const result = buildAnalyzeResponse({
      parcel: {
        parcel_identifier: "122101_1.0001.1",
        geometry_geojson: { type: "Polygon", coordinates: [] },
        metrics: {
          area_sqm: 1000,
          area_ha: 0.1,
          perimeter_m: 140,
          is_valid: true,
          geometry_repaired: false,
        },
        source: {
          source_name: "ULDK",
          source_url: null,
          fetched_at: null,
          response_status: null,
          confidence: 1,
          manual_review_required: false,
        },
        buildable_area_geojson: null,
      },
    });

    render(<ResultPanel result={result} map={map} />);

    const parcelToggle = screen.getByRole("switch", { name: /Obrys działki/ });
    await user.click(parcelToggle);

    expect(map.setLayoutProperty).toHaveBeenCalledWith(
      "parcel-fill-layer",
      "visibility",
      "none",
    );
    expect(map.setLayoutProperty).toHaveBeenCalledWith(
      "parcel-line-layer",
      "visibility",
      "none",
    );
  });

  it("wyłącza przełącznik obszaru zabudowy, gdy geometria jest niedostępna", () => {
    const result = buildAnalyzeResponse({ parcel: null });

    render(<ResultPanel result={result} map={null} />);

    const buildableToggle = screen.getByRole("switch", {
      name: /Obszar zabudowy/,
    });
    expect(buildableToggle).toBeDisabled();
    expect(buildableToggle).toHaveAttribute("aria-checked", "false");
  });

  it("pokazuje wpływ strefy infrastruktury na obszar zabudowy i uwagę reguły", () => {
    const result = buildAnalyzeResponse({
      infrastructure: [
        {
          network_type: "water",
          buffer_m: 1.5,
          zone_area_sqm: 120,
          rule_source: "network_rules.yaml",
          rule_confidence: 0.7,
          rule_note: "Bufor techniczny, nie strefa kontrolowana gestora.",
          affects_buildable_area: true,
          network_geometry_geojson: null,
          protection_zone_geojson: null,
          source: {
            source_name: "KIUT",
            source_url: null,
            fetched_at: null,
            response_status: null,
            confidence: 0.7,
            manual_review_required: true,
          },
        },
      ],
    });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByText(/pomniejszyła szacowany obszar zabudowy/)).toBeVisible();
    expect(
      screen.getByText("Bufor techniczny, nie strefa kontrolowana gestora."),
    ).toBeVisible();
  });

  it("dodaje warstwy sieci, stref ochronnych i ryzyk oraz obsługuje ich przełączniki", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const feature = (layer: string) => ({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [] },
      properties: { layer },
    });
    const source = {
      source_name: "KIUT",
      source_url: null,
      fetched_at: null,
      response_status: null,
      confidence: 0.8,
      manual_review_required: false,
    };
    const result = buildAnalyzeResponse({
      infrastructure: [
        {
          network_type: "water",
          buffer_m: 1.5,
          zone_area_sqm: 40,
          rule_source: "config",
          rule_confidence: 0.7,
          rule_note: null,
          affects_buildable_area: true,
          network_geometry_geojson: feature("network"),
          protection_zone_geojson: feature("protection_zone"),
          source,
        },
      ],
      risks: [
        {
          risk_type: "flood_zone",
          description: "Ryzyko powodziowe.",
          geometry_geojson: feature("risk"),
          source: { ...source, source_name: "ISOK" },
        },
      ],
    });

    render(<ResultPanel result={result} map={map} />);

    expect(map.addSource).toHaveBeenCalledWith(
      "network-source",
      expect.objectContaining({ type: "geojson" }),
    );
    expect(map.addSource).toHaveBeenCalledWith(
      "protection-zone-source",
      expect.objectContaining({ type: "geojson" }),
    );
    expect(map.addSource).toHaveBeenCalledWith(
      "risk-source",
      expect.objectContaining({ type: "geojson" }),
    );
    const resultLayerOrder = map.addLayer.mock.calls.map(
      ([layer]) => (layer as { id: string }).id,
    );
    expect(resultLayerOrder.indexOf("risk-fill-layer")).toBeLessThan(
      resultLayerOrder.indexOf("protection-zone-fill-layer"),
    );
    expect(resultLayerOrder.indexOf("protection-zone-fill-layer")).toBeLessThan(
      resultLayerOrder.indexOf("network-line-layer"),
    );

    await user.click(
      screen.getByRole("switch", { name: /Sieci uzbrojenia terenu/ }),
    );
    await user.click(
      screen.getByRole("switch", { name: /Strefy ochronne sieci/ }),
    );
    await user.click(
      screen.getByRole("switch", { name: /Ryzyka i formy ochrony/ }),
    );

    expect(map.setLayoutProperty).toHaveBeenCalledWith(
      "network-line-layer",
      "visibility",
      "none",
    );
    expect(map.setLayoutProperty).toHaveBeenCalledWith(
      "protection-zone-fill-layer",
      "visibility",
      "none",
    );
    expect(map.setLayoutProperty).toHaveBeenCalledWith(
      "risk-fill-layer",
      "visibility",
      "none",
    );
  });

  it("ConfidenceBadge pokazuje komunikat braku metadanych, gdy źródło POG jest null", () => {
    const result = buildAnalyzeResponse({
      pog: {
        schema_version: "2.1",
        legal_status: "unknown",
        coverage_status: "unknown",
        data_availability: "unavailable",
        status_confirmed_at: null,
        legal_status_evidence: null,
        coverage_evidence: null,
        act: null,
        zones: [],
        dominant_zone_id: null,
        ouz: [],
        downtown_areas: [],
        social_infrastructure_standard_areas: [],
        status: "unknown",
        planning_zone: null,
        zone_type: null,
        in_ouz: false,
        area_ratio: null,
        in_downtown_area: false,
        uchwala_nr: null,
        uchwala_date: null,
        manual_review_required: true,
        compatibility_assessment: null,
        raw_attributes: null,
        ouz_intersection_area_sqm: null,
        ouz_intersection_pct: null,
        touches_ouz_boundary: false,
        source: null,
      },
    });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByText("Brak metadanych źródła.")).toBeVisible();
    expect(screen.getByText("Wynik POG wymaga ręcznej weryfikacji.")).toBeVisible();
  });

  it("pokazuje wszystkie trzy strefy POG i rozróżnia null od zera", () => {
    const source = {
      source_name: "POG_APP_LOCAL_POSTGIS",
      source_url: null,
      fetched_at: "2026-09-24T10:00:00Z",
      response_status: null,
      confidence: 1,
      manual_review_required: false,
    };
    const zone = (id: string, symbol: string, area_sqm: number, area_pct: number, height: number | null) => ({
      id, symbol, type: symbol, label: `Strefa ${symbol}`, area_sqm, area_pct,
      max_overground_floor_area_ratio: symbol === "SJ" ? 0 : null,
      max_building_height_m: height,
      max_building_coverage_pct: null,
      min_biologically_active_pct: null,
      primary_profile: [], additional_profiles: [], source,
    });
    const result = buildAnalyzeResponse({
      pog: {
        schema_version: "2.1", legal_status: "binding", coverage_status: "available",
        data_availability: "current", status_confirmed_at: "2026-09-24T10:00:00Z",
        legal_status_evidence: {
          source_name: "Rejestr Urbanistyczny (lokalne wydanie)", official: true,
          reference: "data_release:1", source_id: "pog_app",
          raw_value: "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce",
          confirmed_at: "2026-09-24T10:00:00Z",
        },
        coverage_evidence: null,
        act: { id: "pog-1", version: "v1", title: "POG", resolution_number: null, resolution_date: null },
        zones: [zone("sj", "SJ", 620, 62, 10), zone("su", "SU", 280, 28, 0), zone("sn", "SN", 100, 10, null)],
        dominant_zone_id: "sj", ouz: [], downtown_areas: [], social_infrastructure_standard_areas: [],
        status: "binding", planning_zone: "SJ", zone_type: "SJ", in_ouz: false,
        area_ratio: 0.62, in_downtown_area: false, uchwala_nr: null, uchwala_date: null,
        manual_review_required: false, compatibility_assessment: null, raw_attributes: null,
        ouz_intersection_area_sqm: null, ouz_intersection_pct: null,
        touches_ouz_boundary: false, source,
      },
    });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByRole("table", { name: "Strefy POG przecinające działkę" })).toBeVisible();
    expect(screen.getByText("620.0 m²")).toBeVisible();
    expect(screen.getByText("280.0 m²")).toBeVisible();
    expect(screen.getByText("100.0 m²")).toBeVisible();
    expect(screen.getByText("0 m")).toBeVisible();
  });

  describe("status prawny i pokrycie POG (BK-106)", () => {
    const pogSectionText = () =>
      (screen.getByRole("region", { name: "Plan Ogólny Gminy i OUZ" }).textContent ?? "").toLowerCase();

    it.each([
      ["project", "available"],
      ["in_progress", "partial"],
      ["unknown", "unknown"],
    ] as const)("%s + %s nie jest prezentowany językiem obowiązywania", (legal, coverage) => {
      render(
        <ResultPanel
          result={buildAnalyzeResponse({
            pog: buildPogResult({ legal_status: legal, coverage_status: coverage }),
          })}
          map={null}
        />,
      );
      expect(pogSectionText()).not.toMatch(/obowiązuj/);
      expect(pogSectionText().replaceAll("braku planu", "")).not.toMatch(/brak planu/);
    });

    it("projekt z pełnymi danymi pokazuje status niewiążący i dostępne dane", () => {
      render(
        <ResultPanel
          result={buildAnalyzeResponse({
            pog: buildPogResult({ legal_status: "project", coverage_status: "available" }),
          })}
          map={null}
        />,
      );
      expect(screen.getByTestId("pog-legal-status")).toHaveTextContent("projekt aktu — niewiążący");
      expect(screen.getByTestId("pog-coverage-status")).toHaveTextContent(
        "dane przestrzenne dostępne dla działki",
      );
      expect(
        screen.getByText("Projekt aktu nie jest wiążący i nie może być traktowany jak prawo miejscowe."),
      ).toBeVisible();
    });

    it("binding bez geometrii nie daje komunikatu o braku planu", () => {
      render(
        <ResultPanel
          result={buildAnalyzeResponse({
            pog: buildPogResult({
              legal_status: "binding",
              coverage_status: "act_without_spatial_data",
            }),
          })}
          map={null}
        />,
      );
      expect(screen.getByTestId("pog-legal-status")).toHaveTextContent(/^obowiązuje/);
      expect(screen.getByTestId("pog-coverage-status")).toHaveTextContent(
        "akt bez danych przestrzennych dla działki",
      );
      expect(
        screen.getByText("Brak geometrii lub pusta odpowiedź usługi nie oznacza braku planu."),
      ).toBeVisible();
      expect(screen.getByText(/kod http:\/\/inspire/)).toBeVisible();
      expect(pogSectionText().replaceAll("braku planu", "")).not.toMatch(/brak planu/);
    });

    it("wartość stale pokazuje datę ostatniego potwierdzenia", () => {
      render(
        <ResultPanel
          result={buildAnalyzeResponse({
            pog: buildPogResult({
              legal_status: "binding",
              coverage_status: "unknown",
              data_availability: "stale",
              status_confirmed_at: "2026-08-19T01:00:00Z",
            }),
          })}
          map={null}
        />,
      );
      expect(screen.getByTestId("pog-data-availability")).toHaveTextContent(
        "ostatnia potwierdzona wartość — źródło było niedostępne (potwierdzono 19.08.2026)",
      );
      expect(screen.getByText(/ostatnią potwierdzoną wartość z dnia 19\.08\.2026/)).toBeVisible();
    });

    it("urzędowe potwierdzenie braku aktu jest pokazane ze wskazaniem dokumentu", () => {
      render(
        <ResultPanel
          result={buildAnalyzeResponse({
            pog: buildPogResult({
              coverage_status: "no_act_confirmed",
              status_confirmed_at: "invalid-date",
              coverage_evidence: {
                source_name: "Urząd Gminy",
                official: true,
                reference: "pismo UG.6720.1.2026",
                source_id: null,
                raw_value: null,
                confirmed_at: null,
              },
            }),
          })}
          map={null}
        />,
      );
      expect(screen.getByText("Urząd Gminy — pismo UG.6720.1.2026")).toBeVisible();
      expect(screen.getByTestId("pog-data-availability")).toHaveTextContent("(potwierdzono invalid-date)");
    });
  });

  it("odświeża warstwę GeoJSON (usuwa i dodaje ponownie), gdy geometria działki się zmienia", () => {
    const buildResult = (geojson: Record<string, unknown>) =>
      buildAnalyzeResponse({
        parcel: {
          parcel_identifier: "122101_1.0001.1",
          geometry_geojson: geojson,
          metrics: {
            area_sqm: 1000,
            area_ha: 0.1,
            perimeter_m: 140,
            is_valid: true,
            geometry_repaired: false,
          },
          source: {
            source_name: "ULDK",
            source_url: null,
            fetched_at: null,
            response_status: null,
            confidence: 1,
            manual_review_required: false,
          },
          buildable_area_geojson: null,
        },
      });

    const { rerender } = render(
      <ResultPanel result={buildResult({ type: "Polygon", coordinates: [] })} map={map} />,
    );
    expect(map.addSource).toHaveBeenCalledWith(
      "parcel-source",
      expect.objectContaining({ type: "geojson" }),
    );

    rerender(
      <ResultPanel
        result={buildResult({ type: "Polygon", coordinates: [[]] })}
        map={map}
      />,
    );

    // Nowa referencja GeoJSON w wyniku analizy uruchamia cleanup (removeSource)
    // i ponowne dodanie warstwy — źródło pozostaje aktualne bez wycieku starej
    // instancji ani duplikatu warstwy na mapie.
    expect(map.removeSource).toHaveBeenCalledWith("parcel-source");
    expect(map.addSource).toHaveBeenCalledTimes(2);
  });
});
