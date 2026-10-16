import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RoadContextCard } from "@/components/RoadContextCard";
import { ROAD_ACCESS_DISCLAIMER } from "@/lib/bdot";
import { ROAD_CASES } from "@/test/bdotFixtures";

describe("RoadContextCard", () => {
  it("pokazuje odległość do osi jezdni, relację, kategorię i klasę ze źródła oraz zdanie o dostępie prawnym", () => {
    render(<RoadContextCard road={ROAD_CASES.available} />);

    expect(screen.getByTestId("road-status")).toHaveAttribute("data-status", "available");
    expect(screen.getByTestId("road-distance")).toHaveTextContent("12 m");
    expect(screen.getByTestId("road-relation")).toHaveTextContent("oś jezdni leży poza działką");
    expect(screen.getByTestId("road-category")).toHaveTextContent("gminna");
    expect(screen.getByTestId("road-class")).toHaveTextContent("droga lokalna");
    expect(screen.getByTestId("road-radius")).toHaveTextContent("25 m (maksymalnie 500 m)");
    expect(screen.getByTestId("road-note")).toHaveTextContent(ROAD_ACCESS_DISCLAIMER);
    expect(screen.getByText(/ul\. Przykładowa/)).toBeInTheDocument();
    expect(screen.getByTestId("road-release")).toHaveTextContent("powiat 1261");
    expect(screen.getByTestId("road-release")).toHaveTextContent("22.01.2026");
    expect(screen.getByTestId("road-source")).toHaveTextContent("Źródło: BDOT10k");
    expect(screen.queryByTestId("road-status-note")).not.toBeInTheDocument();
  });

  it("odległość 0 pokazuje liczbę osi przecinających i stykających się z działką", () => {
    render(<RoadContextCard road={ROAD_CASES.intersects} />);

    expect(screen.getByTestId("road-distance")).toHaveTextContent("0 m");
    expect(screen.getByTestId("road-relation")).toHaveTextContent("przecina działkę");
    expect(screen.getByText(/przecinające: 2; stykające się: 1/)).toBeInTheDocument();
  });

  it("pusty ograniczony promień to osobny stan: nie dowodzi braku drogi", () => {
    render(<RoadContextCard road={ROAD_CASES.no_match} />);

    expect(screen.getByTestId("road-status")).toHaveTextContent("nic nie znaleziono w ograniczonym zasięgu");
    expect(screen.queryByTestId("road-distance")).not.toBeInTheDocument();
    expect(screen.getByTestId("road-radius")).toHaveTextContent("500 m");
    expect(screen.getByTestId("road-status-note")).toHaveTextContent("nie dowodzi braku drogi");
    expect(screen.getByTestId("road-note")).toHaveTextContent(ROAD_ACCESS_DISCLAIMER);
  });

  it("brak pokrycia i awaria zapytania nie pokazują żadnej odległości", () => {
    const { rerender } = render(<RoadContextCard road={ROAD_CASES.no_coverage} />);
    expect(screen.getByTestId("road-status")).toHaveTextContent("brak pokrycia");
    expect(screen.getByTestId("road-status")).toHaveTextContent("brak pokrycia danymi BDOT10k (działka poza zaimportowanymi powiatami)");
    expect(screen.getByTestId("road-status-note")).toHaveTextContent("Brak danych nie oznacza braku drogi");
    expect(screen.queryByTestId("road-distance")).not.toBeInTheDocument();

    rerender(<RoadContextCard road={ROAD_CASES.unavailable} />);
    expect(screen.getByTestId("road-status")).toHaveTextContent("źródło niedostępne (zapytanie przekroczyło limit czasu)");
    expect(screen.getByTestId("road-status-note")).toHaveTextContent("wynik nie oznacza braku drogi");
    expect(screen.queryByTestId("road-distance")).not.toBeInTheDocument();
    expect(screen.getByTestId("road-note")).toHaveTextContent(ROAD_ACCESS_DISCLAIMER);
  });

  it("wynik częściowy wskazuje, że bliższa droga poza zasięgiem nie została sprawdzona", () => {
    render(<RoadContextCard road={ROAD_CASES.partial_coverage} />);

    expect(screen.getByTestId("road-distance")).toBeInTheDocument();
    expect(screen.getByTestId("road-partial-coverage")).toHaveTextContent("nie została sprawdzona");
  });

  it("odpowiedź sprzed sekcji jest nieznana, a nie 'brak drogi'", () => {
    render(<RoadContextCard road={undefined} />);

    expect(screen.getByTestId("road-status")).toHaveAttribute("data-status", "unknown");
    expect(screen.getByTestId("road-status-note")).toHaveTextContent("Brak informacji nie oznacza braku drogi");
    expect(screen.getByTestId("road-note")).toHaveTextContent("nie jest potwierdzeniem prawnego dostępu");
    expect(screen.getByTestId("road-source")).toHaveTextContent("Brak metadanych źródła BDOT10k");
  });

  it("interfejs nie zawiera pola prawnego dostępu do drogi publicznej", () => {
    const { container } = render(<RoadContextCard road={ROAD_CASES.available} />);

    expect(container.innerHTML).not.toMatch(/legal_public_road_access|legal_access/);
    expect(container.textContent).not.toMatch(/dostęp do drogi publicznej:\s*tak/i);
  });
});
