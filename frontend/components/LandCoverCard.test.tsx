import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LandCoverCard } from "@/components/LandCoverCard";
import realResult from "@/test/landCoverFixture.json";
import type { LandCoverResult } from "@/lib/types";

const result = realResult as LandCoverResult;
afterEach(cleanup);

describe("pokrycie terenu z rzeczywistej geometrii Góry Kalwarii", () => {
  it("pokazuje udziały, odległość, bufory, stan danych i ograniczenie interpretacji", () => {
    render(<LandCoverCard value={result} />);
    expect(screen.getByRole("region", {name: "Pokrycie terenu i lasy"})).toBeInTheDocument();
    expect(screen.getByText("12,73 m")).toBeInTheDocument();
    expect(screen.getAllByRole("table")).toHaveLength(2);
    expect(screen.getAllByText("100%")).toHaveLength(3);
    expect(screen.getByText(/Orientacyjny bufor 8 m/)).toBeInTheDocument();
    expect(screen.getByText(/bez interpretacji przepisów/)).toBeInTheDocument();
    expect(screen.getByTestId("land-cover-release")).toHaveTextContent("1418");
    expect(screen.getByTestId("land-cover-source")).toHaveTextContent("BDOT10k");
  });
  it.each(["no_coverage", "unavailable", "unknown"] as const)("odróżnia brak danych: %s, bez fikcyjnych zer", status => {
    render(<LandCoverCard value={{...result,status,parcel:null,surroundings:null,
      forest:{...result.forest,status,distance_m:null,buffers:[]},warnings:["Brak importu"]}} />);
    expect(screen.getByText(/Brak danych nie oznacza braku lasu/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("0 m")).not.toBeInTheDocument();
    expect(screen.getByText("Brak importu")).toBeInTheDocument();
    expect(screen.queryByText(/zapis sprzed/)).not.toBeInTheDocument();
  });
  it("starszy snapshot ma status nieustalony", () => {
    render(<LandCoverCard value={null} />);
    expect(screen.getByTestId("land-cover-status")).toHaveTextContent(/nieustalony|sprzed/);
    expect(screen.getByRole("note")).toHaveTextContent("EPSG:2180");
  });
  it("zero odległości jest wynikiem, a pusty ograniczony promień nie dowodzi braku lasu", () => {
    const {rerender} = render(<LandCoverCard value={{...result,forest:{...result.forest,distance_m:0}}} />);
    expect(screen.getByText("0 m")).toBeInTheDocument();
    rerender(<LandCoverCard value={{...result,forest:{...result.forest,status:"no_match",distance_m:null,buffers:[]}}} />);
    expect(screen.getByText(/Nie dowodzi to braku lasu poza zasięgiem/)).toBeInTheDocument();
  });
});
