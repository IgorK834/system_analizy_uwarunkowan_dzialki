import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PogMapPanel, type PogMapPanelProps } from "@/components/PogMapPanel";
import {
  INITIAL_TILE_ACTIVITY,
  type PogTileActivity,
  derivePogLayerStatus,
} from "@/lib/pogLayerState";
import type { PogReleaseState } from "@/lib/types";
import { buildPogRelease } from "@/test/pogFixtures";

const LOADED: PogTileActivity = { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, anyTileLoaded: true };
const VIEW: [number, number, number, number] = [18.52, 54.42, 18.58, 54.46];

function status(release: PogReleaseState, tiles: PogTileActivity = LOADED) {
  return derivePogLayerStatus({ release, tiles, viewport: VIEW });
}

function available(overrides = {}): PogReleaseState {
  return { status: "available", release: buildPogRelease(overrides), checkedAt: "2026-09-28T10:00:00Z" };
}

function renderPanel(overrides: Partial<PogMapPanelProps> = {}) {
  const props: PogMapPanelProps = {
    layerStatus: status(available()),
    theme: "zones",
    onThemeChange: vi.fn(),
    statusFilter: "all",
    onStatusFilterChange: vi.fn(),
    onRetry: vi.fn(),
    ...overrides,
  };
  render(<PogMapPanel {...props} />);
  return props;
}

describe("PogMapPanel", () => {
  it("opisuje przypięte wydanie z datą, stan warstwy, plakietkę projektu i legendę", () => {
    renderPanel();
    expect(
      screen.getByText(/Wydanie pog-0123456789ab \(#42\) z dnia 28\.09\.2026, styl 2026\.09\.29-1/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("pog-layer-state")).toHaveTextContent("dostępna");
    expect(screen.getByTestId("pog-status-badge")).toHaveTextContent("projekt / dane niewiążące");
    expect(screen.getByLabelText("Legenda: Strefy planistyczne")).toBeInTheDocument();
    // Brak błędów → brak przycisku ponowienia.
    expect(screen.queryByRole("button", { name: /Ponów/ })).not.toBeInTheDocument();
  });

  it.each([
    [{ status: "loading", release: null }, /Ładowanie lokalnego wydania/, "ładowanie"],
    [{ status: "no_release", release: null }, /Nie oznacza to braku planu ogólnego/, "brak pokrycia danymi"],
    [{ status: "error", release: null }, /chwilowo niedostępna/, "awaria warstwy"],
  ] as const)("stan %o ma własny komunikat i blokuje kontrolki", (releaseState, message, chip) => {
    renderPanel({ layerStatus: status(releaseState) });
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByTestId("pog-layer-state")).toHaveTextContent(chip);
    expect(screen.queryByLabelText(/Legenda:/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("pog-status-badge")).not.toBeInTheDocument();
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  });

  it("przełącza tryb i jawną edycję danych klawiaturą, bez innych efektów", async () => {
    const user = userEvent.setup();
    const props = renderPanel({
      layerStatus: status(available({ acts_by_legal_status: { binding: 2 } })),
    });
    expect(screen.queryByTestId("pog-status-badge")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ }));
    expect(props.onThemeChange).toHaveBeenCalledWith("height");
    const edition = screen.getByRole("group", { name: "Edycja danych (status prawny aktu)" });
    await user.click(within(edition).getByRole("radio", { name: "Wszystkie akty" }));
    await user.keyboard("{ArrowDown}");
    expect(props.onStatusFilterChange).toHaveBeenCalledWith("binding");
    await user.click(within(edition).getByRole("radio", { name: "Tylko projekty (niewiążące)" }));
    expect(props.onStatusFilterChange).toHaveBeenCalledWith("non_binding");
  });

  it("awaria kafla: partial z ponowieniem, plakietka projektu nadal widoczna", async () => {
    const user = userEvent.setup();
    const props = renderPanel({ layerStatus: status(available(), { ...LOADED, tileErrors: 2 }) });
    expect(screen.getByTestId("pog-layer-state")).toHaveTextContent("dane niepełne");
    expect(screen.getByText(/Część kafli POG nie została wczytana \(2\)/)).toBeInTheDocument();
    expect(screen.getByTestId("pog-status-badge")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Ponów wczytanie warstwy" }));
    expect(props.onRetry).toHaveBeenCalledOnce();
  });

  it("stale zachowuje wydanie, datę i plakietkę; ponawianie nie usuwa danych", () => {
    const release = buildPogRelease();
    renderPanel({
      layerStatus: status({
        status: "stale",
        release,
        checkedAt: "2026-09-28T10:15:00Z",
        reason: "refresh_failed",
      }),
      retrying: true,
    });
    expect(screen.getByTestId("pog-layer-state")).toHaveTextContent("dane nieaktualne");
    expect(screen.getByTestId("pog-release-line")).toHaveTextContent(
      /#42\) z dnia 28\.09\.2026.*ostatnio potwierdzone 28\.09\.2026, 12:15/,
    );
    expect(screen.getByTestId("pog-status-badge")).toHaveTextContent("projekt / dane niewiążące");
    expect(screen.getByRole("button", { name: /Ponawianie… \(dotychczasowe dane pozostają\)/ })).toBeDisabled();
    // Kontrolki działają na zachowanych danych.
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeEnabled();
  });

  it("niepełne dane importu to partial bez przycisku ponowienia (ponowienie nic nie zmieni)", () => {
    renderPanel({
      layerStatus: status(
        available({
          coverage_areas: [
            {
              act_id: "A",
              teryt: "226401",
              legal_status: "binding",
              bounds: [18.5, 54.4, 18.6, 54.5],
              has_boundary: true,
              is_complete: false,
              incomplete_reasons: ["missing_area"],
            },
          ],
        }),
      ),
    });
    expect(screen.getByTestId("pog-layer-state")).toHaveTextContent("dane niepełne");
    expect(screen.getByText(/Akty z brakiem granicy albo luką/)).toHaveTextContent("A.");
    expect(screen.queryByRole("button", { name: /Ponów/ })).not.toBeInTheDocument();
  });

  it("wydanie z aktem o nieustalonym statusie ma własną plakietkę", () => {
    renderPanel({ layerStatus: status(available({ acts_by_legal_status: { unknown: 1, binding: 1 } })) });
    const badges = screen.getAllByTestId("pog-status-badge");
    expect(badges.map((badge) => badge.textContent?.trim())).toEqual(["status nieustalony"]);
    expect(badges[0]).toHaveAttribute("data-status", "unknown");
  });
});
