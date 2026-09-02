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
});
