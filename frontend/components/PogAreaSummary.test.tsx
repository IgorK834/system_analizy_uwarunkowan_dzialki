import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PogAreaSummary,
  formatShare,
  formatSqkm,
  summaryRows,
} from "@/components/PogAreaSummary";
import { getPogAreaSummary } from "@/lib/api";
import { buildPogAreaSummary } from "@/test/pogFixtures";

vi.mock("@/lib/api", () => ({ getPogAreaSummary: vi.fn() }));

const summaryMock = vi.mocked(getPogAreaSummary);
const ACT = "PL.ZIPPZP.10011/226401-POG/1POG";

function chartValues(): string[] {
  return screen.getAllByTestId("pog-area-bar-value").map((item) => item.textContent ?? "");
}

function tableValues(column: "pog-area-cell-share" | "pog-area-cell-area"): string[] {
  return within(screen.getByTestId("pog-area-table"))
    .getAllByTestId(column)
    .map((item) => item.textContent ?? "");
}

describe("PogAreaSummary", () => {
  beforeEach(() => summaryMock.mockReset());

  it("akt 1 km² 60/40: wykres i tabela pokazują identyczne liczby", async () => {
    summaryMock.mockResolvedValue(buildPogAreaSummary());
    render(<PogAreaSummary releaseId={42} actId={ACT} teryt="226401" legalStatus="binding" />);

    expect(await screen.findByTestId("pog-area-table")).toBeInTheDocument();
    expect(chartValues()).toEqual(["60,0%", "40,0%"]);
    expect(tableValues("pog-area-cell-share")).toEqual(chartValues());
    expect(tableValues("pog-area-cell-area")).toEqual(["0,600 km²", "0,400 km²"]);
    expect(screen.getAllByTestId("pog-area-bar").map((bar) => bar.dataset.key)).toEqual(["SW", "SU"]);
    expect(screen.getByRole("img", { name: `Udziały stref w powierzchni aktu ${ACT}` })).toBeInTheDocument();
    expect(screen.getByText(/Mianownik: 1,000 km² \(granica aktu ze źródła\)/)).toBeInTheDocument();
    expect(screen.queryByTestId("pog-area-partial")).not.toBeInTheDocument();
    expect(screen.getByText(/Wydanie #42 \(pog-0123456789ab\), metoda pog-aggregates\/1, policzono 29\.09\.2026/)).toBeInTheDocument();
    const footer = within(screen.getByTestId("pog-area-table")).getByRole("row", { name: /Suma udziałów/ });
    expect(footer).toHaveTextContent("100,0%");
    expect(summaryMock).toHaveBeenCalledWith(42, { actId: ACT }, expect.anything());
  });

  it("luka 0,1 km²: dane niepełne oznaczone, jawny mianownik i wiersz luki w obu widokach", async () => {
    summaryMock.mockResolvedValue(
      buildPogAreaSummary({
        is_complete: false,
        incomplete_reasons: ["missing_area", "share_sum_out_of_tolerance"],
        zones: [
          { zone_code: "SW", area_sqm: 600_000, area_sqkm: 0.6, share_pct: 60, zone_count: 1 },
          { zone_code: "SU", area_sqm: 300_000, area_sqkm: 0.3, share_pct: 30, zone_count: 1 },
        ],
        zones_area_sqm: 900_000,
        zones_area_sqkm: 0.9,
        missing_area_sqm: 100_000,
        missing_area_sqkm: 0.1,
        share_sum_pct: 90,
      }),
    );
    render(<PogAreaSummary releaseId={42} actId={ACT} legalStatus="binding" />);

    const partial = await screen.findByTestId("pog-area-partial");
    expect(partial).toHaveTextContent("Dane niepełne.");
    expect(partial).toHaveTextContent("luka: część obszaru aktu nie ma przypisanej strefy");
    expect(partial).toHaveTextContent("suma udziałów odbiega od 100% ponad tolerancję");
    expect(screen.getByText(/Mianownik: 1,000 km²/)).toBeInTheDocument();
    expect(chartValues()).toEqual(["60,0%", "30,0%", "10,0%"]);
    expect(tableValues("pog-area-cell-share")).toEqual(chartValues());
    expect(screen.getByRole("row", { name: /luka — obszar aktu bez strefy/ })).toHaveTextContent("0,100 km²");
  });

  it("brak mianownika: udziały „nie obliczono”, skala względna, nigdy 100%", async () => {
    summaryMock.mockResolvedValue(
      buildPogAreaSummary({
        is_complete: false,
        incomplete_reasons: ["no_boundary"],
        denominator_area_sqm: null,
        denominator_area_sqkm: null,
        denominator_source: null,
        missing_area_sqm: null,
        missing_area_sqkm: null,
        share_sum_pct: null,
        zones: [
          { zone_code: "SW", area_sqm: 600_000, area_sqkm: 0.6, share_pct: null, zone_count: 1 },
          { zone_code: "XX", area_sqm: 400_000, area_sqkm: 0.4, share_pct: null, zone_count: 2 },
        ],
      }),
    );
    render(<PogAreaSummary releaseId={42} actId={ACT} legalStatus="unknown" />);

    expect(await screen.findByTestId("pog-area-partial")).toHaveTextContent(
      "brak granicy aktu w źródle — udziałów nie obliczono (to nie jest 100%)",
    );
    expect(screen.getByText(/Mianownik: brak/)).toBeInTheDocument();
    expect(tableValues("pog-area-cell-share")).toEqual(["nie obliczono", "nie obliczono"]);
    // Wykres bez mianownika pokazuje pola (te same napisy co kolumna tabeli).
    expect(chartValues()).toEqual(tableValues("pog-area-cell-area"));
    expect(screen.getByRole("img", { name: /skala względna, udziałów nie obliczono/ })).toBeInTheDocument();
    expect(screen.queryByText("100,0%")).not.toBeInTheDocument();
    // Akt o nieustalonym statusie nie ma agregatu gminy.
    expect(screen.getByRole("radio", { name: /Gmina/ })).toBeDisabled();
    expect(screen.getByText(/wyłącznie agregat aktu/)).toBeInTheDocument();
  });

  it("przełącza zakres na gminę w edycji zgodnej ze statusem aktu", async () => {
    const user = userEvent.setup();
    summaryMock
      .mockResolvedValueOnce(buildPogAreaSummary())
      .mockResolvedValueOnce(
        buildPogAreaSummary({
          scope: "municipality",
          edition: "project",
          act_id: null,
          act_count: 2,
          deduplicated_area_sqm: 500_000,
          denominator_source: "act_boundaries_union",
        }),
      );
    render(<PogAreaSummary releaseId={42} actId={ACT} teryt="226401" legalStatus="project" />);
    await screen.findByTestId("pog-area-table");
    await user.click(screen.getByRole("radio", { name: /Gmina 226401 \(projekty\)/ }));
    expect(summaryMock).toHaveBeenLastCalledWith(
      42,
      { teryt: "226401", edition: "project" },
      expect.anything(),
    );
    expect(await screen.findByText(/suma granic aktów bez podwójnego liczenia/)).toBeInTheDocument();
    expect(screen.getByText(/Nakładające się akty policzono raz \(0,500 km²\)/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /gminy 226401 \(projekty, 2 akt\.\)/ })).toBeInTheDocument();
  });

  it("odrębne komunikaty: ładowanie, brak agregatu (nie brak planu) i błąd", async () => {
    let resolve: (value: null) => void = () => undefined;
    summaryMock.mockReturnValueOnce(new Promise((done) => (resolve = done)));
    const { unmount } = render(<PogAreaSummary releaseId={7} actId={ACT} legalStatus="binding" />);
    expect(screen.getByText("Wczytywanie gotowego agregatu stref…")).toBeInTheDocument();
    resolve(null);
    expect(await screen.findByText(/nie jest dostępny dla tego zakresu w wydaniu #7/)).toHaveTextContent(
      "Nie oznacza to braku stref ani planu.",
    );
    unmount();

    summaryMock.mockRejectedValueOnce(new Error("503"));
    render(<PogAreaSummary releaseId={7} actId={ACT} legalStatus="binding" />);
    expect(await screen.findByText("Nie udało się pobrać agregatu stref — spróbuj ponownie.")).toBeInTheDocument();
  });

  it("formatowanie i wiersze są jedną funkcją dla wykresu i tabeli", () => {
    expect(formatSqkm(1.23456)).toBe("1,235 km²");
    expect(formatShare(null)).toBe("nie obliczono");
    expect(formatShare(33.333)).toBe("33,3%");
    const rows = summaryRows(buildPogAreaSummary({ zones: [] }));
    expect(rows).toEqual([]);
    const unknown = summaryRows(
      buildPogAreaSummary({
        zones: [{ zone_code: "XX", area_sqm: 1, area_sqkm: 0.000001, share_pct: 150, zone_count: 1 }],
      }),
    );
    expect(unknown[0]).toMatchObject({ pattern: "cross-hatch", barFraction: 1 });
  });
});
