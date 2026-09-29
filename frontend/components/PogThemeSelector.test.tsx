import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { PogThemeSelector } from "@/components/PogThemeSelector";
import { POG_THEMES, type PogThemeId } from "@/lib/pogThemes";

function Harness({ onChange }: { onChange: (theme: PogThemeId) => void }) {
  const [value, setValue] = useState<PogThemeId>("zones");
  return (
    <PogThemeSelector
      value={value}
      onChange={(theme) => {
        setValue(theme);
        onChange(theme);
      }}
    />
  );
}

describe("PogThemeSelector", () => {
  it("pokazuje pięć trybów z jednostką i kierunkiem skali", () => {
    render(<PogThemeSelector value="height" onChange={vi.fn()} />);
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(5);
    expect(radios.map((radio) => (radio as HTMLInputElement).value)).toEqual(
      POG_THEMES.map((theme) => theme.id),
    );
    expect(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ })).toBeChecked();
    expect(screen.getByText("(m)")).toBeInTheDocument();
    expect(screen.getByText("(bezwymiarowa)")).toBeInTheDocument();
    expect(screen.getAllByText("(%)")).toHaveLength(2);
    expect(screen.getByText(/Jednostka: metry \(m\)\. Im ciemniejszy kolor/)).toBeInTheDocument();
    expect(screen.getByText("↑")).toBeInTheDocument();
  });

  it("obsługuje klawiaturę: Tab i strzałki przechodzą przez wszystkie tryby", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);

    await user.tab();
    expect(screen.getByRole("radio", { name: /Strefy planistyczne/ })).toHaveFocus();
    for (let index = 0; index < 4; index += 1) await user.keyboard("{ArrowDown}");

    expect(onChange.mock.calls.map(([theme]) => theme)).toEqual([
      "intensity",
      "building_coverage",
      "height",
      "biologically_active",
    ]);
    expect(
      screen.getByRole("radio", { name: /Minimalny udział powierzchni biologicznie czynnej/ }),
    ).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(onChange).toHaveBeenLastCalledWith("zones");
    expect(screen.queryByText("↑")).not.toBeInTheDocument();
  });

  it("jest nieaktywny, gdy warstwa POG jest niedostępna", () => {
    render(<PogThemeSelector value="zones" onChange={vi.fn()} disabled />);
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  });
});
