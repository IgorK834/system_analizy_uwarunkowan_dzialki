import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MpzpDiscoveryCard } from "@/components/MpzpDiscoveryCard";
import { ResultPanel } from "@/components/ResultPanel";
import { buildAnalyzeResponse } from "@/test/fixtures";
import { buildDiscovery, GORA_KALWARIA_DISCOVERY, NO_COVERAGE_DISCOVERY } from "@/test/mpzpDiscoveryFixtures";

describe("MpzpDiscoveryCard", () => {
  it("pokazuje oba akty Góry Kalwarii z linkami, a LIV/467/2021 tylko jako zmianę", () => {
    render(<MpzpDiscoveryCard discovery={GORA_KALWARIA_DISCOVERY} />);

    const acts = screen.getAllByTestId("mpzp-discovery-act");
    expect(acts.map((act) => act.getAttribute("data-act"))).toEqual(["IV/30/2024", "576/XLVII/2010"]);
    expect(acts[0]).toHaveTextContent("obowiązuje od 26.06.2024");
    expect(within(acts[0]).getByText("http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf")).toBeVisible();
    expect(within(acts[0]).queryByRole("link", { name: "Tekst uchwały" })).toBeNull(); // HTTP — bez href
    expect(within(acts[0]).getByRole("link", { name: "Strona BIP" })).toHaveAttribute(
      "href",
      "https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/757992",
    );
    const amendments = within(acts[1]).getAllByTestId("mpzp-discovery-amendment");
    expect(amendments.map((item) => item.getAttribute("data-amendment"))).toEqual([
      "",
      "LIV/467/2021",
      "XXXIX/366/2017",
    ]);
    expect(within(acts[1]).getByRole("link", { name: "LIV/467/2021" })).toHaveAttribute(
      "href",
      "https://bip-v1-files.idcom-jst.pl/sites/47313/wiadomosci/582692/files/liv_467_2021.pdf",
    );
    expect(within(acts[0]).queryByText(/LIV\/467\/2021/)).toBeNull();
    expect(screen.getByTestId("mpzp-discovery-multiple")).toHaveTextContent("nie wybiera aktu automatycznie");
    expect(screen.queryByText("użyta w analizie")).toBeNull();
  });

  it("brak serwisu to brak danych, nie brak planu", () => {
    render(<MpzpDiscoveryCard discovery={NO_COVERAGE_DISCOVERY} />);

    expect(screen.getByTestId("mpzp-discovery")).toHaveAttribute("data-status", "no_coverage");
    expect(screen.getByTestId("mpzp-discovery-status")).toHaveTextContent("Brak usługi gminnej w KIMPZP");
    expect(screen.getByTestId("mpzp-discovery-note")).toHaveTextContent("nie potwierdzenie braku planu");
    expect(screen.queryAllByTestId("mpzp-discovery-act")).toHaveLength(0);
  });

  it("oznacza jedyny akt użyty w analizie oraz nieudane punkty i plan rastrowy", () => {
    const [act] = GORA_KALWARIA_DISCOVERY.acts;
    render(
      <MpzpDiscoveryCard
        discovery={buildDiscovery({
          acts: [{ ...act, informatization: "raster", repealed_on: "2025-01-01", zone_symbols: ["MN.1"] }],
          selected_act: "IV/30/2024",
          multiple_acts_at_point: false,
          multiple_acts_on_parcel: false,
          sampled_points: 1,
          failed_points: 1,
        })}
      />,
    );

    expect(screen.getByText("użyta w analizie")).toBeVisible();
    expect(screen.getByTestId("mpzp-discovery-act")).toHaveTextContent("plan rastrowy");
    expect(screen.getByTestId("mpzp-discovery-act")).toHaveTextContent("utracił moc 01.01.2025");
    expect(screen.getByText("Symbole stref w punktach: MN.1")).toBeVisible();
    expect(screen.getByText(/Rozpoznanie na 1 punkcie działki \(nieudane zapytania: 1\)/)).toBeVisible();
  });

  it("w panelu wyniku zastępuje ogólny komunikat informacją o wskazanych aktach", () => {
    render(<ResultPanel result={buildAnalyzeResponse({ mpzp_discovery: GORA_KALWARIA_DISCOVERY })} map={null} />);

    const section = screen.getByRole("region", { name: "Strefy MPZP" });
    expect(section).toHaveTextContent("poniżej akty wskazane przez KIMPZP");
    expect(within(section).getAllByTestId("mpzp-discovery-act")).toHaveLength(2);
  });

  it("odpowiedź sprzed AU-004 bez sekcji nie pokazuje karty", () => {
    render(<ResultPanel result={buildAnalyzeResponse()} map={null} />);

    expect(screen.queryByTestId("mpzp-discovery")).toBeNull();
    expect(screen.getByText("Nie sprawdzono albo nie znaleziono stref MPZP przecinających działkę.")).toBeVisible();
  });
});
