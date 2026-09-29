import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PogFeatureInspector,
  type PogFeatureInspectorProps,
} from "@/components/PogFeatureInspector";
import { getPogAreaSummary, getPogFeatureDetails } from "@/lib/api";
import { POG_LAYER_IDS, pogInspectorHits } from "@/lib/pogLayers";
import {
  INITIAL_TILE_ACTIVITY,
  type PogTileActivity,
  derivePogLayerStatus,
} from "@/lib/pogLayerState";
import type { PogPointQuery, PogReleaseState } from "@/lib/types";
import {
  buildOverlayHit,
  buildPogAreaSummary,
  buildPogFeatureDetails,
  buildPogOverlayProperties,
  buildPogRelease,
  buildPogZoneProperties,
  buildZoneHit,
} from "@/test/pogFixtures";

vi.mock("@/lib/api", () => ({
  getPogFeatureDetails: vi.fn(),
  getPogAreaSummary: vi.fn(),
}));

const detailsMock = vi.mocked(getPogFeatureDetails);
const summaryMock = vi.mocked(getPogAreaSummary);
const LOADED: PogTileActivity = { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, anyTileLoaded: true };
const POINT = { lon: 18.55, lat: 54.45 };
const FORBIDDEN = /(^|[^a-ząćęłńóśźż])brak planu/i;

function layerStatus(
  release: PogReleaseState = {
    status: "available",
    release: buildPogRelease(),
    checkedAt: "2026-09-28T10:00:00Z",
  },
  tiles: PogTileActivity = LOADED,
) {
  return derivePogLayerStatus({ release, tiles, viewport: [18.52, 54.42, 18.58, 54.46] });
}

function renderInspector(overrides: Partial<PogFeatureInspectorProps> = {}) {
  const props: PogFeatureInspectorProps = {
    query: { ...POINT, queried: true, hits: [buildZoneHit()] },
    layerStatus: layerStatus(),
    statusFilter: "all",
    statusFilterLabel: "Wszystkie akty",
    onAnalyze: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  const view = render(<PogFeatureInspector {...props} />);
  return { props, ...view };
}

describe("PogFeatureInspector", () => {
  beforeEach(() => {
    detailsMock.mockReset().mockReturnValue(new Promise(() => undefined));
    summaryMock.mockReset().mockReturnValue(new Promise(() => undefined));
  });

  it("pokazuje natychmiast strefę, 4 parametry, profile, akt/status i wydanie — bez analizy", () => {
    const { props } = renderInspector({
      query: {
        ...POINT,
        queried: true,
        hits: [
          buildZoneHit({
            max_building_height_m: undefined,
            max_building_coverage_pct: 0,
            primary_profiles: "U,MW",
            parameters_informational: true,
          }),
        ],
      },
    });
    const zone = screen.getByTestId("pog-inspector-zone");
    expect(within(zone).getByRole("heading", { name: "SU: SU — strefa usługowa" })).toBeInTheDocument();
    expect(within(zone).getByTestId("pog-inspector-max_overground_floor_area_ratio")).toHaveTextContent("0,9");
    // null nie jest zerowane, a 0 nie jest „brakiem wartości”.
    expect(within(zone).getByTestId("pog-inspector-max_building_height_m")).toHaveTextContent(
      "brak wartości w danych",
    );
    expect(within(zone).getByTestId("pog-inspector-max_building_coverage_pct")).toHaveTextContent("0%");
    expect(within(zone).getByTestId("pog-inspector-min_biologically_active_pct")).toHaveTextContent("5%");
    expect(within(zone).getByTestId("pog-inspector-primary-profiles")).toHaveTextContent("U, MW");
    expect(within(zone).getByText("obowiązuje")).toBeInTheDocument();
    expect(within(zone).getByText("PL.ZIPPZP.10011/226401-POG/1POG")).toBeInTheDocument();
    expect(within(zone).getByTestId("pog-inspector-release")).toHaveTextContent(
      "#42 (pog-0123456789ab), wersja obiektu 20260819T010000",
    );
    expect(within(zone).getByText(/charakter informacyjny/)).toBeInTheDocument();
    expect(screen.getByText(/podgląd bez uruchamiania analizy działki/)).toBeInTheDocument();
    expect(props.onAnalyze).not.toHaveBeenCalled();
    expect(within(zone).getByTestId("pog-inspector-details-state")).toHaveTextContent(
      "Pobieranie szczegółów z wydania #42…",
    );
  });

  it("dwie nakładające się strefy i OUZ/OZS/OSDIS bez duplikatów kaflowych, z tym samym wydaniem", () => {
    const zone = buildPogZoneProperties();
    const project = buildPogZoneProperties({
      feature_id: "PL.ZIPPZP.99999/226401-POG/2POG-1SJ",
      symbol: "SJ",
      zone_code: "SJ",
      legal_status: "project",
      act_id: "PL.ZIPPZP.99999/226401-POG/2POG",
    });
    const ouz = buildPogOverlayProperties();
    const ozs = buildPogOverlayProperties({ feature_id: "OZS-1", symbol: "OZS1", label: "śródmieście" });
    const osdis = buildPogOverlayProperties({ feature_id: "OSDIS-1", symbol: "OSDIS1", label: "standardy" });
    const rendered = [
      { id: 1, layer: { id: POG_LAYER_IDS.zonesFill }, properties: zone },
      { id: 1, layer: { id: POG_LAYER_IDS.zonesFill }, properties: zone },
      { id: 2, layer: { id: POG_LAYER_IDS.zonesFill }, properties: project },
      { id: 3, layer: { id: POG_LAYER_IDS.ouzPattern }, properties: ouz },
      { id: 3, layer: { id: POG_LAYER_IDS.ouzPattern }, properties: ouz },
      { id: 4, layer: { id: POG_LAYER_IDS.downtownPattern }, properties: ozs },
      { id: 5, layer: { id: POG_LAYER_IDS.socialPattern }, properties: osdis },
      { id: 5, layer: { id: POG_LAYER_IDS.socialPattern }, properties: osdis },
    ];
    renderInspector({ query: { ...POINT, queried: true, hits: pogInspectorHits(rendered) } });

    const zones = screen.getAllByTestId("pog-inspector-zone");
    expect(zones).toHaveLength(2);
    expect(screen.getByText("W punkcie nakłada się 2 stref — pokazano wszystkie.")).toBeInTheDocument();
    expect(within(zones[1]).getByTestId("pog-inspector-badge")).toHaveTextContent(
      "projekt / dane niewiążące",
    );
    expect(within(zones[0]).queryByTestId("pog-inspector-badge")).not.toBeInTheDocument();
    const overlays = screen.getAllByTestId("pog-inspector-overlay");
    expect(overlays.map((item) => item.dataset.layer)).toEqual([
      "ouz",
      "downtown",
      "social_infrastructure_standard",
    ]);
    expect(overlays[0]).toHaveTextContent("OUZ — obszar uzupełnienia zabudowy (OUZ1)");
    expect(overlays[1]).toHaveTextContent("OZS");
    expect(overlays[2]).toHaveTextContent("OSDIS");
    for (const item of [...zones, ...overlays]) expect(item).toHaveTextContent("#42");
    expect(screen.getByText(/Dane z kafli mapy — wydanie #42/)).toBeInTheDocument();
    expect(detailsMock).toHaveBeenCalledTimes(5);
    expect(detailsMock).toHaveBeenCalledWith(42, zone.feature_id, expect.anything());
  });

  it("dopiero przycisk uruchamia analizę — dokładnie jedno wywołanie z punktem kliknięcia", async () => {
    const user = userEvent.setup();
    const parentClick = vi.fn();
    const onAnalyze = vi.fn();
    render(
      // Kliknięcia w inspektorze nie propagują do rodzica (np. mapy).
      <div onClick={parentClick}>
        <PogFeatureInspector
          query={{ lon: 18.538, lat: 54.456, queried: true, hits: [buildZoneHit()] }}
          layerStatus={layerStatus()}
          statusFilter="all"
          statusFilterLabel="Wszystkie akty"
          onAnalyze={onAnalyze}
          onClose={vi.fn()}
        />
      </div>,
    );
    await user.click(screen.getByTestId("pog-inspector-zone"));
    expect(onAnalyze).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Analizuj działkę w tym punkcie" }));
    expect(onAnalyze).toHaveBeenCalledOnce();
    expect(onAnalyze).toHaveBeenCalledWith(18.538, 54.456);
    expect(parentClick).not.toHaveBeenCalled();
  });

  it("przycisk jest zablokowany podczas trwającej analizy", () => {
    renderInspector({ analyzing: true });
    expect(screen.getByRole("button", { name: "Analizuj działkę w tym punkcie" })).toBeDisabled();
  });

  it("szczegóły spoza kafla: nazwy profili, akt i pełna etykieta; błąd nie zeruje danych kafla", async () => {
    detailsMock.mockReset();
    detailsMock.mockResolvedValueOnce(buildPogFeatureDetails());
    detailsMock.mockRejectedValueOnce(new Error("503"));
    renderInspector({
      query: {
        ...POINT,
        queried: true,
        hits: [buildZoneHit({ primary_profiles: "U" }), buildOverlayHit("ouz")],
      },
    });
    const zone = screen.getByTestId("pog-inspector-zone");
    expect(await within(zone).findByText("strefa usługowa — pełna etykieta z aktu")).toBeInTheDocument();
    expect(within(zone).getByTestId("pog-inspector-primary-profiles")).toHaveTextContent("U — usługi");
    expect(within(zone).getByText(/Plan ogólny Miasta Sopotu/)).toBeInTheDocument();
    expect(within(zone).getByText(/uchwała XV\/123\/2026 z 19\.08\.2026/)).toBeInTheDocument();
    expect(within(zone).queryByTestId("pog-inspector-details-state")).not.toBeInTheDocument();
    expect(within(zone).getByTestId("pog-inspector-max_building_height_m")).toHaveTextContent("4 m");
    await waitFor(() =>
      expect(screen.getByTestId("pog-inspector-overlay")).toHaveTextContent("obszar uzupełnienia zabudowy"),
    );
  });

  it("błąd szczegółów ma własny komunikat, odrębny od ładowania", async () => {
    detailsMock.mockReset().mockRejectedValue(new Error("503"));
    renderInspector();
    expect(await screen.findByText("Szczegóły niedostępne — powyżej dane z kafla wydania #42.")).toBeInTheDocument();
    expect(screen.getByTestId("pog-inspector-max_building_height_m")).toHaveTextContent("4 m");
  });

  it.each([
    ["loading", { status: "loading", release: null }, LOADED, true, POINT, /jeszcze się ładuje/],
    ["unavailable", { status: "error", release: null }, LOADED, true, POINT, /Warstwa POG jest niedostępna/],
    [
      "no_coverage",
      undefined,
      LOADED,
      true,
      { lon: 21.0, lat: 52.2 },
      /poza zasięgiem danych wydania #42/,
    ],
    ["no_object", undefined, LOADED, true, POINT, /Brak obiektu POG w tym punkcie w wydaniu #42/],
    ["loading", undefined, INITIAL_TILE_ACTIVITY, true, POINT, /jeszcze się wczytują/],
    ["unavailable", undefined, LOADED, false, POINT, /Warstwa POG jest niedostępna/],
  ] as const)(
    "brak trafień w stanie %s ma odrębny komunikat bez „brak planu”",
    (kind, release, tiles, queried, point, message) => {
      renderInspector({
        query: { ...point, queried, hits: [] },
        layerStatus: layerStatus(release as PogReleaseState | undefined, tiles),
      });
      const empty = screen.getByTestId("pog-inspector-empty");
      expect(empty).toHaveAttribute("data-kind", kind);
      expect(empty).toHaveTextContent(message);
      expect(empty.textContent).not.toMatch(FORBIDDEN);
      expect(screen.getByRole("button", { name: "Analizuj działkę w tym punkcie" })).toBeEnabled();
      expect(detailsMock).not.toHaveBeenCalled();
    },
  );

  it("same nakładki bez strefy: jawna informacja o luce w danych, bez „brak planu”", () => {
    const { unmount } = renderInspector({
      query: { ...POINT, queried: true, hits: [buildOverlayHit("ouz")] },
    });
    const noZone = screen.getByTestId("pog-inspector-no-zone");
    expect(noZone).toHaveTextContent("wydanie #42 nie zawiera strefy planistycznej (luka w danych wydania)");
    expect(noZone.textContent).not.toMatch(FORBIDDEN);
    expect(screen.getAllByTestId("pog-inspector-overlay")).toHaveLength(1);
    unmount();
    renderInspector({
      query: { ...POINT, queried: true, hits: [buildOverlayHit("ouz")] },
      layerStatus: layerStatus(undefined, { ...LOADED, tileErrors: 1 }),
    });
    expect(screen.getByTestId("pog-inspector-no-zone")).toHaveAttribute("data-kind", "partial");
  });

  it("brak nakładek opisuje się tylko przy wczytanej warstwie; filtr edycji jest nazwany", () => {
    const { unmount } = renderInspector();
    expect(screen.getByTestId("pog-inspector-no-overlays")).toHaveTextContent(
      "W tym punkcie wydanie #42 nie zawiera obszarów OUZ, OZS ani OSDIS.",
    );
    unmount();
    renderInspector({ statusFilter: "binding", statusFilterLabel: "Tylko akty obowiązujące" });
    expect(screen.getByTestId("pog-inspector-no-overlays")).toHaveTextContent(
      /aktywnym filtrze „Tylko akty obowiązujące”/,
    );
  });

  it("focus trafia na nagłówek, Escape zamyka i przywraca focus", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const mapCanvas = document.createElement("button");
    mapCanvas.textContent = "mapa";
    document.body.appendChild(mapCanvas);
    mapCanvas.focus();
    renderInspector({ onClose });
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveFocus();
    expect(heading).toHaveTextContent("Plan ogólny w punkcie 54,45000, 18,55000");
    expect(screen.getByRole("region", { name: /Plan ogólny w punkcie/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    expect(mapCanvas).toHaveFocus();
    mapCanvas.remove();
  });

  it("przycisk Zamknij zamyka inspektor", async () => {
    const user = userEvent.setup();
    const { props } = renderInspector();
    await user.click(screen.getByRole("button", { name: "Zamknij inspektor (Escape)" }));
    expect(props.onClose).toHaveBeenCalledOnce();
  });

  it("obiekty z różnych wydań są jawnie oznaczone", () => {
    renderInspector({
      query: {
        ...POINT,
        queried: true,
        hits: [buildZoneHit(), buildZoneHit({ data_release_id: 43 }, 999)],
      },
    });
    expect(screen.getByText(/pochodzą z różnych wydań \(#42, #43\)/)).toBeInTheDocument();
  });

  it("struktura stref aktu jest dostępna z inspektora i nie uruchamia analizy", async () => {
    const user = userEvent.setup();
    summaryMock.mockReset().mockResolvedValue(buildPogAreaSummary());
    const { props } = renderInspector();
    await user.click(screen.getByRole("button", { name: "Struktura stref aktu i gminy" }));
    expect(await screen.findByTestId("pog-area-table")).toBeInTheDocument();
    expect(summaryMock).toHaveBeenCalledWith(
      42,
      { actId: "PL.ZIPPZP.10011/226401-POG/1POG" },
      expect.anything(),
    );
    expect(props.onAnalyze).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Ukryj strukturę stref" }));
    expect(screen.queryByTestId("pog-area-table")).not.toBeInTheDocument();
  });

  it("nowe kliknięcie przerywa pobieranie szczegółów poprzedniego punktu", () => {
    const first: PogPointQuery = { ...POINT, queried: true, hits: [buildZoneHit()] };
    const { rerender, props } = renderInspector({ query: first });
    const signal = detailsMock.mock.calls[0][2]?.signal as AbortSignal;
    rerender(
      <PogFeatureInspector {...props} query={{ ...POINT, lon: 18.56, queried: true, hits: [buildZoneHit()] }} />,
    );
    expect(signal.aborted).toBe(true);
    expect(detailsMock).toHaveBeenCalledTimes(2);
  });
});
