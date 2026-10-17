import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { Map } from "maplibre-gl";
import { CoverageLayer, ImportRequestButton } from "@/components/CoverageLayer";
import { getCoverage, requestPogImport } from "@/lib/api";
import type { CoverageResponse } from "@/lib/types";

vi.mock("@/lib/api", () => ({ getCoverage: vi.fn(), requestPogImport: vi.fn() }));
const fixture = (counties: CoverageResponse["bdot10k"]["counties"] = []): CoverageResponse => ({
  pog: {} as CoverageResponse["pog"], communes: { type: "FeatureCollection", features: [] },
  bdot10k: { counties }, boundaries_note: "PRG — geometria tylko do podglądu",
});
function fakeMap(loaded = true) {
  const layers = new Set<string>(), sources = new Set<string>();
  const map = { isStyleLoaded: vi.fn(() => loaded), getSource: vi.fn((s: string) => sources.has(s)),
    addSource: vi.fn((s: string) => sources.add(s)), removeSource: vi.fn((s: string) => sources.delete(s)),
    getLayer: vi.fn((s: string) => layers.has(s)), addLayer: vi.fn(({ id }: { id: string }) => layers.add(id)),
    removeLayer: vi.fn((s: string) => layers.delete(s)), on: vi.fn((_event: string, _handler: () => void) => {}), off: vi.fn() };
  return map;
}
beforeEach(() => vi.resetAllMocks());

describe("CoverageLayer", () => {
  it("pobiera dopiero po włączeniu, pokazuje legendę i usuwa źródło po wyłączeniu", async () => {
    vi.mocked(getCoverage).mockResolvedValue(fixture());
    const map = fakeMap(); const user = userEvent.setup();
    render(<CoverageLayer map={map as unknown as Map} />);
    expect(getCoverage).not.toHaveBeenCalled();
    await user.click(screen.getByLabelText("Pokrycie"));
    await screen.findByText(/PRG — geometria/);
    expect(map.addSource).toHaveBeenCalledOnce(); expect(map.addLayer).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Brak lokalnych danych (≠ brak planu)")).toBeInTheDocument();
    await user.click(screen.getByText("Pokrycie BDOT10k (powiaty)"));
    expect(screen.getByText(/Brak lokalnych wydań BDOT10k/)).toBeVisible();
    act(() => map.on.mock.calls[0][1]()); // zmiana stylu nie dubluje warstw
    expect(map.addSource).toHaveBeenCalledOnce();
    await user.click(screen.getByLabelText("Pokrycie"));
    expect(map.removeLayer).toHaveBeenCalledTimes(2); expect(map.removeSource).toHaveBeenCalledOnce();
  });
  it("czeka na styl i wyświetla wydania powiatów z datami lub jawnym brakiem daty", async () => {
    vi.mocked(getCoverage).mockResolvedValue(fixture([
      { teryt_powiat: "1418", name: "piaseczyński", version_label: "v1", data_as_of: "2026-01-01", imported_at: "2026-10-16T10:00:00Z" },
      { teryt_powiat: "1261", name: null, version_label: "v2", data_as_of: null, imported_at: "2026-10-16T10:00:00Z" },
    ]));
    const map = fakeMap(false); const user = userEvent.setup();
    const { unmount } = render(<CoverageLayer map={map as unknown as Map} />);
    await user.click(screen.getByLabelText("Pokrycie"));
    await screen.findByText(/PRG — geometria/);
    expect(map.addLayer).not.toHaveBeenCalled();
    act(() => map.on.mock.calls[0][1]());
    expect(map.addLayer).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/piaseczyński: v1, stan na 2026-01-01/)).toBeInTheDocument();
    expect(screen.getByText(/1261: v2, stan na nieznany/)).toBeInTheDocument();
    // Styl może usunąć źródła przed cleanup.
    map.removeLayer("data-coverage-line"); map.removeLayer("data-coverage-fill"); map.removeSource("data-coverage");
    unmount(); expect(map.off).toHaveBeenCalled();
  });
  it("po błędzie pozwala ponowić, a metadane są dostępne także bez instancji mapy", async () => {
    vi.mocked(getCoverage).mockRejectedValueOnce(new Error("offline")).mockResolvedValue(fixture());
    const user = userEvent.setup(); render(<CoverageLayer map={null} />);
    await user.click(screen.getByLabelText("Pokrycie"));
    expect(await screen.findByRole("alert")).toHaveTextContent("stan nieustalony");
    await user.click(screen.getByRole("button", { name: "Ponów" }));
    await screen.findByText(/PRG — geometria/);
  });
  it("anuluje spóźnioną odpowiedź i porażkę po wyłączeniu", async () => {
    let resolve!: (value: CoverageResponse) => void;
    vi.mocked(getCoverage).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    const user = userEvent.setup(); render(<CoverageLayer map={null} />);
    await user.click(screen.getByLabelText("Pokrycie"));
    expect(screen.getByRole("status")).toHaveTextContent("Wczytywanie");
    await user.click(screen.getByLabelText("Pokrycie"));
    await act(async () => resolve(fixture()));
    expect(screen.queryByText(/PRG — geometria/)).not.toBeInTheDocument();
    let reject!: (err: Error) => void;
    vi.mocked(getCoverage).mockImplementationOnce(() => new Promise((_, r) => { reject = r; }));
    await user.click(screen.getByLabelText("Pokrycie")); await user.click(screen.getByLabelText("Pokrycie"));
    await act(async () => reject(new Error("aborted")));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ImportRequestButton", () => {
  it("nie zleca bez kodu gminy", () => { const { container } = render(<ImportRequestButton terc="bad" />); expect(container).toBeEmptyDOMElement(); });
  it("zleca import tylko po kliknięciu i blokuje duplikat", async () => {
    vi.mocked(requestPogImport).mockResolvedValue({ status: "queued", detail: "Import w kolejce #42" });
    const user = userEvent.setup(); render(<ImportRequestButton terc="246101" />);
    await user.click(screen.getByRole("button", { name: "poproś o import" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Import w kolejce #42");
    expect(requestPogImport).toHaveBeenCalledWith("246101");
    expect(screen.getByRole("button")).toBeDisabled();
  });
  it("nie przedstawia poprzedniego nieudanego importu jako nowego przyjętego zlecenia", async () => {
    vi.mocked(requestPogImport).mockResolvedValue({ status: "failed", detail: "Ostatni import nie powiódł się; kolejna próba automatycznie." });
    const user = userEvent.setup(); render(<ImportRequestButton terc="246101" />);
    await user.click(screen.getByRole("button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("nie powiódł się");
    expect(screen.queryByText("Zlecenie przyjęte")).not.toBeInTheDocument();
  });
  it("po błędzie umożliwia ponowienie bez fałszywego sukcesu", async () => {
    vi.mocked(requestPogImport).mockRejectedValue(new Error("offline"));
    const user = userEvent.setup(); render(<ImportRequestButton terc="246101" />);
    await user.click(screen.getByRole("button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie zlecono importu");
    await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
  });
});
