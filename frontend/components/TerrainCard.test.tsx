import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResultPanel } from "@/components/ResultPanel";
import { TerrainCard } from "@/components/TerrainCard";
import { buildAnalyzeResponse } from "@/test/fixtures";
import {
  buildRelief,
  buildTerrain,
  FLAT_TERRAIN,
  NO_COVERAGE_TERRAIN,
  TIMEOUT_TERRAIN,
} from "@/test/terrainFixtures";

describe("TerrainCard", () => {
  it("pokazuje kontrolny pomiar 112,3–115,7 m jako deniwelację 3,4 m z metadanymi", () => {
    render(<TerrainCard terrain={buildTerrain()} />);

    expect(screen.getByTestId("terrain-status")).toHaveAttribute("data-status", "available");
    expect(screen.getByTestId("terrain-min")).toHaveTextContent("112,3 m");
    expect(screen.getByTestId("terrain-max")).toHaveTextContent("115,7 m");
    expect(screen.getByTestId("terrain-height-difference")).toHaveTextContent("3,4 m");
    expect(screen.getByText("Siatka próbkowania").nextSibling).toHaveTextContent("4 m");
    expect(screen.getByText("Punkty siatki").nextSibling).toHaveTextContent("676");
    expect(screen.getByTestId("terrain-source")).toHaveTextContent("Źródło: NMT, pobrano 28.09.2026 · pewność 90%");
    expect(screen.queryByTestId("terrain-note")).not.toBeInTheDocument();
  });

  it("pokazuje spadek, klasy, ekspozycję, profil, rozdzielczość i źródło rastra", () => {
    render(<TerrainCard terrain={buildTerrain()} />);

    const relief = screen.getByTestId("terrain-relief");
    expect(relief).toHaveAttribute("data-status", "available");
    expect(screen.getByTestId("relief-resolution")).toHaveTextContent("1 m");
    expect(within(relief).getByText("P90").closest("tr")).toHaveTextContent("9,1°16,01%");
    const classes = screen.getByTestId("relief-classes");
    expect(within(classes).getAllByRole("row")).toHaveLength(7);
    expect(within(classes).getByText("umiarkowany (5–10%)").closest("tr")).toHaveTextContent("1128 m²11,28%");
    expect(screen.getByTestId("relief-aspect")).toHaveTextContent("rozproszona — brak dominującego kierunku");
    const profile = within(screen.getByTestId("relief-profile"));
    expect(profile.getByRole("img")).toHaveAccessibleName(/długość 100 m, krok 25 m, 5 próbek, w tym 1 bez danych/);
    expect(screen.getByTestId("relief-profile").querySelectorAll("polyline")).toHaveLength(2);
    expect(screen.getByTestId("relief-raster")).toHaveTextContent(
      "Raster DTM_PL-KRON86-NH_TIFF, układ wysokości PL-KRON86-NH, 105×105 px (bufor 2 m); algorytm horn1981-3x3-v1; GDAL 3.10.3",
    );
    expect(screen.getByTestId("relief-source")).toHaveTextContent("NMT_WCS (DTM_PL-KRON86-NH_TIFF (WCS 2.0.1))");
    expect(screen.getByText(/różnią się od usługi GetMinMaxByPolygon/)).toBeInTheDocument();
  });

  it("brak pokrycia nie pokazuje wysokości ani płaskiego terenu", () => {
    render(<TerrainCard terrain={NO_COVERAGE_TERRAIN} />);

    expect(screen.getByTestId("terrain-status")).toHaveTextContent(
      "brak pokrycia danymi NMT (usługa zwróciła znacznik braku danych)",
    );
    expect(screen.queryByTestId("terrain-height-difference")).not.toBeInTheDocument();
    expect(screen.getByTestId("terrain-note")).toHaveTextContent("brak pokrycia nie oznacza płaskiego terenu");
    expect(screen.getByText("NMT nie ma danych wysokościowych dla obszaru działki.")).toBeInTheDocument();
    expect(screen.getByTestId("relief-missing")).toBeInTheDocument();
  });

  it("timeout i nieudany raster są opisane jako niedostępne z przyczyną", () => {
    render(<TerrainCard terrain={TIMEOUT_TERRAIN} />);

    expect(screen.getByTestId("terrain-status")).toHaveAttribute("data-status", "unavailable");
    expect(screen.getByTestId("terrain-status")).toHaveTextContent("nie odpowiedziała w wymaganym czasie");
    expect(screen.getByTestId("terrain-source")).toHaveTextContent("pewność 0% · wymaga weryfikacji");
    expect(screen.getByTestId("relief-unavailable")).toHaveTextContent(
      "Spadku, ekspozycji i profilu nie policzono — pomiar niedostępny (raster przekracza limit rozmiaru). Brak statystyk nie oznacza płaskiego terenu.",
    );
    expect(screen.queryByTestId("relief-classes")).not.toBeInTheDocument();
  });

  it("zapis bez sekcji terrain jest stanem unknown bez metadanych", () => {
    render(<TerrainCard terrain={undefined} />);

    expect(screen.getByTestId("terrain-status")).toHaveAttribute("data-status", "unknown");
    expect(screen.getByTestId("terrain-status")).toHaveTextContent("brak informacji w zapisanym wyniku (zapis sprzed sekcji NMT)");
    expect(screen.getByTestId("terrain-source")).toHaveTextContent("Brak metadanych źródła NMT.");
    expect(screen.queryByTestId("terrain-min")).not.toBeInTheDocument();
  });

  it("faktyczne 0 m jest pokazane jako zmierzony płaski teren", () => {
    render(<TerrainCard terrain={FLAT_TERRAIN} />);

    expect(screen.getByTestId("terrain-height-difference")).toHaveTextContent("0 m");
    expect(screen.getByTestId("terrain-note")).toHaveTextContent("wynosi 0 m");
    expect(screen.getByTestId("relief-aspect")).toHaveTextContent("nie wyznaczono — teren płaski");
  });

  it("pomija opcjonalne elementy, gdy brak profilu, rastra i ekspozycji", () => {
    render(
      <TerrainCard
        terrain={buildTerrain({
          grid_size_m: null,
          sampled_points: null,
          relief: buildRelief({ profile: null, raster: null, aspect: null, source: null, warnings: [] }),
        })}
      />,
    );

    expect(screen.queryByTestId("relief-profile")).not.toBeInTheDocument();
    expect(screen.queryByTestId("relief-raster")).not.toBeInTheDocument();
    expect(screen.getByTestId("relief-aspect")).toHaveTextContent("—");
    expect(screen.getByTestId("relief-source")).toHaveTextContent("Brak metadanych źródła NMT.");
    expect(screen.queryByText("Siatka próbkowania")).not.toBeInTheDocument();
  });

  it("profil bez wysokości ma tylko opis, bez wykresu", () => {
    const relief = buildRelief();
    const profile = {
      ...relief.profile!,
      samples: relief.profile!.samples.map((sample) => ({ ...sample, height_m: null })),
    };
    render(<TerrainCard terrain={buildTerrain({ relief: { ...relief, profile } })} />);

    const figure = screen.getByTestId("relief-profile");
    expect(figure.querySelector("svg")).toBeNull();
    expect(figure).toHaveTextContent("brak wysokości do narysowania");
  });

  it("jest częścią panelu wyników", () => {
    render(<ResultPanel result={buildAnalyzeResponse({ terrain: buildTerrain() })} map={null} />);

    expect(screen.getByRole("region", { name: "Rzeźba terenu (NMT)" })).toBeInTheDocument();
    expect(screen.getByTestId("terrain-height-difference")).toHaveTextContent("3,4 m");
  });
});
