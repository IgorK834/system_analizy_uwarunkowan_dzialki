import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PogZoneShareChart } from "@/components/PogZoneShareChart";
import { POG_UNKNOWN_ZONE, zoneStyle } from "@/lib/pogZones";
import type { PogZoneResult } from "@/lib/types";

function zone(id: string, type: string, pct: number): PogZoneResult {
  return {
    id,
    symbol: type,
    type,
    label: null,
    area_sqm: pct * 10,
    area_pct: pct,
    max_overground_floor_area_ratio: null,
    max_building_height_m: null,
    max_building_coverage_pct: null,
    min_biologically_active_pct: null,
    primary_profile: [],
    additional_profiles: [],
    source: null,
  };
}

describe("PogZoneShareChart", () => {
  it("koloruje udziały tą samą paletą co mapa i opisuje je tekstem", () => {
    render(<PogZoneShareChart zones={[zone("a", "SU", 62.5), zone("b", "XX", 37.5)]} />);
    const segments = screen.getAllByTestId("pog-share-segment");
    expect(segments.map((item) => item.dataset.color)).toEqual([
      zoneStyle("SU").fill,
      POG_UNKNOWN_ZONE.fill,
    ]);
    expect(segments[0]).toHaveStyle({ width: "62.5%" });
    expect(segments[1].style.backgroundImage).toContain("repeating-linear-gradient");
    expect(
      screen.getByRole("img", {
        name: /SU — strefa usługowa: 62,5%; strefa nierozpoznana \(kod spoza słownika\): 37,5%/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("SU — strefa usługowa — 62,5%")).toBeInTheDocument();
  });

  it("nie renderuje się bez stref", () => {
    const { container } = render(<PogZoneShareChart zones={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
