import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BuildingsCard } from "@/components/BuildingsCard";
import { BUILDINGS_CASES } from "@/test/bdotFixtures";

describe("BuildingsCard", () => {
  it("pokazuje budynki na działce, pokrycie i trzy bufory z licznościami", () => {
    render(<BuildingsCard buildings={BUILDINGS_CASES.available} />);

    expect(screen.getByTestId("buildings-status")).toHaveAttribute("data-status", "available");
    expect(screen.getByTestId("buildings-on-parcel")).toHaveTextContent("2");
    expect(screen.getByTestId("buildings-coverage")).toHaveTextContent("8,1%");
    const table = screen.getByTestId("buildings-buffers");
    expect(within(table).getAllByRole("row")).toHaveLength(4); // nagłówek + 50/100/200 m
    expect(screen.getByTestId("buildings-count-50")).toHaveTextContent("2");
    expect(screen.getByTestId("buildings-count-100")).toHaveTextContent("3");
    expect(screen.getByTestId("buildings-count-200")).toHaveTextContent("3");
    expect(screen.getByTestId("buildings-note")).toHaveTextContent("nie ocena możliwości zabudowy");
    expect(screen.getByText(/buffer\(parcel, r\) - parcel/)).toBeInTheDocument();
    expect(screen.getByTestId("buildings-release")).toHaveTextContent("powiat 1261");
  });

  it("tabela najbliższych budynków podaje odległość, położenie, funkcję i kondygnacje", () => {
    render(<BuildingsCard buildings={BUILDINGS_CASES.available} />);

    const list = screen.getByTestId("buildings-list");
    expect(within(list).getByText("zachodzi na działkę")).toBeInTheDocument();
    expect(within(list).getAllByText("budynki mieszkalne").length).toBeGreaterThan(0);
    expect(within(list).getByText("sąsiedni")).toBeInTheDocument();
    expect(screen.queryByTestId("buildings-truncated")).not.toBeInTheDocument();
  });

  it("zero budynków to sprawdzone 0, a nieokreślone wielkości są pokazane jako brak wartości", () => {
    render(<BuildingsCard buildings={BUILDINGS_CASES.empty} />);

    expect(screen.getByTestId("buildings-on-parcel")).toHaveTextContent("0");
    expect(screen.getByTestId("buildings-count-50")).toHaveTextContent("0");
    const row = within(screen.getByTestId("buildings-buffers")).getAllByRole("row")[1];
    expect(row).toHaveTextContent("—"); // min. odległość i rozmiary: null ≠ 0
    expect(screen.queryByTestId("buildings-list")).not.toBeInTheDocument();
    expect(screen.queryByTestId("buildings-status-note")).not.toBeInTheDocument();
  });

  it("długa lista jest skrócona w tabeli z informacją o pełnej liście w pakiecie audytowym", () => {
    render(<BuildingsCard buildings={BUILDINGS_CASES.crowded} />);

    const rows = within(screen.getByTestId("buildings-list")).getAllByRole("row");
    expect(rows).toHaveLength(26); // nagłówek + 25 wierszy
    expect(screen.getByTestId("buildings-truncated")).toHaveTextContent("Pokazano 25 z 30");
  });

  it("brak pokrycia i awaria nie pokazują żadnych liczb ani nie sugerują braku budynków", () => {
    const { rerender } = render(<BuildingsCard buildings={BUILDINGS_CASES.no_coverage} />);
    expect(screen.getByTestId("buildings-status")).toHaveTextContent("brak pokrycia");
    expect(screen.getByTestId("buildings-status-note")).toHaveTextContent("Brak danych nie oznacza braku budynków");
    expect(screen.queryByTestId("buildings-on-parcel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("buildings-buffers")).not.toBeInTheDocument();

    rerender(<BuildingsCard buildings={BUILDINGS_CASES.unavailable} />);
    expect(screen.getByTestId("buildings-status")).toHaveTextContent("źródło niedostępne");
    expect(screen.getByTestId("buildings-status-note")).toHaveTextContent("wynik nie oznacza braku budynków");
    expect(screen.queryByTestId("buildings-buffers")).not.toBeInTheDocument();
  });

  it("bufor wykraczający poza zasięg powiatu jest oznaczony jako wynik częściowy", () => {
    render(<BuildingsCard buildings={BUILDINGS_CASES.partial_coverage} />);

    expect(screen.getByTestId("buildings-buffers")).toBeInTheDocument();
    expect(screen.getByTestId("buildings-partial-coverage")).toHaveTextContent("mogą być zaniżone");
  });

  it("odpowiedź sprzed sekcji jest nieznana", () => {
    render(<BuildingsCard buildings={undefined} />);

    expect(screen.getByTestId("buildings-status")).toHaveAttribute("data-status", "unknown");
    expect(screen.getByTestId("buildings-status-note")).toHaveTextContent("Brak informacji nie oznacza braku budynków");
  });

  it("nie pokazuje nazw, właścicieli ani identyfikatora EGiB", () => {
    const { container } = render(<BuildingsCard buildings={BUILDINGS_CASES.available} />);

    expect(container.textContent).not.toMatch(/identyfikatorEGiB|nazwa budynku|właściciel:/i);
  });
});
