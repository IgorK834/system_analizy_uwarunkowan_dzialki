import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LayerToggle, type LayerToggleItem } from "@/components/LayerToggle";

const baseItems: LayerToggleItem[] = [
  { id: "parcel", label: "Obrys działki", color: "#176c4b", checked: true },
  {
    id: "buildable_area",
    label: "Obszar zabudowy",
    color: "#c96a1f",
    checked: false,
  },
];

describe("LayerToggle", () => {
  it("renderuje legendę i etykietę przełącznika", () => {
    render(<LayerToggle items={baseItems} onChange={vi.fn()} legendLabel="Warstwy" />);

    expect(screen.getByText("Warstwy")).toBeVisible();
    expect(screen.getByRole("switch", { name: /Obrys działki/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: /Obszar zabudowy/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("wywołuje onChange z przełączoną wartością po kliknięciu", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<LayerToggle items={baseItems} onChange={onChange} />);

    await user.click(screen.getByRole("switch", { name: /Obrys działki/ }));
    expect(onChange).toHaveBeenCalledWith("parcel", false);

    await user.click(screen.getByRole("switch", { name: /Obszar zabudowy/ }));
    expect(onChange).toHaveBeenCalledWith("buildable_area", true);
  });

  it("wyłącza przełącznik i pokazuje powód, gdy warstwa jest niedostępna", () => {
    const items: LayerToggleItem[] = [
      {
        id: "pog_wms",
        label: "POG",
        color: "#7a3fae",
        checked: false,
        disabled: true,
        disabledReason: "Nakładka POG niedostępna dla tej gminy.",
      },
    ];
    render(<LayerToggle items={items} onChange={vi.fn()} />);

    const toggle = screen.getByRole("switch", { name: "POG" });
    expect(toggle).toBeDisabled();
    expect(
      screen.getByText("Nakładka POG niedostępna dla tej gminy."),
    ).toBeVisible();
  });

  it("pokazuje jawny status warstwy pod przełącznikiem", () => {
    render(
      <LayerToggle
        items={[
          {
            ...baseItems[0],
            status: "Podgląd krajowy. Analiza parametrów: dostępna.",
          },
        ]}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/Analiza parametrów: dostępna/)).toBeVisible();
  });

  it("BK-406: pokazuje stan warstwy tekstem, osobno od statusu i przełącznika", () => {
    const states = ["loading", "available", "partial", "no_coverage", "error", "stale"] as const;
    render(
      <LayerToggle
        items={states.map((state) => ({
          id: state,
          label: `Warstwa ${state}`,
          color: "#176c4b",
          checked: true,
          state,
          status: "Status aktu: projekt (niewiążący).",
        }))}
        onChange={vi.fn()}
      />,
    );
    const chips = screen.getAllByText((_, element) => element?.hasAttribute("data-layer-state") ?? false);
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "ładowanie",
      "dostępna",
      "dane niepełne",
      "brak pokrycia danymi",
      "awaria warstwy",
      "dane nieaktualne",
    ]);
    // Stan nie wchodzi do nazwy przełącznika, a status prawny pozostaje osobnym tekstem.
    expect(screen.getByRole("switch", { name: "Warstwa partial" })).toBeInTheDocument();
    expect(screen.getAllByText("Status aktu: projekt (niewiążący).")).toHaveLength(6);
    for (const chip of chips) expect(chip.textContent).not.toMatch(/brak planu/i);
  });

  it("bez stanu nie renderuje znacznika stanu", () => {
    render(<LayerToggle items={baseItems} onChange={vi.fn()} />);
    expect(screen.queryByText(/Stan warstwy/)).not.toBeInTheDocument();
  });
});
