import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as maplibregl from "maplibre-gl";

import realCover from "@/test/landCoverFixture.json";
import type { LandCoverResult } from "@/lib/types";
import { ResultPanel } from "@/components/ResultPanel";
import { BUILDINGS_CASES, ROAD_CASES } from "@/test/bdotFixtures";
import { COMPLETENESS_CASES } from "@/test/completenessFixtures";
import { buildAnalyzeResponse, buildPogResult, buildQualityMatrix } from "@/test/fixtures";

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

  it("pokazuje macierz kompletności i świeżości z odpowiedzi API (BK-504)", () => {
    const result = buildAnalyzeResponse({ section_quality: buildQualityMatrix() });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByRole("heading", { name: "Kompletność i świeżość danych" })).toBeVisible();
    expect(screen.getByTestId("quality-matrix")).toBeVisible();
    expect(screen.getByTestId("quality-row-terrain")).toHaveTextContent("brak pokrycia źródła");
    expect(screen.getByTestId("quality-row-nature")).toHaveTextContent("źródło niedostępne");
    expect(screen.getByTestId("quality-legend")).toBeInTheDocument();
    // Status analizy nie zastępuje ocen sekcji.
    expect(screen.getByText(result.status)).toBeVisible();
  });

  it("pokazuje pasek kompletności zamiast sztywnej plakietki statusu (AU-104)", () => {
    const completeness = COMPLETENESS_CASES.partial_pog;
    const result = buildAnalyzeResponse({ status: "partial", completeness, headline: completeness.headline });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByTestId("completeness-headline")).toHaveTextContent(completeness.headline);
    expect(screen.getByRole("progressbar", { name: "Kompletność analizy" })).toHaveAttribute(
      "aria-valuenow",
      String(completeness.checked),
    );
    expect(screen.queryByText("partial")).toBeNull();
  });

  it("odpowiedź sprzed AU-104 zachowuje plakietkę statusu i nie udaje paska", () => {
    render(<ResultPanel result={buildAnalyzeResponse({ status: "partial" })} map={null} />);

    expect(screen.getByText("partial")).toBeVisible();
    expect(screen.getByTestId("completeness-missing")).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("nie udaje kompletności, gdy odpowiedź nie ma macierzy jakości", () => {
    render(<ResultPanel result={buildAnalyzeResponse()} map={null} />);

    expect(screen.getByTestId("quality-missing")).toBeVisible();
    expect(screen.queryByTestId("quality-matrix")).toBeNull();
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

  it("pokazuje odległości powodziowe, brak pomników w 200 m i jawny stan WORP", () => {
    const source = {
      source_id: "isok",
      source_name: "ISOK",
      source_url: null,
      fetched_at: "2026-10-20T20:00:00Z",
      response_status: 200,
      confidence: 0.85,
      manual_review_required: false,
    };
    const result = buildAnalyzeResponse({
      risk_sections: [
        {
          schema_version: "2.0",
          section: "flood",
          status: "available",
          reason_code: null,
          relation: "near",
          feature_count: 1,
          intersecting_feature_count: 0,
          boundary_feature_count: 0,
          near_feature_count: 1,
          union_intersection_area_sqm: 0,
          union_intersection_pct: 0,
          feature_ids: ["HA-Q10"],
          nearest_distance_m: 163.395,
          proximities: [
            {
              category: "q10",
              label: "Q10 (10%, raz na 10 lat)",
              layer_name: "nz-core:HazardArea",
              status: "near",
              search_radius_m: 1000,
              distance_m: 163.395,
              feature_id: "HA-Q10",
              name: null,
              probability_class: "scenariusz Q 10% (raz na 10 lat)",
              return_period_years: 10,
              informational_only: false,
            },
          ],
          preliminary_assessment: {
            status: "contract_required",
            method: "WORP",
            reason_code: "WORP_CONTRACT_NOT_CONFIRMED",
            source_layers: ["nz-core:RiskZone"],
            value: null,
            note: "Nie potwierdzono równoważności produktu.",
          },
          source,
          warnings: [],
        },
        {
          schema_version: "2.0",
          section: "nature",
          status: "available",
          reason_code: null,
          relation: "none_within_radius",
          feature_count: 0,
          intersecting_feature_count: 0,
          boundary_feature_count: 0,
          near_feature_count: 0,
          union_intersection_area_sqm: 0,
          union_intersection_pct: 0,
          feature_ids: [],
          nearest_distance_m: null,
          proximities: [
            {
              category: "pomnik_przyrody",
              label: "Pomniki przyrody (punktowe)",
              layer_name: "GDOS:PomnikiPrzyrodyPunktowe",
              status: "none_within_radius",
              search_radius_m: 200,
              distance_m: null,
              feature_id: null,
              name: null,
              probability_class: null,
              return_period_years: null,
              informational_only: false,
            },
          ],
          preliminary_assessment: null,
          source: { ...source, source_id: "gdos", source_name: "GDOŚ" },
          warnings: [],
        },
      ],
    });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getAllByText(/163,4 m od granicy działki/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Pomniki przyrody \(punktowe\): brak obiektu w promieniu 200 m/)).toBeVisible();
    expect(screen.getByText(/Wstępna ocena WORP: niewyliczona — kontrakt wymaga potwierdzenia/)).toBeVisible();
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

  it("pokazuje karty drogi i zabudowy sąsiedniej z odpowiedzi API wraz ze zdaniem o dostępie prawnym", () => {
    const result = buildAnalyzeResponse({
      road_context: ROAD_CASES.available,
      neighbour_buildings: BUILDINGS_CASES.available,
    });

    render(<ResultPanel result={result} map={null} />);

    expect(screen.getByRole("heading", { name: "Droga i dostęp do drogi" })).toBeVisible();
    expect(screen.getByTestId("road-distance")).toHaveTextContent("12 m");
    expect(screen.getByTestId("road-note")).toHaveTextContent(
      "nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej",
    );
    expect(screen.getByRole("heading", { name: "Zabudowa sąsiednia" })).toBeVisible();
    expect(screen.getByTestId("buildings-count-50")).toHaveTextContent("2");
  });

  it("odpowiedź sprzed sekcji drogi i zabudowy pokazuje stan nieznany, a nie brak drogi ani budynków", () => {
    render(<ResultPanel result={buildAnalyzeResponse()} map={null} />);

    expect(screen.getByTestId("road-status")).toHaveAttribute("data-status", "unknown");
    expect(screen.getByTestId("buildings-status")).toHaveAttribute("data-status", "unknown");
    expect(screen.getByTestId("road-note")).toBeVisible();
  });

  it("dodaje warstwy osi drogi, odcinka działka → oś i budynków oraz obsługuje ich przełączniki", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const result = buildAnalyzeResponse({
      road_context: ROAD_CASES.available,
      neighbour_buildings: BUILDINGS_CASES.available,
    });

    render(<ResultPanel result={result} map={map} />);

    for (const id of ["road-context-source", "road-link-source", "neighbour-buildings-source"]) {
      expect(map.addSource).toHaveBeenCalledWith(id, expect.objectContaining({ type: "geojson" }));
    }
    const order = map.addLayer.mock.calls.map(([layer]) => (layer as { id: string }).id);
    expect(order.indexOf("neighbour-buildings-fill-layer")).toBeLessThan(order.indexOf("road-context-line-layer"));

    await user.click(screen.getByRole("switch", { name: /Najbliższa droga/ }));
    await user.click(screen.getByRole("switch", { name: /Budynki w sąsiedztwie/ }));

    expect(map.setLayoutProperty).toHaveBeenCalledWith("road-context-line-layer", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("road-link-line-layer", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("neighbour-buildings-fill-layer", "visibility", "none");
  });

  it("rysuje prawdziwe pokrycie i bufory lasu, przełącza warstwę i sprząta geometrie", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const result = buildAnalyzeResponse({land_cover: realCover as LandCoverResult});
    const {rerender} = render(<ResultPanel result={result} map={map} />);
    expect(map.addSource).toHaveBeenCalledWith("land-cover-source", expect.objectContaining({type:"geojson"}));
    expect(screen.getByRole("switch", {name:/Pokrycie terenu i orientacyjne/})).toBeEnabled();
    await userEvent.click(screen.getByRole("switch", {name:/Pokrycie terenu i orientacyjne/}));
    expect(map.setLayoutProperty).toHaveBeenCalledWith("land-cover-fill", "visibility", "none");
    rerender(<ResultPanel result={buildAnalyzeResponse({land_cover:null})} map={map} />);
    expect(map.removeSource).toHaveBeenCalledWith("land-cover-source");
    expect(screen.getByRole("switch", {name:/Pokrycie terenu i orientacyjne/})).toBeDisabled();
  });

  it("bez pokrycia BDOT10k przełączniki dróg i budynków są wyłączone z powodem", () => {
    const result = buildAnalyzeResponse({
      road_context: ROAD_CASES.no_coverage,
      neighbour_buildings: BUILDINGS_CASES.no_coverage,
    });

    render(<ResultPanel result={result} map={map} />);

    expect(screen.getByRole("switch", { name: /Najbliższa droga/ })).toBeDisabled();
    expect(screen.getByRole("switch", { name: /Budynki w sąsiedztwie/ })).toBeDisabled();
    expect(map.addSource).not.toHaveBeenCalledWith("road-context-source", expect.anything());
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

  it("AU-110: artefakty styku stref nie wchodzą do tabeli, a nota podaje ich liczbę, pole i udział", () => {
    const zone = (id: string, symbol: string, area_sqm: number, area_pct: number, topological_sliver = false) => ({
      id, symbol, type: symbol, label: null, area_sqm, area_pct,
      max_overground_floor_area_ratio: null, max_building_height_m: null,
      max_building_coverage_pct: null, min_biologically_active_pct: null,
      primary_profile: [], additional_profiles: [], source: null, topological_sliver,
    });
    const withSlivers = buildAnalyzeResponse({
      pog: buildPogResult({
        legal_status: "binding",
        coverage_status: "available",
        zones: [zone("sk", "SK", 1_000, 99.99)],
        topological_slivers: [zone("sw", "SW", 0.1, 0.01, true), zone("su", "SU", 0.08, 0.01, true)],
        dominant_zone_id: "sk",
      }),
    });

    const { unmount } = render(<ResultPanel result={withSlivers} map={null} />);

    const table = screen.getByRole("table", { name: "Strefy POG przecinające działkę" });
    expect(table).toHaveTextContent("SK");
    expect(table).not.toHaveTextContent("SW");
    expect(table).not.toHaveTextContent("SU");
    const note = screen.getByTestId("pog-sliver-note");
    expect(note).toHaveTextContent("Pominięto 2 artefakty styku sąsiednich stref (łącznie 0,18 m², 0,02% działki)");
    expect(note).toHaveTextContent("zachowano je w zapisie analizy");
    unmount();

    // Pojedynczy okruch i brak okruchów (odpowiedź sprzed AU-110 nie ma pola).
    const single = buildAnalyzeResponse({
      pog: buildPogResult({ zones: [zone("sk", "SK", 1_000, 99.9)], topological_slivers: [zone("sw", "SW", 0.1, 0.01, true)] }),
    });
    const second = render(<ResultPanel result={single} map={null} />);
    expect(screen.getByTestId("pog-sliver-note")).toHaveTextContent("Pominięto 1 artefakt styku");
    second.unmount();
    const manyBase = Array.from({ length: 5 }, (_, index) => zone(`s${index}`, "SW", 0.1, 0.01, true));
    const many = render(
      <ResultPanel
        result={buildAnalyzeResponse({ pog: buildPogResult({ zones: [zone("sk", "SK", 1_000, 99.9)], topological_slivers: manyBase }) })}
        map={null}
      />,
    );
    expect(screen.getByTestId("pog-sliver-note")).toHaveTextContent("Pominięto 5 artefaktów styku");
    many.unmount();
    render(<ResultPanel result={buildAnalyzeResponse({ pog: buildPogResult({ zones: [zone("sk", "SK", 1_000, 100)] }) })} map={null} />);
    expect(screen.queryByTestId("pog-sliver-note")).not.toBeInTheDocument();
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
