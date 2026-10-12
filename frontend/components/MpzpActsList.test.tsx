import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MpzpActsList } from "@/components/MpzpActsList";
import { ResultPanel } from "@/components/ResultPanel";
import { buildAnalyzeResponse } from "@/test/fixtures";
import { MPZP_ACTS_CASES } from "@/test/mpzpActsFixtures";

describe("MpzpActsList (AU-101)", () => {
  it("wymienia oba akty Góry Kalwarii bez wyboru, z udziałem 'nie ustalono' i uwagą o weryfikacji", () => {
    const { mpzp_acts, mpzp_status } = MPZP_ACTS_CASES.ambiguous;
    render(<MpzpActsList acts={mpzp_acts} rejected={mpzp_status.rejected_acts} />);

    const acts = screen.getAllByTestId("mpzp-act");
    expect(acts.map((act) => act.getAttribute("data-act"))).toEqual(["IV/30/2024", "576/XLVII/2010"]);
    expect(acts.every((act) => act.getAttribute("data-precedence") === "unresolved")).toBe(true);
    expect(within(acts[0]).getByTestId("mpzp-act-share")).toHaveTextContent("nie ustalono (akt bez granicy w źródle)");
    expect(within(acts[0]).getByTestId("mpzp-act-review")).toBeVisible();
    expect(within(acts[0]).getByTestId("mpzp-act-sources")).toHaveTextContent("Źródła: KIMPZP; kilka aktów bez rozstrzygnięcia");
    expect(within(acts[0]).getByText("http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf")).toBeVisible();
    expect(within(acts[0]).queryByRole("link", { name: "Tekst uchwały" })).toBeNull(); // HTTP — bez href
    expect(screen.getByTestId("mpzp-acts-note")).toHaveTextContent("a nie strefa przeznaczenia");
  });

  it("akt cz. II zwrócony przez RU jest jawnie opisany jako odrzucony z odległością", () => {
    const { mpzp_acts, mpzp_status } = MPZP_ACTS_CASES.ambiguous;
    render(<MpzpActsList acts={mpzp_acts} rejected={mpzp_status.rejected_acts} />);

    const rejected = screen.getByTestId("mpzp-rejected-acts");
    const items = within(rejected).getAllByTestId("mpzp-rejected-act");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveAttribute("data-reason", "no_overlap");
    expect(items[0]).toHaveTextContent("cz. II");
    expect(items[0]).toHaveTextContent("granica aktu leży poza działką");
    expect(items[0]).toHaveTextContent("odległość od działki ok. 6 m");
    expect(items[0]).toHaveTextContent("projekt lub jeszcze nieobowiązujący");
  });

  it("akt z RU: udział, dokument jako link HTTPS, legenda, rysunek i zmiana planu", () => {
    const { mpzp_acts } = MPZP_ACTS_CASES.available;
    render(<MpzpActsList acts={mpzp_acts} />);

    const act = screen.getByTestId("mpzp-act");
    expect(act).toHaveAttribute("data-legal-status", "binding");
    expect(act).toHaveTextContent("Uchwała LIII/749/1998");
    expect(act).toHaveTextContent("obowiązuje od 01.04.1998");
    expect(within(act).getByTestId("mpzp-act-share")).toHaveTextContent("11,84% (167,44 m²)");
    expect(within(act).getByTestId("mpzp-act-sources")).toHaveTextContent("Źródła: Rejestr Urbanistyczny");
    expect(within(act).getByRole("link", { name: "Tekst uchwały" })).toHaveAttribute(
      "href",
      "https://brm.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf",
    );
    expect(within(act).getByRole("link", { name: "Legenda" })).toBeVisible();
    expect(within(act).getByRole("link", { name: "Rysunek planu" })).toBeVisible();
    expect(within(act).queryByTestId("mpzp-act-review")).toBeNull();
    const amendment = within(act).getByTestId("mpzp-act-amendment");
    expect(amendment).toHaveAttribute("data-amendment", "XLIV/1038/2022");
    expect(within(amendment).getByRole("link", { name: "XLIV/1038/2022" })).toHaveAttribute(
      "href",
      "https://brm.bielsko-biala.pl/wp-content/uploads/2022/05/243_Uchwala.pdf",
    );
  });

  it("projekt planu nie pokazuje 'obowiązuje od' i nie udaje numeru uchwały planu", () => {
    const [act] = MPZP_ACTS_CASES.available.mpzp_acts;
    render(
      <MpzpActsList
        acts={[
          {
            ...act,
            resolution_number: null,
            legal_status: "not_yet_binding",
            raw_status: "w opracowaniu",
            document_kind: "commencement",
            precedence: "not_in_force",
            amendments: [],
          },
        ]}
      />,
    );

    const item = screen.getByTestId("mpzp-act");
    expect(item).toHaveTextContent("Projekt planu (bez uchwały uchwalającej)");
    expect(item).toHaveTextContent("projekt lub jeszcze nieobowiązujący (w opracowaniu)");
    expect(item).not.toHaveTextContent("obowiązuje od");
    expect(within(item).getByRole("link", { name: "Uchwała o przystąpieniu (nie tekst planu)" })).toBeVisible();
  });

  it("nic nie renderuje bez aktów i bez odrzuconych", () => {
    const { container } = render(<MpzpActsList acts={[]} rejected={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("sam odrzucony akt jest pokazany bez listy aktów", () => {
    const { mpzp_status } = MPZP_ACTS_CASES.ambiguous;
    render(<MpzpActsList acts={[]} rejected={mpzp_status.rejected_acts} />);
    expect(screen.queryByTestId("mpzp-acts-note")).toBeNull();
    expect(screen.getByTestId("mpzp-rejected-acts")).toBeInTheDocument();
  });
});

describe("ResultPanel: stan i akty MPZP (AU-101, AU-103)", () => {
  it.each(Object.entries(MPZP_ACTS_CASES))("stan %s jest widoczny w sekcji MPZP i nie zlewa się z innymi", (key, value) => {
    render(<ResultPanel result={buildAnalyzeResponse(value)} map={null} />);

    const section = screen.getByRole("region", { name: "Strefy MPZP" });
    expect(within(section).getByTestId("mpzp-status")).toHaveAttribute("data-status", key);
    expect(within(section).getByTestId("mpzp-status-message")).toHaveTextContent(value.mpzp_status.message);
    expect(within(section).getByTestId("mpzp-zones-empty")).toHaveTextContent("Nie ustalono stref MPZP przecinających działkę.");
    expect(section).not.toHaveTextContent("Nie sprawdzono albo nie znaleziono");
    expect(within(section).queryAllByTestId("mpzp-act")).toHaveLength(value.mpzp_acts.length);
  });

  it("zapis sprzed AU-103: osobny komunikat i brak akapitu 'nie sprawdzono albo nie znaleziono'", () => {
    render(<ResultPanel result={buildAnalyzeResponse()} map={null} />);

    const section = screen.getByRole("region", { name: "Strefy MPZP" });
    expect(within(section).getByTestId("mpzp-status")).toHaveAttribute("data-status", "missing");
    expect(section).toHaveTextContent("przed wprowadzeniem stanów MPZP");
  });

  it("warstwa aktów na mapie jest przełącznikiem i dostaje granice z odpowiedzi", () => {
    const sources: string[] = [];
    const map = {
      getSource: () => undefined,
      addSource: (id: string) => sources.push(id),
      removeSource: () => undefined,
      getLayer: () => undefined,
      addLayer: () => undefined,
      removeLayer: () => undefined,
      setLayoutProperty: () => undefined,
      fitBounds: () => undefined,
    };
    render(<ResultPanel result={buildAnalyzeResponse(MPZP_ACTS_CASES.available)} map={map as never} />);

    expect(sources).toContain("mpzp-act-source");
    expect(sources).toContain("mpzp-act-boundary-source");
    expect(screen.getByRole("switch", { name: /Granice aktów MPZP/ })).toBeEnabled();
  });

  it("bez granicy aktu przełącznik jest wyłączony z wyjaśnieniem", () => {
    render(<ResultPanel result={buildAnalyzeResponse(MPZP_ACTS_CASES.ambiguous)} map={null} />);
    expect(screen.getByRole("switch", { name: /Granice aktów MPZP/ })).toBeDisabled();
  });
});
