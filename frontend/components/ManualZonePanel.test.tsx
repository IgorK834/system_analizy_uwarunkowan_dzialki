import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ManualZonePanel } from "@/components/ManualZonePanel";
import { getPreviewSources } from "@/lib/api";
import {
  MPZP_PREVIEW_SOURCE,
  PINNED_SHA,
  manualZoneContext,
  waitingResponse,
} from "@/test/manualZoneFixtures";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getPreviewSources: vi.fn() };
});

const getPreviewSourcesMock = vi.mocked(getPreviewSources);

function renderPanel(overrides: Parameters<typeof waitingResponse>[0] = {}, onSubmit = vi.fn().mockResolvedValue(true)) {
  render(
    <ManualZonePanel result={waitingResponse(overrides)} loading={false} error={null} onSubmit={onSubmit} />,
  );
  return onSubmit;
}

describe("ManualZonePanel (BK-204)", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    getPreviewSourcesMock.mockReset();
    getPreviewSourcesMock.mockResolvedValue([MPZP_PREVIEW_SOURCE]);
  });
  afterEach(() => vi.unstubAllEnvs());

  it("pokazuje obraz źródłowy, plan, dokument i kandydatów przed formularzem", async () => {
    renderPanel();
    const form = screen.getByRole("form", { name: "Ręczne podanie symbolu strefy MPZP" });

    expect(await within(form).findAllByTestId("mpzp-raster-tile")).toHaveLength(9);
    expect(screen.getByTestId("manual-zone-plan-id")).toHaveTextContent("MPZP/2020/1");
    const link = screen.getByRole("link", { name: /Otwórz przypiętą kopię/ });
    expect(link).toHaveAttribute("href", "https://api.example.test/analyze/77/pending-document");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText(/SHA-256 bbbbbbbbbbbb…/)).toHaveAttribute("title", PINNED_SHA);
    expect(screen.getByRole("link", { name: "https://bip.krakow.pl/uchwala.pdf" })).toBeVisible();
    expect(screen.getByRole("button", { name: "230_U" })).toBeVisible();
    expect(screen.getByRole("button", { name: "231_MN" })).toBeVisible();

    const order = [
      screen.getByRole("region", { name: "Obraz źródłowy planu" }),
      screen.getByRole("region", { name: "Plan i dokument źródłowy" }),
      screen.getByRole("region", { name: "Kandydaci symboli" }),
      screen.getByLabelText("Symbol strefy"),
    ];
    for (let index = 1; index < order.length; index += 1) {
      expect(
        order[index - 1].compareDocumentPosition(order[index]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(screen.getByText(/nieustalony/)).toBeVisible();
  });

  it("wysyła symbol dopiero po potwierdzeniu porównania i poprawnej walidacji", async () => {
    const user = userEvent.setup();
    const onSubmit = renderPanel();
    const submit = screen.getByRole("button", { name: "Wznów analizę" });

    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "231_MN" }));
    expect(screen.getByLabelText("Symbol strefy")).toHaveValue("231_MN");
    expect(screen.getByRole("button", { name: "231_MN" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("checkbox"));
    expect(submit).toBeEnabled();
    await user.click(submit);

    expect(onSubmit).toHaveBeenCalledWith("231_MN");
    await waitFor(() => expect(screen.getByLabelText("Symbol strefy")).toHaveValue(""));
  });

  it("blokuje symbol z niedozwolonym znakiem tą samą regułą co API", async () => {
    const user = userEvent.setup();
    const onSubmit = renderPanel();

    await user.type(screen.getByLabelText("Symbol strefy"), "230 U");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Wznów analizę" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/niedozwolone znaki/)).toBeVisible();
    expect(screen.getByLabelText("Symbol strefy")).toHaveAttribute("aria-invalid", "true");
  });

  it("ostrzega o symbolu spoza kandydatów, ale go nie blokuje", async () => {
    const user = userEvent.setup();
    const onSubmit = renderPanel();

    await user.type(screen.getByLabelText("Symbol strefy"), "999_X");
    expect(screen.getByRole("note")).toHaveTextContent("spoza kandydatów");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Wznów analizę" }));
    expect(onSubmit).toHaveBeenCalledWith("999_X");
  });

  it("wymaga potwierdzenia także przy wysłaniu formularza klawiaturą", async () => {
    const onSubmit = renderPanel();
    const form = screen.getByRole("form", { name: "Ręczne podanie symbolu strefy MPZP" });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Symbol strefy"), "230_U");
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(await screen.findByText(/Potwierdź porównanie symbolu/)).toBeVisible();
  });

  it("zachowuje wpis po odrzuceniu przez API i pokazuje błąd", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(false);
    render(
      <ManualZonePanel
        result={waitingResponse()}
        loading={false}
        error="Nie udało się odczytać dokumentu MPZP przypiętego przy wstrzymaniu analizy."
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Symbol strefy"), "230_U");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Wznów analizę" }));

    expect(screen.getByLabelText("Symbol strefy")).toHaveValue("230_U");
    expect(screen.getByText(/przypiętego przy wstrzymaniu/)).toBeVisible();
  });

  it.each([
    ["unavailable", "nie udało się pobrać"],
    ["not_provided", "nie wskazało dokumentu"],
  ] as const)("opisuje brak przypiętego dokumentu (%s)", (status, text) => {
    renderPanel({
      manual_zone_context: manualZoneContext({ document_status: status, document: null, candidate_zone_symbols: [] }),
    });

    expect(screen.getByText(new RegExp(text, "i"))).toBeVisible();
    expect(screen.queryByRole("link", { name: /przypiętą kopię/ })).toBeNull();
    expect(screen.getByText("Discovery nie wskazało kandydatów symboli dla tego planu.")).toBeVisible();
  });

  it("niezweryfikowany adres źródłowy nie jest klikalny", () => {
    const context = manualZoneContext();
    renderPanel({
      manual_zone_context: {
        ...context,
        document: { ...context.document!, requested_url: "javascript:alert(1)", requested_url_verified: false },
      },
    });

    expect(screen.getByText("javascript:alert(1)").closest("a")).toBeNull();
  });

  it("działa bez kontekstu (starsze API) i blokuje brak analysis_id", async () => {
    const user = userEvent.setup();
    const onSubmit = renderPanel({ manual_zone_context: null, analysis_id: null });

    expect(screen.getByText(/Odczytaj symbol strefy z podglądu rastrowego/)).toBeVisible();
    expect(screen.getByLabelText("Symbol strefy")).toBeDisabled();
    const form = screen.getByRole("form", { name: "Ręczne podanie symbolu strefy MPZP" });
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(await screen.findByText(/Brak identyfikatora analizy/)).toBeVisible();
    expect(onSubmit).not.toHaveBeenCalled();
    void user;
  });

  it("gdy konfiguracja podglądów się nie wczyta, jawnie informuje o braku obrazu", async () => {
    getPreviewSourcesMock.mockRejectedValue(new Error("offline"));
    renderPanel();

    expect(await screen.findByText(/Podgląd rastrowy MPZP jest chwilowo niedostępny/)).toBeVisible();
  });
});
