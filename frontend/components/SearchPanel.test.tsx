import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPanel } from "@/components/SearchPanel";
import { ApiError, getAddressSuggestions } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getAddressSuggestions: vi.fn() };
});

const getAddressSuggestionsMock = vi.mocked(getAddressSuggestions);
const warsawSuggestion = {
  label: "Warszawa, Marszałkowska 1",
  x: 637421.5,
  y: 486921.2,
  confidence: 0.9,
  teryt: "146501",
};

describe("SearchPanel", () => {
  beforeEach(() => {
    getAddressSuggestionsMock.mockReset();
    getAddressSuggestionsMock.mockResolvedValue({
      query: "Warszawa",
      suggestions: [warsawSuggestion],
      total_returned: 1,
    });
  });

  it("domyślnie pokazuje instrukcję wyboru punktu z mapy", () => {
    render(<SearchPanel loading={false} onAnalyze={vi.fn()} />);

    expect(screen.getByRole("tab", { name: "Mapa" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText(/Kliknij wybrane miejsce na mapie/)).toBeVisible();
  });

  it("pobiera sugestie po debounce, ale nie analizuje podczas wpisywania", async () => {
    const user = userEvent.setup();
    const onAnalyze = vi.fn();
    render(<SearchPanel loading={false} onAnalyze={onAnalyze} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    await user.type(screen.getByLabelText("Adres"), "Warszawa");

    expect(onAnalyze).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(getAddressSuggestionsMock).toHaveBeenCalledWith(
        "Warszawa",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(await screen.findByRole("option", { name: /Marszałkowska 1/ })).toHaveTextContent(
      "EPSG:2180",
    );
    expect(onAnalyze).not.toHaveBeenCalled();

    await user.click(screen.getByRole("option", { name: /Marszałkowska 1/ }));
    expect(onAnalyze).toHaveBeenCalledWith({
      method: "address",
      query: warsawSuggestion.label,
    });
    expect(onAnalyze).toHaveBeenCalledOnce();
  });

  it("pozwala wybrać sugestię strzałką i Enterem", async () => {
    const user = userEvent.setup();
    const onAnalyze = vi.fn();
    render(<SearchPanel loading={false} onAnalyze={onAnalyze} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    const input = screen.getByLabelText("Adres");
    await user.type(input, "Warszawa");
    await screen.findByRole("option", { name: /Marszałkowska 1/ });
    await user.type(input, "{ArrowDown}{Enter}");

    expect(onAnalyze).toHaveBeenCalledWith({
      method: "address",
      query: warsawSuggestion.label,
    });
  });

  it("nie odpytuje API dla zbyt krótkiego adresu i pokazuje błąd sugestii", async () => {
    const user = userEvent.setup();
    render(<SearchPanel loading={false} onAnalyze={vi.fn()} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    const input = screen.getByLabelText("Adres");
    await user.type(input, "Wa");
    await new Promise((resolve) => window.setTimeout(resolve, 330));
    expect(getAddressSuggestionsMock).not.toHaveBeenCalled();

    getAddressSuggestionsMock.mockRejectedValueOnce(
      new ApiError(503, "Geokodowanie jest niedostępne"),
    );
    await user.type(input, "r");
    expect(await screen.findByText("Geokodowanie jest niedostępne")).toBeVisible();
  });

  it("waliduje identyfikator i wysyła poprawny kontrakt", async () => {
    const user = userEvent.setup();
    const onAnalyze = vi.fn();
    render(<SearchPanel loading={false} onAnalyze={onAnalyze} />);

    await user.click(
      screen.getByRole("tab", { name: "Identyfikator działki" }),
    );
    const input = screen.getByLabelText("Identyfikator działki");
    await user.type(input, "12AB");
    await user.click(screen.getByRole("button", { name: "Analizuj działkę" }));
    expect(screen.getByText(/Dozwolone są cyfry/)).toBeVisible();
    expect(onAnalyze).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, "122101_1.0001.1234/2");
    await user.click(screen.getByRole("button", { name: "Analizuj działkę" }));
    expect(onAnalyze).toHaveBeenCalledWith({
      method: "parcel_id",
      parcel_identifier: "122101_1.0001.1234/2",
    });
  });

  it("blokuje formularze podczas analizy", async () => {
    const user = userEvent.setup();
    render(<SearchPanel loading onAnalyze={vi.fn()} />);
    await user.click(
      screen.getByRole("tab", { name: "Identyfikator działki" }),
    );

    expect(screen.getByLabelText("Identyfikator działki")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Analizuję…" })).toBeDisabled();
  });
});
