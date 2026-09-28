import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { analyzeParcel, getPreviewSources, resumeAnalysis } from "@/lib/api";
import type { PreviewSource } from "@/lib/types";
import { buildAnalyzeResponse } from "@/test/fixtures";

vi.mock("@/components/MapViewLoader", () => ({
  MapViewLoader: ({
    onMapClick,
  }: {
    onMapClick: (lon: number, lat: number) => void;
  }) => (
    <button type="button" onClick={() => onMapClick(21.01, 52.23)}>
      Testowy punkt mapy
    </button>
  ),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    analyzeParcel: vi.fn(),
    getPreviewSources: vi.fn(),
    resumeAnalysis: vi.fn(),
  };
});

const analyzeParcelMock = vi.mocked(analyzeParcel);
const getPreviewSourcesMock = vi.mocked(getPreviewSources);
const resumeAnalysisMock = vi.mocked(resumeAnalysis);
const previewSources: PreviewSource[] = [
  {
    source_key: "mpzp",
    label: "Miejscowe plany zagospodarowania przestrzennego",
    attribution: "KIMPZP",
    min_zoom: 11,
    max_zoom: 18,
    tile_size: 256,
    tile_url_template: "/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png",
    legal_note: "Podgląd poglądowy.",
    info_url: "https://info.example.test/mpzp",
    catalog_status: "production",
  },
  {
    source_key: "pog",
    label: "Plany ogólne gmin",
    attribution: "POG",
    min_zoom: 11,
    max_zoom: 18,
    tile_size: 256,
    tile_url_template: "/api/v1/map/tiles/pog/{z}/{x}/{y}.png",
    legal_note: "Podgląd poglądowy.",
    info_url: "https://info.example.test/pog",
    catalog_status: "production",
  },
  {
    source_key: "kiut",
    label: "Uzbrojenie terenu",
    attribution: "KIUT",
    min_zoom: 17,
    max_zoom: 20,
    tile_size: 512,
    tile_url_template: "/api/v1/map/tiles/kiut/{z}/{x}/{y}.png",
    legal_note: "Podgląd poglądowy.",
    info_url: "https://info.example.test/kiut",
    catalog_status: "production",
  },
];

describe("strona główna", () => {
  beforeEach(() => {
    analyzeParcelMock.mockReset();
    resumeAnalysisMock.mockReset();
    getPreviewSourcesMock.mockReset();
    getPreviewSourcesMock.mockResolvedValue(previewSources);
    window.localStorage.clear();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
  });

  afterEach(() => vi.unstubAllEnvs());

  it("pokazuje przełączniki warstw podglądowych jeszcze przed analizą", async () => {
    render(<HomePage />);

    expect(
      screen.getByRole("complementary", { name: "Warstwy podglądowe" }),
    ).toBeVisible();
    expect(await screen.findByRole("switch", { name: /MPZP/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(
      screen.getByRole("switch", { name: /Plan Ogólny Gminy/ }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: /Uzbrojenie/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByText(/Brak obiektów na mapie nie potwierdza/)).toBeVisible();
  });

  it("łączy klik mapy z POST /analyze i pokazuje krótki wynik", async () => {
    const user = userEvent.setup();
    analyzeParcelMock.mockResolvedValue(
      buildAnalyzeResponse({
        status: "partial",
        analysis_id: 77,
        manual_zone_required: true,
        warnings: [
          {
            code: "MPZP_PARTIAL",
            message: "Dane częściowe",
            severity: "warning",
            source_name: "mpzp",
          },
        ],
      }),
    );
    render(<HomePage />);

    await user.click(screen.getByRole("button", { name: "Testowy punkt mapy" }));

    await waitFor(() =>
      expect(analyzeParcelMock).toHaveBeenCalledWith(
        { method: "map", lon: 21.01, lat: 52.23 },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(await screen.findByText("partial")).toBeVisible();
    expect(screen.getByText("77")).toBeVisible();
    expect(
      screen.getByRole("form", { name: "Ręczne podanie symbolu strefy MPZP" }),
    ).toBeVisible();
  });

  it("przepływ ręcznego symbolu: podgląd źródła → symbol → wynik partial", async () => {
    const { manualZone, waitingResponse } = await import("@/test/manualZoneFixtures");
    const user = userEvent.setup();
    analyzeParcelMock.mockResolvedValue(waitingResponse());
    resumeAnalysisMock.mockResolvedValue(
      buildAnalyzeResponse({
        analysis_id: 77,
        status: "partial",
        manual_zone_required: false,
        mpzp_zones: [manualZone()],
      }),
    );
    render(<HomePage />);

    await user.click(screen.getByRole("button", { name: "Testowy punkt mapy" }));
    const form = await screen.findByRole("form", { name: "Ręczne podanie symbolu strefy MPZP" });
    expect(await within(form).findAllByTestId("mpzp-raster-tile")).toHaveLength(9);
    expect(within(form).getByTestId("manual-zone-plan-id")).toHaveTextContent("MPZP/2020/1");

    await user.click(within(form).getByRole("button", { name: "230_U" }));
    await user.click(within(form).getByRole("checkbox"));
    await user.click(within(form).getByRole("button", { name: "Wznów analizę" }));

    await waitFor(() =>
      expect(resumeAnalysisMock).toHaveBeenCalledWith(
        { analysis_id: 77, zone_symbol: "230_U" },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(await screen.findByText("partial")).toBeVisible();
    expect(screen.getByTestId("manual-zone-result-note")).toBeVisible();
    expect(screen.getByText(/Udział w powierzchni działki: nieustalony/)).toBeVisible();
    expect(screen.queryByRole("form", { name: "Ręczne podanie symbolu strefy MPZP" })).toBeNull();
  });
});
