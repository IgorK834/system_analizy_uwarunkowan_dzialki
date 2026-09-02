import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type maplibregl from "maplibre-gl";

import { SearchPanel } from "@/components/SearchPanel";
import { ApiError, searchAddresses } from "@/lib/api";
import type { AddressSearchResult } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, searchAddresses: vi.fn() };
});

const searchAddressesMock = vi.mocked(searchAddresses);
const warsawResult: AddressSearchResult = {
  id: "hash:abc123def456",
  label: "Warszawa, Marszałkowska 1",
  match_ranges: [],
  point: { type: "Point", coordinates: [21.012, 52.23] },
  address_parts: {
    country: "Polska",
    voivodeship: null,
    county: null,
    municipality: null,
    city: "Warszawa",
    street: "Marszałkowska",
    house_number: "1",
  },
  result_type: "house_number",
  confidence: 0.9,
  source: { source_id: "emuia_uug", attribution: "GUGiK / EMUiA" },
};

describe("SearchPanel", () => {
  beforeEach(() => {
    searchAddressesMock.mockReset();
    searchAddressesMock.mockResolvedValue({
      query: "Warszawa",
      results: [warsawResult],
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
      expect(searchAddressesMock).toHaveBeenCalledWith(
        "Warszawa",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(await screen.findByRole("option", { name: /Marszałkowska 1/ })).toHaveTextContent(
      "Adres",
    );
    expect(onAnalyze).not.toHaveBeenCalled();

    await user.click(screen.getByRole("option", { name: /Marszałkowska 1/ }));
    expect(onAnalyze).toHaveBeenCalledWith({
      method: "address",
      query: warsawResult.label,
      selected_lon: 21.012,
      selected_lat: 52.23,
      selected_result_id: warsawResult.id,
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
      query: warsawResult.label,
      selected_lon: 21.012,
      selected_lat: 52.23,
      selected_result_id: warsawResult.id,
    });
  });

  it("nie odpytuje API dla zbyt krótkiego adresu i pokazuje błąd sugestii", async () => {
    const user = userEvent.setup();
    render(<SearchPanel loading={false} onAnalyze={vi.fn()} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    const input = screen.getByLabelText("Adres");
    await user.type(input, "Wa");
    await new Promise((resolve) => window.setTimeout(resolve, 330));
    expect(searchAddressesMock).not.toHaveBeenCalled();

    searchAddressesMock.mockRejectedValueOnce(
      new ApiError(503, "Geokodowanie jest niedostępne"),
    );
    await user.type(input, "r");
    expect(await screen.findByText("Geokodowanie jest niedostępne")).toBeVisible();
  });

  it("pokazuje pomocny komunikat, gdy źródło nie zwróci sugestii", async () => {
    searchAddressesMock.mockResolvedValueOnce({
      query: "Odysei 51",
      results: [],
      total_returned: 0,
    });
    const user = userEvent.setup();
    render(<SearchPanel loading={false} onAnalyze={vi.fn()} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    await user.type(screen.getByLabelText("Adres"), "Odysei 51");

    expect(
      await screen.findByText(/Brak podpowiedzi/),
    ).toHaveTextContent("zawęzić wyszukiwanie");
  });

  it("po wyborze miejscowości pozwala dopisać ulicę bez uruchamiania analizy", async () => {
    const cityResult: AddressSearchResult = {
      ...warsawResult,
      id: "prg_address_dictionary:city-warszawa",
      label: "Warszawa",
      address_parts: {
        ...warsawResult.address_parts,
        street: null,
        house_number: null,
      },
      result_type: "city",
    };
    searchAddressesMock
      .mockResolvedValueOnce({
        query: "Wars",
        results: [cityResult],
        total_returned: 1,
      })
      .mockResolvedValue({
        query: "Warszawa, Marsz",
        results: [warsawResult],
        total_returned: 1,
      });
    const user = userEvent.setup();
    const onAnalyze = vi.fn();
    render(<SearchPanel loading={false} onAnalyze={onAnalyze} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    const input = screen.getByLabelText("Adres");
    await user.type(input, "Wars");
    await user.click(await screen.findByRole("option", { name: /Warszawa/ }));

    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toHaveValue("Warszawa, ");
    expect(onAnalyze).not.toHaveBeenCalled();

    await user.type(input, "Marsz");
    await waitFor(() =>
      expect(searchAddressesMock).toHaveBeenLastCalledWith(
        "Warszawa, Marsz",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(onAnalyze).not.toHaveBeenCalled();
  });

  it("przekazuje środek i bbox bieżącego widoku mapy do rankingu", async () => {
    const map = {
      getCenter: () => ({ lng: 21.01, lat: 52.23 }),
      getBounds: () => ({
        getWest: () => 20.9,
        getSouth: () => 52.1,
        getEast: () => 21.2,
        getNorth: () => 52.4,
      }),
    } as unknown as maplibregl.Map;
    const user = userEvent.setup();
    render(<SearchPanel loading={false} onAnalyze={vi.fn()} map={map} />);

    await user.click(screen.getByRole("tab", { name: "Adres" }));
    await user.type(screen.getByLabelText("Adres"), "Warszawa");

    await waitFor(() =>
      expect(searchAddressesMock).toHaveBeenCalledWith(
        "Warszawa",
        expect.objectContaining({
          bias: { lon: 21.01, lat: 52.23 },
          bbox: [20.9, 52.1, 21.2, 52.4],
        }),
      ),
    );
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
