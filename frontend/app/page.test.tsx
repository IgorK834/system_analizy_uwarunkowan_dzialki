import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { analyzeParcel } from "@/lib/api";
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
  return { ...actual, analyzeParcel: vi.fn() };
});

const analyzeParcelMock = vi.mocked(analyzeParcel);

describe("strona główna", () => {
  beforeEach(() => {
    analyzeParcelMock.mockReset();
    window.localStorage.clear();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    vi.stubEnv("NEXT_PUBLIC_KIMPZP_TILE_URL", "");
  });

  afterEach(() => vi.unstubAllEnvs());

  it("pokazuje przełączniki nakładek planistycznych jeszcze przed analizą", () => {
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_URL", "https://wms.example.test/pog");

    render(<HomePage />);

    expect(
      screen.getByRole("complementary", { name: "Nakładki planistyczne" }),
    ).toBeVisible();
    expect(screen.getByRole("switch", { name: /MPZP/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(
      screen.getByRole("switch", { name: /Plan Ogólny Gminy/ }),
    ).toHaveAttribute("aria-checked", "true");
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
});
