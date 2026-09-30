import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SectionQualityMatrix } from "@/components/SectionQualityMatrix";
import { buildQualityMatrix } from "@/test/fixtures";

const NOW = new Date("2026-09-21T10:00:00Z");

describe("SectionQualityMatrix", () => {
  it("pokazuje każdą sekcję — także pustą — ze statusem, źródłem albo powodem braku", () => {
    render(<SectionQualityMatrix matrix={buildQualityMatrix()} now={NOW} />);

    const list = screen.getByTestId("quality-matrix");
    expect(within(list).getAllByRole("listitem")).toHaveLength(10); // każda sekcja, także pusta

    const parcel = screen.getByTestId("quality-row-parcel");
    expect(within(parcel).getByText("sprawdzono")).toBeVisible();
    expect(within(parcel).getByTestId("quality-source")).toHaveTextContent("ULDK (uldk)");
    expect(parcel).toHaveTextContent("2026");

    const transport = screen.getByTestId("quality-row-transport");
    expect(within(transport).getByText("poza zakresem")).toBeVisible();
    expect(within(transport).getByTestId("quality-source")).toHaveTextContent("brak źródła");
    expect(within(transport).getByTestId("quality-reasons")).toHaveTextContent(
      "brak potwierdzonego kontraktu źródła danych (BK-305)",
    );
    expect(transport).toHaveTextContent("brak danych"); // czas pobrania nieznany

    const relation = screen.getByTestId("quality-row-mpzp_pog_relation");
    expect(within(relation).getByTestId("quality-reasons")).toHaveTextContent(/nie wykonano oceny/);
  });

  it("odróżnia brak pokrycia, niedostępność i stary pomiar oraz pokazuje flagę weryfikacji", () => {
    render(<SectionQualityMatrix matrix={buildQualityMatrix()} now={NOW} />);

    const terrain = screen.getByTestId("quality-row-terrain");
    expect(within(terrain).getByText("brak pokrycia źródła")).toBeVisible();
    expect(terrain.querySelector("[data-status='no_coverage']")).not.toBeNull();
    expect(terrain.querySelector("[data-freshness='stale']")).toHaveTextContent("starsze niż reguła");
    expect(terrain).toHaveTextContent("wiek: 50 dni; reguła źródła: 7 dni");

    const nature = screen.getByTestId("quality-row-nature");
    expect(within(nature).getByText("źródło niedostępne")).toBeVisible();
    expect(within(nature).getByTestId("quality-manual")).toHaveTextContent("wymaga weryfikacji");
    expect(nature).toHaveTextContent("przekroczono limit czasu usługi");
    expect(nature.querySelector("[data-freshness='fresh']")).toHaveTextContent("aktualne wg reguły");

    const parcel = screen.getByTestId("quality-row-parcel");
    expect(parcel.querySelector("[data-freshness='unknown']")).toHaveTextContent("świeżość nieustalona");
    expect(parcel).toHaveTextContent("brak reguły wieku");
    expect(within(parcel).queryByTestId("quality-manual")).toBeNull();

    const mpzp = screen.getByTestId("quality-row-mpzp");
    expect(within(mpzp).getByText("częściowo")).toBeVisible();
    expect(within(mpzp).getByTestId("quality-manual")).toBeVisible();
    expect(mpzp).toHaveTextContent("#7");
    expect(screen.getByTestId("quality-row-pog")).toHaveTextContent("2026-09-19");
  });

  it("pokazuje kod nieznany dosłownie i myślnik przy pełnym wyniku bez powodów", () => {
    const matrix = buildQualityMatrix();
    matrix.sections[0] = { ...matrix.sections[0], reason_codes: ["KOD_SPOZA_LEGENDY"] };
    render(<SectionQualityMatrix matrix={matrix} now={NOW} />);

    expect(screen.getByTestId("quality-row-parcel")).toHaveTextContent("kod: KOD_SPOZA_LEGENDY");
    expect(screen.getByTestId("quality-row-pog")).toHaveTextContent("—");
  });

  it("ostrzega o wieku na dziś osobno i podaje, że nie zmienia oceny historycznej", () => {
    const { rerender } = render(<SectionQualityMatrix matrix={buildQualityMatrix()} now={NOW} />);
    // Dzień po analizie ostrzega tylko sekcja, której pomiar był stary już wtedy.
    const early = screen.getByTestId("quality-age-warning");
    expect(early).toHaveTextContent("Teren (NMT)");
    expect(early).not.toHaveTextContent("Zagrożenie powodziowe (ISOK)");

    rerender(
      <SectionQualityMatrix matrix={buildQualityMatrix()} now={new Date("2027-01-01T00:00:00Z")} />,
    );
    const warning = screen.getByTestId("quality-age-warning");
    expect(warning).toHaveTextContent("Zagrożenie powodziowe (ISOK)");
    expect(warning).toHaveTextContent("Teren (NMT)");
    expect(warning).toHaveTextContent(/nie zmienia oceny historycznej/);
    // Zapisana ocena w tabeli pozostaje ta sama.
    expect(screen.getByTestId("quality-row-flood").querySelector("[data-freshness='fresh']")).not.toBeNull();
  });

  it("używa chwili otwarcia jako domyślnego punktu odniesienia ostrzeżenia", () => {
    render(<SectionQualityMatrix matrix={buildQualityMatrix()} />);
    // Fixture z 2026-09 jest starsze o więcej niż 7 dni od dowolnej daty po 2026-10.
    const warning = screen.queryByTestId("quality-age-warning");
    const now = Date.now();
    const stale = now - new Date("2026-09-20T09:29:00Z").getTime() > 7 * 86_400_000;
    expect(Boolean(warning)).toBe(stale);
  });

  it("zawiera legendę z API, sumę kontrolną i objaśnienie zakresu", () => {
    render(<SectionQualityMatrix matrix={buildQualityMatrix()} now={NOW} />);

    const legend = screen.getByTestId("quality-legend");
    expect(within(legend).getByText("Legenda statusów i świeżości")).toBeVisible();
    expect(legend).toHaveTextContent("to nie jest błąd źródła");
    expect(legend).toHaveTextContent("starsze niż reguła");
    expect(screen.getByTestId("quality-hash")).toHaveTextContent("a".repeat(64));
    expect(screen.getByTestId("quality-scope-note")).toHaveTextContent(
      /żaden zbiorczy status nie gwarantuje kompletności/,
    );
    expect(screen.getByTestId("quality-scope-note")).toHaveTextContent("quality-policy/1+abc123def456");
    expect(screen.queryByTestId("quality-reconstructed")).toBeNull();
  });

  it("jawnie oznacza ocenę odtworzoną ze starego zapisu", () => {
    render(
      <SectionQualityMatrix matrix={buildQualityMatrix({ origin: "reconstructed" })} now={NOW} />,
    );

    expect(screen.getByTestId("quality-reconstructed")).toHaveTextContent(
      /nie odzwierciedla oceny z chwili analizy/,
    );
  });

  it("nie udaje kompletności, gdy odpowiedź nie ma macierzy", () => {
    const { rerender } = render(<SectionQualityMatrix matrix={null} />);
    expect(screen.getByTestId("quality-missing")).toHaveTextContent(
      /nie oznacza, że dane są aktualne ani kompletne/,
    );
    expect(screen.queryByTestId("quality-matrix")).toBeNull();

    rerender(<SectionQualityMatrix matrix={undefined} />);
    expect(screen.getByTestId("quality-missing")).toBeVisible();
    rerender(<SectionQualityMatrix matrix={buildQualityMatrix({ sections: [] })} />);
    expect(screen.getByTestId("quality-missing")).toBeVisible();
  });
});
