import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompletenessBar } from "@/components/CompletenessBar";
import { COMPLETENESS_CASES } from "@/test/completenessFixtures";
import { contrastRatio, resolveColor, textContrast } from "@/test/contrast";

describe("CompletenessBar (AU-104)", () => {
  it.each(Object.entries(COMPLETENESS_CASES))(
    "przypadek %s pokazuje komunikat z backendu, liczby i po jednym segmencie na sekcję w zakresie",
    (_name, value) => {
      render(<CompletenessBar completeness={value} status="partial" />);

      expect(screen.getByTestId("completeness-headline")).toHaveTextContent(value.headline);
      const progress = screen.getByRole("progressbar", { name: "Kompletność analizy" });
      expect(progress).toHaveAttribute("aria-valuemin", "0");
      expect(progress).toHaveAttribute("aria-valuemax", String(value.total));
      expect(progress).toHaveAttribute("aria-valuenow", String(value.checked));
      expect(progress.getAttribute("aria-valuetext")).toContain(String(value.total));
      expect(progress.querySelectorAll(".completeness-segment")).toHaveLength(value.total);
      expect(progress.querySelectorAll('[data-state="checked"]')).toHaveLength(value.checked);
      expect(screen.getByTestId("completeness")).toHaveAttribute("data-complete", value.complete ? "true" : "false");
      expect(screen.getByTestId("completeness-counter")).toHaveTextContent(`${value.checked} z ${value.total} sekcji sprawdzono`);
    },
  );

  it("zastępuje plakietkę statusu: przy kompletności nie ma sztywnego `partial`", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.partial_pog} status="partial" />);

    expect(screen.queryByText("partial")).toBeNull();
    expect(screen.getByTestId("completeness-counter")).toHaveTextContent("brak:");
    expect(screen.getByTestId("completeness-counter")).toHaveTextContent("strefy POG");
  });

  it("kompletna analiza z aktu i dokumentu mówi, że nie ma lokalnego wektora (jakość, nie warunek)", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.complete_document} status="complete" />);

    expect(screen.getByTestId("completeness")).toHaveAttribute("data-complete", "true");
    const notes = screen.getAllByTestId("completeness-note").map((node) => node.textContent ?? "");
    expect(notes.some((text) => text.includes("document_candidate") && text.includes("bez lokalnego wektora"))).toBe(true);
    expect(within(screen.getByTestId("completeness-list")).getByText(/akt i dokument, bez lokalnego wektora/)).toBeInTheDocument();
  });

  it("MPZP z lokalnego wektora ma własną podstawę i nie ma zastrzeżenia o braku wektora", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.complete_vector} status="complete" />);

    expect(within(screen.getByTestId("completeness-list")).getByText(/przecięcie z lokalnym wektorem stref/)).toBeInTheDocument();
    expect(screen.queryByText(/bez lokalnego wektora stref/)).toBeNull();
  });

  it("`no_match` jest sprawdzeniem, ale komunikat nie twierdzi, że planu nie ma", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.complete_no_match} status="complete" />);

    const notes = screen.getAllByTestId("completeness-note").map((node) => node.textContent ?? "");
    expect(notes.some((text) => text.includes("nie jest dowód braku planu"))).toBe(true);
    expect(within(screen.getByTestId("completeness-list")).getByText(/kanały odpowiedziały i nie wskazały planu/)).toBeInTheDocument();
  });

  it("sekcje poza zakresem nie mają segmentu, ale są na liście i w liczniku", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.complete_vector} status="complete" />);

    const list = screen.getByTestId("completeness-list");
    expect(list.querySelector('[data-section="utilities"]')).toHaveAttribute("data-state", "out_of_scope");
    expect(list.querySelector('[data-section="transport"]')).toHaveAttribute("data-state", "out_of_scope");
    expect(screen.getByRole("progressbar").querySelector('[data-section="transport"]')).toBeNull();
    expect(screen.getByTestId("completeness-counter")).toHaveTextContent("poza zakresem: uzbrojenie terenu, droga i dostęp");
  });

  it("analiza wstrzymana pokazuje brak MPZP, a nie sukces", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.waiting} status="waiting_for_user_input" />);

    expect(screen.getByTestId("completeness")).toHaveAttribute("data-complete", "false");
    expect(screen.getByTestId("completeness-list").querySelector('[data-section="mpzp"]')).toHaveAttribute("data-state", "missing");
  });

  it("zapis sprzed AU-104 wraca do plakietki statusu z jawnym wyjaśnieniem", () => {
    render(<CompletenessBar completeness={undefined} status="partial" />);

    expect(screen.getByText("partial")).toBeVisible();
    expect(screen.getByTestId("completeness-missing")).toHaveTextContent("nie wskazuje, których sekcji brakuje");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("kompletność odtworzona przy odczycie jest oznaczona pochodzeniem", () => {
    render(
      <CompletenessBar completeness={{ ...COMPLETENESS_CASES.complete_vector, origin: "reconstructed" }} status="complete" />,
    );

    expect(screen.getByTestId("completeness")).toHaveAttribute("data-origin", "reconstructed");
  });

  it("status źródła i znacznik weryfikacji są pokazane obok stanu sekcji", () => {
    render(<CompletenessBar completeness={COMPLETENESS_CASES.complete_document} status="complete" />);

    const mpzp = screen.getByTestId("completeness-list").querySelector('[data-section="mpzp"]') as HTMLElement;
    expect(mpzp).toHaveTextContent("status źródła:");
    expect(mpzp).toHaveTextContent("wymaga weryfikacji");
  });
});

describe("CompletenessBar — dostępność (kontrast WCAG 2.x z arkusza stylów)", () => {
  it("tekst licznika i źródła ma kontrast co najmniej 4,5:1", () => {
    expect(textContrast(".completeness-counter")).toBeGreaterThanOrEqual(4.5);
    expect(textContrast(".completeness-source")).toBeGreaterThanOrEqual(4.5);
  });

  it("obrys segmentów ma kontrast co najmniej 3:1 względem tła (elementy graficzne)", () => {
    const background = "#ffffff";
    expect(contrastRatio(resolveColor("var(--accent-dark)"), background)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio("#7a4300", background)).toBeGreaterThanOrEqual(3);
  });
});
