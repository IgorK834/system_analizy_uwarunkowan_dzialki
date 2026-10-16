import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  POG_MAP_PREFERENCES_KEY,
  usePogMapPreferences,
  usePogTileState,
} from "@/hooks/usePogTileState";
import { getCurrentPogTileState } from "@/lib/api";
import { buildPogState } from "@/test/pogFixtures";

vi.mock("@/lib/api", () => ({ getCurrentPogTileState: vi.fn() }));

const getStateMock = vi.mocked(getCurrentPogTileState);

describe("usePogTileState", () => {
  beforeEach(() => getStateMock.mockReset());

  it("pobiera stan krajowy raz i nie odświeża go w trakcie sesji", async () => {
    const state = buildPogState();
    getStateMock.mockResolvedValue(state);
    const { result, rerender } = renderHook(() => usePogTileState());
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current).toMatchObject({ status: "available", state }));
    expect(result.current.status === "available" && result.current.checkedAt).toBeTruthy();
    rerender();
    expect(getStateMock).toHaveBeenCalledOnce();
  });

  it("BK-406: ponowienie bez zmiany stanu zachowuje obiekt; nieudane daje stale z datą", async () => {
    const state = buildPogState();
    getStateMock.mockResolvedValueOnce(state);
    const { result } = renderHook(() => usePogTileState());
    await waitFor(() => expect(result.current.status).toBe("available"));
    const firstCheck = result.current.status === "available" ? result.current.checkedAt : null;

    // Ten sam stan (nowy obiekt JSON) — źródło mapy nie może się przeładować.
    getStateMock.mockResolvedValueOnce(buildPogState());
    act(() => result.current.retry());
    expect(result.current.retrying).toBe(true);
    // Podczas ponowienia dotychczasowe dane pozostają.
    expect(result.current.state).toBe(state);
    await waitFor(() => expect(result.current.retrying).toBe(false));
    expect(result.current.state).toBe(state);

    getStateMock.mockRejectedValueOnce(new Error("503"));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("stale"));
    expect(result.current).toMatchObject({ status: "stale", state, reason: "refresh_failed" });
    expect(result.current.status === "stale" && result.current.checkedAt >= (firstCheck ?? "")).toBe(true);

    // Jawne ponowienie może przełączyć na nowszy stan (zaimportowano kolejne gminy).
    const newer = buildPogState({ state_id: 43, act_count: 5 });
    getStateMock.mockResolvedValueOnce(newer);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.state).toBe(newer));
    expect(result.current.status).toBe("available");

    getStateMock.mockResolvedValueOnce(null);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("no_data"));
    getStateMock.mockRejectedValueOnce(new Error("503"));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("error"));
  });

  it("odróżnia brak zaimportowanych gmin od błędu warstwy", async () => {
    getStateMock.mockResolvedValueOnce(null);
    const missing = renderHook(() => usePogTileState());
    await waitFor(() => expect(missing.result.current.status).toBe("no_data"));

    getStateMock.mockRejectedValueOnce(new Error("503"));
    const failed = renderHook(() => usePogTileState());
    await waitFor(() => expect(failed.result.current.status).toBe("error"));
  });

  it("ignoruje odpowiedź po odmontowaniu", async () => {
    let resolve: (value: null) => void = () => undefined;
    getStateMock.mockReturnValueOnce(new Promise((done) => (resolve = done)));
    const { result, unmount } = renderHook(() => usePogTileState());
    unmount();
    resolve(null);
    await Promise.resolve();
    expect(result.current.status).toBe("loading");
  });

  it("ignoruje błąd po odmontowaniu", async () => {
    let reject: (error: Error) => void = () => undefined;
    getStateMock.mockReturnValueOnce(new Promise((_, fail) => (reject = fail)));
    const { result, unmount } = renderHook(() => usePogTileState());
    unmount();
    reject(new Error("x"));
    await Promise.resolve();
    expect(result.current.status).toBe("loading");
  });
});

describe("usePogMapPreferences", () => {
  it("zapisuje i odtwarza tryb oraz filtr statusu", async () => {
    const first = renderHook(() => usePogMapPreferences());
    act(() => first.result.current.setTheme("height"));
    act(() => first.result.current.setStatusFilter("non_binding"));
    expect(JSON.parse(window.localStorage.getItem(POG_MAP_PREFERENCES_KEY) ?? "{}")).toEqual({
      theme: "height",
      statusFilter: "non_binding",
    });
    first.unmount();

    const second = renderHook(() => usePogMapPreferences());
    await waitFor(() => expect(second.result.current.theme).toBe("height"));
    expect(second.result.current.statusFilter).toBe("non_binding");
  });

  it("odrzuca nieznane i uszkodzone wartości", async () => {
    window.localStorage.setItem(
      POG_MAP_PREFERENCES_KEY,
      JSON.stringify({ theme: "area", statusFilter: "draft" }),
    );
    const invalid = renderHook(() => usePogMapPreferences());
    await waitFor(() => expect(invalid.result.current.theme).toBe("zones"));
    expect(invalid.result.current.statusFilter).toBe("all");

    window.localStorage.setItem(POG_MAP_PREFERENCES_KEY, "{uszkodzony");
    const broken = renderHook(() => usePogMapPreferences());
    await waitFor(() => expect(broken.result.current.theme).toBe("zones"));
  });

  it("działa w pamięci, gdy localStorage odmawia zapisu", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    const { result } = renderHook(() => usePogMapPreferences());
    act(() => result.current.setTheme("intensity"));
    expect(result.current.theme).toBe("intensity");
    setItem.mockRestore();
  });
});
