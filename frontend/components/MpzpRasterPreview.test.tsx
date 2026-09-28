import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MpzpRasterPreview, computeTileMosaic } from "@/components/MpzpRasterPreview";
import { MPZP_PREVIEW_SOURCE, PARCEL_FEATURE } from "@/test/manualZoneFixtures";

describe("computeTileMosaic", () => {
  const options = { minZoom: 11, maxZoom: 18, tileSize: 256 };

  it("wybiera największy zoom mieszczący działkę w siatce 3×3", () => {
    const mosaic = computeTileMosaic(PARCEL_FEATURE, options);

    expect(mosaic).not.toBeNull();
    expect(mosaic!.z).toBeLessThanOrEqual(18);
    expect(mosaic!.z).toBeGreaterThanOrEqual(15);
    expect(mosaic!.tiles).toHaveLength(9);
    for (const [x, y] of mosaic!.rings.flat()) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(mosaic!.width);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(mosaic!.height);
    }
  });

  it("obsługuje Polygon i zwraca null bez geometrii", () => {
    const polygon = { type: "Polygon", coordinates: PARCEL_FEATURE.geometry.coordinates[0] };
    expect(computeTileMosaic(polygon, options)?.rings).toHaveLength(1);
    expect(computeTileMosaic(null, options)).toBeNull();
    expect(computeTileMosaic({ type: "Point", coordinates: [19, 50] }, options)).toBeNull();
  });

  it("dla zbyt dużej działki zostaje na minimalnym zoomie", () => {
    const huge = {
      type: "Polygon",
      coordinates: [[[14, 49], [24, 49], [24, 55], [14, 55], [14, 49]]],
    };
    expect(computeTileMosaic(huge, options)?.z).toBe(11);
  });
});

describe("MpzpRasterPreview", () => {
  beforeEach(() => vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test"));
  afterEach(() => vi.unstubAllEnvs());

  it("renderuje kafle z backendowego proxy i obrys działki", () => {
    render(
      <MpzpRasterPreview parcelGeojson={PARCEL_FEATURE} source={MPZP_PREVIEW_SOURCE} sourcesState="ready" />,
    );

    const tiles = screen.getAllByTestId("mpzp-raster-tile");
    expect(tiles).toHaveLength(9);
    for (const tile of tiles) {
      expect(tile.getAttribute("href")).toMatch(
        /^https:\/\/api\.example\.test\/api\/v1\/map\/tiles\/mpzp\/\d+\/\d+\/\d+\.png$/,
      );
    }
    expect(screen.getByRole("img", { name: /Podgląd rastrowy MPZP/ })).toBeVisible();
    expect(screen.getByText(/nie geometrią obliczeniową/)).toBeVisible();
  });

  it("komunikuje ładowanie, brak źródła i brak geometrii", () => {
    const { rerender } = render(
      <MpzpRasterPreview parcelGeojson={PARCEL_FEATURE} source={undefined} sourcesState="loading" />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Trwa ładowanie");

    rerender(<MpzpRasterPreview parcelGeojson={PARCEL_FEATURE} source={undefined} sourcesState="error" />);
    expect(screen.getByRole("note")).toHaveTextContent("chwilowo niedostępny");

    rerender(<MpzpRasterPreview parcelGeojson={null} source={MPZP_PREVIEW_SOURCE} sourcesState="ready" />);
    expect(screen.getByText("Brak geometrii działki do pokazania na podglądzie.")).toBeVisible();
  });
});
