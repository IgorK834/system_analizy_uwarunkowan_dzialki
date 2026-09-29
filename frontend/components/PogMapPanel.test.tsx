import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PogMapPanel, type PogMapPanelProps } from "@/components/PogMapPanel";
import { buildPogRelease, buildPogZoneProperties } from "@/test/pogFixtures";

function renderPanel(overrides: Partial<PogMapPanelProps> = {}) {
  const props: PogMapPanelProps = {
    releaseState: { status: "available", release: buildPogRelease() },
    theme: "zones",
    onThemeChange: vi.fn(),
    statusFilter: "all",
    onStatusFilterChange: vi.fn(),
    ...overrides,
  };
  render(<PogMapPanel {...props} />);
  return props;
}

describe("PogMapPanel", () => {
  it("opisuje przypięte wydanie, projekty i pokazuje legendę", () => {
    renderPanel();
    expect(screen.getByText(/Wydanie pog-0123456789ab \(#42\), styl 2026\.09\.28-1/)).toBeInTheDocument();
    expect(screen.getByText(/Wydanie zawiera projekty — dane niewiążące/)).toBeInTheDocument();
    expect(screen.getByLabelText("Legenda: Strefy planistyczne")).toBeInTheDocument();
  });

  it.each([
    [{ status: "loading", release: null }, /Ładowanie lokalnego wydania/],
    [{ status: "no_release", release: null }, /Nie oznacza to braku planu ogólnego/],
    [{ status: "error", release: null }, /chwilowo niedostępna/],
  ] as const)("stan %o ma własny komunikat i blokuje kontrolki", (releaseState, message) => {
    renderPanel({ releaseState });
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Legenda:/)).not.toBeInTheDocument();
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  });

  it("przełącza tryb i filtr statusu bez innych efektów", async () => {
    const user = userEvent.setup();
    const props = renderPanel({
      releaseState: {
        status: "available",
        release: buildPogRelease({ acts_by_legal_status: { binding: 2 } }),
      },
    });
    expect(screen.queryByText(/Wydanie zawiera projekty/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ }));
    expect(props.onThemeChange).toHaveBeenCalledWith("height");
    await user.click(screen.getByRole("radio", { name: "Tylko projekty (niewiążące)" }));
    expect(props.onStatusFilterChange).toHaveBeenCalledWith("non_binding");
  });

  it("pokazuje wartości klikniętej strefy z kafla: 0 ≠ brak wartości", () => {
    renderPanel({
      selectedZone: buildPogZoneProperties({
        max_building_height_m: undefined,
        max_building_coverage_pct: 0,
        legal_status: "project",
        parameters_informational: true,
      }),
    });
    expect(screen.getByTestId("pog-selected-max_overground_floor_area_ratio")).toHaveTextContent("0,9");
    expect(screen.getByTestId("pog-selected-max_building_height_m")).toHaveTextContent(
      "brak wartości w danych",
    );
    expect(screen.getByTestId("pog-selected-max_building_coverage_pct")).toHaveTextContent("0%");
    expect(screen.getByTestId("pog-selected-min_biologically_active_pct")).toHaveTextContent("5%");
    expect(screen.getByText("SU: SU — strefa usługowa")).toBeInTheDocument();
    expect(screen.getByText("projekt (niewiążący)")).toBeInTheDocument();
    expect(screen.getByText("#42")).toBeInTheDocument();
    expect(screen.getByText(/charakter informacyjny/)).toBeInTheDocument();
  });

  it("strefa bez symbolu i spoza słownika ma jawną etykietę", () => {
    renderPanel({ selectedZone: buildPogZoneProperties({ symbol: undefined, zone_code: "unknown" }) });
    const selected = screen.getByLabelText("Wybrana strefa z mapy POG");
    expect(within(selected).getByText("strefa nierozpoznana (kod spoza słownika)")).toBeInTheDocument();
  });
});
