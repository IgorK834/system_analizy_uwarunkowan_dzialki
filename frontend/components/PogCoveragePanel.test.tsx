import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PogCoveragePanel } from "@/components/PogCoveragePanel";
import { getPogCommuneState, getPogCoverage } from "@/lib/api";
import type { PogCommuneCoverage } from "@/lib/types";
import { buildCommuneCoverage, buildPogCoverage } from "@/test/pogFixtures";

vi.mock("@/lib/api", () => ({ getPogCoverage: vi.fn(), getPogCommuneState: vi.fn() }));

const coverageMock = vi.mocked(getPogCoverage);
const communeMock = vi.mocked(getPogCommuneState);
const SUMMARY = "Pokrycie importu planów ogólnych (gminy)";

async function openPanel() {
  const user = userEvent.setup();
  render(<PogCoveragePanel />);
  await user.click(screen.getByText(SUMMARY));
  return user;
}

describe("PogCoveragePanel", () => {
  beforeEach(() => {
    coverageMock.mockReset().mockResolvedValue(buildPogCoverage());
    communeMock.mockReset();
  });

  it("nie pobiera listy do czasu rozwinięcia panelu", () => {
    render(<PogCoveragePanel />);
    expect(coverageMock).not.toHaveBeenCalled();
  });

  it("po rozwinięciu pokazuje podsumowanie, świeżość wykazu RU i gminy ze stanem oraz wersją", async () => {
    await openPanel();
    expect(await screen.findByTestId("pog-coverage-summary")).toHaveTextContent(
      "zaimportowane: 1; czekają na import: 2",
    );
    expect(screen.getByTestId("pog-coverage-freshness")).toHaveTextContent("Wykaz RU zsynchronizowany 13.10.2026");
    const items = screen.getAllByTestId("pog-coverage-item");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Sopot");
    expect(items[0]).toHaveTextContent("Zaimportowana");
    expect(items[0]).toHaveTextContent("wersja 20260819T010000, stan #42, import 28.09.2026");
    expect(items[1]).toHaveTextContent("Plan w RU — jeszcze niezaimportowany");
    expect(items[1]).toHaveTextContent("zlecenie importu w kolejce");
    expect(coverageMock).toHaveBeenCalledOnce();
  });

  it("filtruje listę po zakresie i wyszukiwarce, a puste wyniki nie są informacją o braku planu", async () => {
    const user = await openPanel();
    await screen.findByTestId("pog-coverage-list");

    await user.click(screen.getByRole("radio", { name: "Czekają na import" }));
    expect(screen.getAllByTestId("pog-coverage-item").map((item) => item.dataset.status)).toEqual([
      "in_ru_not_imported",
      "in_ru_not_imported",
    ]);
    await user.type(screen.getByLabelText("Szukaj gminy, powiatu lub kodu TERC"), "bielsko");
    expect(screen.getAllByTestId("pog-coverage-item")).toHaveLength(1);
    expect(screen.getByTestId("pog-coverage-item")).toHaveTextContent("Bielsko-Biała");

    await user.clear(screen.getByLabelText("Szukaj gminy, powiatu lub kodu TERC"));
    await user.type(screen.getByLabelText("Szukaj gminy, powiatu lub kodu TERC"), "xyz");
    const empty = screen.getByTestId("pog-coverage-empty");
    expect(empty).toHaveTextContent("To nie jest informacja o braku planu");
    expect(empty).toHaveTextContent("kodzie TERC");
  });

  it("dłuższa lista jest stronicowana przyciskiem „Pokaż więcej”", async () => {
    const many: PogCommuneCoverage[] = Array.from({ length: 30 }, (_, index) =>
      buildCommuneCoverage({ terc: String(100000 + index), name: `Gmina ${index}` }),
    );
    coverageMock.mockResolvedValue(buildPogCoverage({ communes: many }));
    const user = await openPanel();
    expect(await screen.findAllByTestId("pog-coverage-item")).toHaveLength(25);
    await user.click(screen.getByRole("button", { name: "Pokaż więcej (5)" }));
    expect(screen.getAllByTestId("pog-coverage-item")).toHaveLength(30);
    expect(screen.queryByRole("button", { name: /Pokaż więcej/ })).not.toBeInTheDocument();
  });

  it("błąd pobrania ma własny komunikat (stan nieustalony) i ponowienie", async () => {
    coverageMock.mockRejectedValueOnce(new Error("503"));
    const user = await openPanel();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("stan gmin jest nieustalony (to nie oznacza braku planów)");
    await user.click(within(alert).getByRole("button", { name: "Ponów" }));
    expect(await screen.findByTestId("pog-coverage-summary")).toBeInTheDocument();
    expect(coverageMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["imported", /Zaimportowana\./],
    ["in_ru_not_imported", /Plan w RU — jeszcze niezaimportowany\./],
    ["no_act_in_ru", /Brak aktu POG w wykazie RU\..*nie dowodzi braku planu w gminie/],
    ["unknown", /Źródło niedostępne — stan nieustalony\./],
  ] as const)("sprawdzenie gminy po kodzie TERC: stan %s ma własny komunikat", async (status, message) => {
    communeMock.mockResolvedValue({ terc: "246101", status, catalog_synced_at: null, detail: "x" });
    const user = await openPanel();
    await screen.findByTestId("pog-coverage-list");
    await user.type(screen.getByLabelText("Sprawdź gminę po kodzie TERC (6 cyfr)"), "246101");
    await user.click(screen.getByRole("button", { name: "Sprawdź" }));
    await waitFor(() => expect(screen.getByTestId("pog-coverage-lookup")).toHaveTextContent(message));
    expect(communeMock).toHaveBeenCalledWith("246101");
  });

  it("niepoprawny kod TERC nie wysyła żądania, a błąd żądania nie udaje żadnego stanu gminy", async () => {
    const user = await openPanel();
    await screen.findByTestId("pog-coverage-list");
    const input = screen.getByLabelText("Sprawdź gminę po kodzie TERC (6 cyfr)");
    await user.type(input, "24");
    await user.click(screen.getByRole("button", { name: "Sprawdź" }));
    expect(communeMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("pog-coverage-lookup")).toHaveTextContent("stan gminy jest nieustalony");

    communeMock.mockRejectedValueOnce(new Error("503"));
    await user.clear(input);
    await user.type(input, "246101");
    await user.click(screen.getByRole("button", { name: "Sprawdź" }));
    await waitFor(() => expect(screen.getByTestId("pog-coverage-lookup")).toHaveTextContent("stan gminy jest nieustalony"));
    expect(screen.getByTestId("pog-coverage-lookup").textContent).not.toMatch(/Zaimportowana|Brak aktu/);
  });
});
